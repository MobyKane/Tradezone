import hashlib
import hmac
import json
import re
from decimal import Decimal

from django.core import mail
from django.core.cache import cache
from django.core.exceptions import ValidationError
from io import StringIO

from django.core.management import call_command
from django.test import Client, TestCase, override_settings
from django.urls import reverse


from django.utils import timezone
from django.contrib.auth import get_user_model
from unittest.mock import patch
from products.models import (
    CategoryCommission,
    CategoryNode,
    CartItem,
    Complaint,
    LedgerTransaction,
    Order,
    OrderItem,
    Payment,
    PaymentWebhookEvent,
    PlatformPaymentSettings,
    Product,
    ProductReport,
    VendorDispute,
    VendorOrder,
    VendorWallet,
    Payout,
)
from vendors.models import AccountProfile, Vendor
from products.money import (
    commission_rate_bps,
    fail_payout,
    mark_vendor_order_delivered,
    record_successful_payment,
    release_matured_vendor_orders,
    request_payout,
    snapshot_order_item,
)
from products.forms import ProductListingForm
from products.fashion_classifier import classify_fashion_section


class NavigationAndAuthTests(TestCase):
    def test_cart_nav_link_has_active_style_on_cart_page(self):
        response = self.client.get(reverse('cart'))
        self.assertEqual(response.status_code, 200)

        content = response.content.decode()
        cart_nav = re.search(
            r'<a[^>]*class="[^"]*bg-primary-container[^"]*"[^>]*href="/cart/"[^>]*>\s*<span[^>]*>shopping_cart</span>\s*<span[^>]*>Cart</span>',
            content,
        )
        self.assertIsNotNone(cart_nav, 'Footer cart nav item should get the active styling on the cart page.')

    def test_listing_form_uses_subcategory_label(self):
        response = self.client.get(reverse('list_item'))
        self.assertEqual(response.status_code, 200)

        content = response.content.decode()
        self.assertIn('Subcategory', content)

    def test_subcategory_page_has_category_specific_add_product_link(self):
        response = self.client.get(reverse('subcategory', kwargs={'category_slug': 'electronics'}))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Electronics')
        self.assertContains(response, '/list/?category=Electronics')

    def test_profile_redirects_to_app_login_not_admin(self):
        response = self.client.get(reverse('profile'))
        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, reverse('login') + '?next=' + reverse('profile'))

    def test_buyer_signup_is_short_and_starts_tour_without_javascript(self):
        response = self.client.get(reverse('signup'))
        self.assertContains(response, 'name="name"')
        self.assertContains(response, 'name="email"')
        self.assertContains(response, 'name="password"')
        self.assertNotContains(response, 'name="role"')

        response = self.client.post(reverse('signup'), {
            'name': 'New Buyer',
            'email': 'newbuyer@example.test',
            'password': 'Market!Ready2026',
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], reverse('home'))
        user = get_user_model().objects.get(email='newbuyer@example.test')
        self.assertEqual(user.account_profile.role, AccountProfile.BUYER)
        self.assertIsNone(user.account_profile.onboarding_completed_at)
        self.assertTrue(self.client.session['buyer_tour_autostart'])
        home_response = self.client.get(reverse('home'))
        self.assertContains(home_response, 'data-autostart="true"')
        user.account_profile.refresh_from_db()
        self.assertIsNotNone(user.account_profile.onboarding_completed_at)

    def test_buyer_first_home_render_sets_completion_and_shows_tour(self):
        user = get_user_model().objects.create_user(username='first_render_buyer', password='Test123!')
        profile = AccountProfile.objects.create(user=user, role=AccountProfile.BUYER)
        self.client.force_login(user)
        session = self.client.session
        session['buyer_tour_autostart'] = True
        session.save()

        response = self.client.get(reverse('home'))

        self.assertEqual(response.status_code, 200)
        profile.refresh_from_db()
        self.assertIsNotNone(profile.onboarding_completed_at)
        self.assertContains(response, 'id="buyer-tour-prompt"')
        self.assertContains(response, 'id="buyer-tour"')
        self.assertContains(response, 'data-autostart="true"')

    def test_buyer_refresh_hides_all_onboarding_content_after_first_render(self):
        user = get_user_model().objects.create_user(username='refresh_buyer', password='Test123!')
        AccountProfile.objects.create(user=user, role=AccountProfile.BUYER)
        self.client.force_login(user)
        self.client.get(reverse('home'))

        response = self.client.get(reverse('home'))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'Quick onboarding')
        self.assertNotContains(response, 'Take a quick tour')
        self.assertNotContains(response, 'id="buyer-tour"')
        self.assertNotContains(response, 'buyer-tour.js')

    def test_buyer_login_after_logout_or_new_device_does_not_repeat_onboarding(self):
        user = get_user_model().objects.create_user(
            username='relogin_buyer',
            password='Test123!',
        )
        profile = AccountProfile.objects.create(user=user, role=AccountProfile.BUYER)
        self.client.force_login(user)
        self.client.get(reverse('home'))
        profile.refresh_from_db()
        completed_at = profile.onboarding_completed_at

        self.client.logout()
        response = self.client.post(reverse('login'), {
            'username': user.username,
            'password': 'Test123!',
            'role': AccountProfile.BUYER,
        })
        self.assertRedirects(response, reverse('home'))
        response = self.client.get(reverse('home'))

        profile.refresh_from_db()
        self.assertEqual(profile.onboarding_completed_at, completed_at)
        self.assertNotContains(response, 'Quick onboarding')
        self.assertNotContains(response, 'Take a quick tour')
        self.assertNotContains(response, 'id="buyer-tour"')

        new_device = Client()
        response = new_device.post(reverse('login'), {
            'username': user.username,
            'password': 'Test123!',
            'role': AccountProfile.BUYER,
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], reverse('home'))
        response = new_device.get(reverse('home'))
        self.assertNotContains(response, 'Quick onboarding')
        self.assertNotContains(response, 'Take a quick tour')
        self.assertNotContains(response, 'id="buyer-tour"')

    def test_existing_buyer_sees_no_onboarding_content(self):
        user = get_user_model().objects.create_user(username='existing_buyer', password='Test123!')
        AccountProfile.objects.create(
            user=user,
            role=AccountProfile.BUYER,
            onboarding_completed_at=timezone.now(),
        )
        self.client.force_login(user)

        response = self.client.get(reverse('home'))

        self.assertNotContains(response, 'Quick onboarding')
        self.assertNotContains(response, 'Take a quick tour')
        self.assertNotContains(response, 'id="buyer-tour"')
        self.assertNotContains(response, 'buyer-tour.js')

    def test_guest_tour_prompt_and_empty_home_render_without_gateway_calls(self):
        with patch('products.views.gateways.initialize_paystack_payment') as initialize, \
                patch('products.views.gateways.resolve_paystack_account') as resolve, \
                patch('products.views.gateways.create_paystack_recipient') as recipient:
            response = self.client.get(reverse('home'))
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, 'Take a quick tour')
            self.assertContains(response, 'No products found')
            self.assertContains(response, 'id="buyer-tour"')
            dismissed = self.client.post(reverse('buyer_tour_complete'))
            self.assertEqual(dismissed.status_code, 204)
            self.assertEqual(self.client.get(reverse('home')).context['buyer_tour_prompt'], False)
            initialize.assert_not_called()
            resolve.assert_not_called()
            recipient.assert_not_called()

    def test_completed_buyer_does_not_auto_start_and_has_no_replay_link(self):
        user = get_user_model().objects.create_user(username='tour_buyer', password='Test123!')
        profile = AccountProfile.objects.create(
            user=user,
            role=AccountProfile.BUYER,
            onboarding_completed_at=timezone.now(),
        )
        self.client.force_login(user)
        response = self.client.get(reverse('home'))
        self.assertNotContains(response, 'id="buyer-tour"')
        self.assertNotContains(response, 'id="buyer-tour-prompt"')
        self.assertNotContains(response, 'Quick onboarding')
        replay = self.client.get(reverse('home') + '?tour=1')
        self.assertRedirects(replay, reverse('home'))

    def test_tour_skip_endpoint_persists_completion_for_buyer(self):
        user = get_user_model().objects.create_user(username='tour_skip_buyer', password='Test123!')
        AccountProfile.objects.create(user=user, role=AccountProfile.BUYER)
        self.client.force_login(user)
        response = self.client.post(reverse('buyer_tour_complete'))
        self.assertEqual(response.status_code, 204)
        user.account_profile.refresh_from_db()
        completed_at = user.account_profile.onboarding_completed_at
        self.assertIsNotNone(completed_at)
        response = self.client.post(reverse('buyer_tour_complete'))
        user.account_profile.refresh_from_db()
        self.assertEqual(user.account_profile.onboarding_completed_at, completed_at)
        response = self.client.get(reverse('home'))
        self.assertNotContains(response, 'id="buyer-tour"')
        self.assertNotContains(response, 'id="buyer-tour-prompt"')

    @override_settings(
        EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
        COMPLAINTS_EMAIL='support@example.test',
        DEFAULT_FROM_EMAIL='noreply@example.test',
    )
    def test_buyer_can_report_order_issue_from_profile_history(self):
        buyer = get_user_model().objects.create_user(username='order_issue_buyer', password='Test123!')
        vendor_user = get_user_model().objects.create_user(username='order_issue_vendor', password='Test123!')
        vendor = Vendor.objects.create(user=vendor_user, business_name='Issue Vendor')
        order = Order.objects.create(
            user=buyer,
            full_name='Order Issue Buyer',
            email='buyer@example.test',
            address='1 Market Road',
            total_amount='30.00',
            total_kobo=3000,
            payment_status='paid',
        )
        vendor_order = VendorOrder.objects.create(order=order, vendor=vendor, subtotal_kobo=3000)
        self.client.force_login(buyer)
        response = self.client.post(reverse('report_order_issue', kwargs={'order_id': order.pk}), {
            'reason': 'The parcel arrived damaged.',
        })
        self.assertRedirects(response, reverse('profile'))
        issue = VendorDispute.objects.get(vendor_order=vendor_order)
        self.assertEqual(issue.opened_by, buyer)
        self.assertEqual(issue.reason, 'The parcel arrived damaged.')
        complaint = Complaint.objects.get(order_number=str(order.pk))
        self.assertEqual(complaint.category, Complaint.ORDER_ISSUE)
        self.assertEqual(complaint.email, order.email)
        self.assertEqual(len(mail.outbox), 2)
        vendor_order.refresh_from_db()
        self.assertTrue(vendor_order.has_open_dispute)


@override_settings(
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    COMPLAINTS_EMAIL='support@example.test',
    DEFAULT_FROM_EMAIL='noreply@example.test',
)
class ComplaintFlowTests(TestCase):
    def setUp(self):
        cache.clear()
        mail.outbox = []
        self.url = reverse('contact_support')
        self.payload = {
            'name': 'Amina Buyer',
            'email': 'amina@example.test',
            'order_number': 'TZ-123',
            'category': Complaint.PAYMENT,
            'message': 'My Paystack payment is not showing.',
            'website': '',
        }

    def test_valid_guest_complaint_is_saved_and_sends_both_emails(self):
        response = self.client.post(self.url, self.payload)

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'complaint_confirmation.html')
        complaint = Complaint.objects.get()
        self.assertEqual(complaint.name, self.payload['name'])
        self.assertEqual(complaint.email, self.payload['email'])
        self.assertEqual(complaint.order_number, self.payload['order_number'])
        self.assertEqual(complaint.category, Complaint.PAYMENT)
        self.assertEqual(complaint.status, Complaint.NEW)
        self.assertEqual(complaint.reference, response.context['complaint'].reference)
        self.assertTrue(complaint.reference.startswith('TZC-'))
        self.assertEqual(len(mail.outbox), 2)
        self.assertEqual(mail.outbox[0].to, ['support@example.test'])
        self.assertIn(complaint.reference, mail.outbox[0].subject)
        self.assertEqual(mail.outbox[1].to, [self.payload['email']])
        self.assertIn(complaint.reference, mail.outbox[1].body)

    def test_invalid_complaint_is_rejected_without_saving_or_email(self):
        response = self.client.post(self.url, {
            **self.payload,
            'email': 'not-an-email',
            'category': 'not-a-category',
            'message': '',
        })

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'contact_support.html')
        self.assertEqual(Complaint.objects.count(), 0)
        self.assertEqual(mail.outbox, [])
        self.assertContains(response, 'Enter a valid email address.')
        self.assertContains(response, 'Select a valid choice.')

    def test_honeypot_blocks_bots_without_saving_or_sending(self):
        response = self.client.post(self.url, {**self.payload, 'website': 'spam.example'})

        self.assertEqual(response.status_code, 400)
        self.assertEqual(Complaint.objects.count(), 0)
        self.assertEqual(mail.outbox, [])

    def test_complaint_rate_limit_is_per_ip(self):
        for _ in range(3):
            response = self.client.post(self.url, self.payload)
            self.assertEqual(response.status_code, 200)
            self.assertTemplateUsed(response, 'complaint_confirmation.html')

        response = self.client.post(self.url, self.payload)

        self.assertEqual(response.status_code, 429)
        other_ip_client = Client()
        other_ip_response = other_ip_client.post(
            self.url,
            self.payload,
            REMOTE_ADDR='203.0.113.11',
        )
        self.assertEqual(other_ip_response.status_code, 200)
        self.assertEqual(Complaint.objects.count(), 4)
        self.assertEqual(len(mail.outbox), 8)

    def test_staff_can_review_and_update_complaint_status_in_admin(self):
        staff = get_user_model().objects.create_superuser(
            username='complaint_admin',
            email='admin@example.test',
            password='Test123!',
        )
        complaint = Complaint.objects.create(
            name='Amina Buyer',
            email='amina@example.test',
            category=Complaint.OTHER,
            message='Please help.',
        )
        self.client.force_login(staff)
        url = reverse('admin:products_complaint_change', args=(complaint.pk,))

        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="status"')
        response = self.client.post(url, {
            'name': complaint.name,
            'email': complaint.email,
            'order_number': '',
            'category': Complaint.OTHER,
            'message': complaint.message,
            'status': Complaint.IN_PROGRESS,
            '_save': 'Save',
        })
        self.assertEqual(response.status_code, 302)
        complaint.refresh_from_db()
        self.assertEqual(complaint.status, Complaint.IN_PROGRESS)

    def test_email_failures_do_not_lose_complaint_or_hide_confirmation(self):
        with patch('products.views.send_mail', side_effect=OSError('SMTP unavailable')) as send:
            with self.assertLogs('products.views', level='ERROR'):
                response = self.client.post(self.url, self.payload)

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'complaint_confirmation.html')
        self.assertEqual(Complaint.objects.count(), 1)
        self.assertEqual(send.call_count, 2)
        self.assertContains(response, Complaint.objects.get().reference)

    def test_contact_link_is_available_to_guests(self):
        response = self.client.get(reverse('home'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'href="{self.url}"')

    def test_guest_user_can_only_sell_twice_before_signup(self):
        payload = {
            'name': 'Test Product',
            'description': 'A test product',
            'price': '99.99',
            'stock': '5',
            'category': 'Electronics',
        }

        for _ in range(2):
            response = self.client.post(reverse('list_item'), payload)
            self.assertEqual(response.status_code, 302)

        response = self.client.post(reverse('list_item'), payload)
        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, reverse('signup'))

    @patch('products.views.gateways.initialize_paystack_payment', return_value='https://checkout.example.test')
    def test_guest_user_can_only_buy_twice_before_signup(self, initialize_payment):
        from vendors.models import Vendor
        from products.models import Product
        from django.contrib.auth import get_user_model

        default_user = get_user_model().objects.create_user(username='temp_vendor', password='Test123!')
        vendor = Vendor.objects.create(user=default_user, business_name='Guest Vendor')

        for i in range(2):
            product = Product.objects.create(
                vendor=vendor,
                name=f'Guest Buy Product {i + 1}',
                description='A buy test product',
                price='10.00',
                stock=5,
                category='Electronics',
            )
            self.client.get(reverse('add_to_cart', kwargs={'product_id': product.id}))

            response = self.client.post(reverse('checkout'), {
                'full_name': 'Guest Buyer',
                'email': 'guest@example.com',
                'address': '123 Guest St',
                'provider': 'paystack',
            })
            self.assertEqual(response.status_code, 302)
            order = Order.objects.latest('pk')
            self.assertEqual(order.payment_status, 'unpaid')
            self.assertEqual(order.status, 'Pending')
            self.assertFalse(order.legacy)

        response = self.client.post(reverse('checkout'), {
            'full_name': 'Guest Buyer',
            'email': 'guest@example.com',
            'address': '123 Guest St',
            'provider': 'paystack',
        })
        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, reverse('signup'))

    @patch('products.views.gateways.initialize_paystack_payment', return_value='https://checkout.example.test')
    def test_checkout_uses_paystack_as_the_only_gateway(self, initialize_payment):
        user = get_user_model().objects.create_user(username='paystack_only_buyer', password='Test123!')
        vendor = Vendor.objects.create(user=get_user_model().objects.create_user(username='paystack_only_vendor', password='Test123!'), business_name='Paystack Vendor')
        product = Product.objects.create(
            vendor=vendor,
            name='Paystack only product',
            description='Only Paystack is accepted',
            price='25.00',
            stock=4,
            category='Electronics',
        )
        self.client.force_login(user)
        self.client.get(reverse('add_to_cart', kwargs={'product_id': product.id}))

        checkout_page = self.client.get(reverse('checkout'))
        self.assertNotContains(checkout_page, 'OPay')

        response = self.client.post(reverse('checkout'), {
            'full_name': 'Paystack Buyer',
            'email': 'paystackbuyer@example.com',
            'address': '456 Paystack Street',
            'provider': 'paystack',
        })

        self.assertEqual(response.status_code, 302)
        self.assertEqual(Payment.objects.latest('pk').provider, Payment.PAYSTACK)

    def test_authenticated_user_must_log_back_in_after_24_hours(self):
        user = get_user_model().objects.create_user(username='stale_user', password='Test123!')
        self.client.force_login(user)
        user.last_login = timezone.now() - timezone.timedelta(days=2)
        user.save(update_fields=['last_login'])

        response = self.client.post(reverse('list_item'), {
            'name': 'Fresh Listing',
            'description': 'Needs re-auth',
            'price': '45.00',
            'stock': '7',
            'category': 'Electronics',
        })

        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, reverse('login') + '?next=' + reverse('list_item'))

    def test_empty_home_and_category_pages_show_clean_empty_state(self):
        Product.objects.all().delete()
        home_response = self.client.get(reverse('home'))
        category_response = self.client.get(reverse('subcategory', kwargs={'category_slug': 'electronics'}))

        self.assertEqual(home_response.status_code, 200)
        self.assertContains(home_response, 'No products found')
        self.assertEqual(category_response.status_code, 200)
        self.assertContains(category_response, 'No products found')

    def test_building_materials_categories_and_listing_fields(self):
        root = CategoryNode.objects.get(parent__isnull=True, slug='building-materials')
        self.assertEqual(
            set(root.children.values_list('name', flat=True)),
            {'Spanish Tiles', 'Doors', 'Other Building Materials'},
        )
        response = self.client.get(reverse('subcategory', kwargs={'category_slug': 'building-materials'}))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Spanish Tiles')
        self.assertContains(response, 'Doors')
        self.assertContains(response, 'Other Building Materials')

        user = get_user_model().objects.create_user(username='materials_vendor', password='Test123!')
        vendor = Vendor.objects.create(user=user, business_name='Materials Vendor', fashion_audience='men')
        tiles = root.children.get(slug='spanish-tiles')
        form = ProductListingForm(data={
            'name': 'Porcelain tile',
            'description': 'Floor tile',
            'price': '25.00',
            'stock': '20',
            'category': 'Building Materials',
            'section': str(tiles.pk),
            'unit_of_sale': 'box',
            'quantity_per_carton': '8',
            'dimensions': '60 x 60 cm',
            'material_finish': 'Porcelain, matte',
            'country_of_origin': 'Nigeria',
        }, vendor=vendor)
        self.assertTrue(form.is_valid(), form.errors)
        self.client.force_login(user)
        response = self.client.post(reverse('list_item'), form.data)
        self.assertEqual(response.status_code, 302)
        product = Product.objects.get(name='Porcelain tile')
        self.assertEqual(product.section, tiles)
        self.assertEqual(product.unit_of_sale, 'box')
        self.assertEqual(product.quantity_per_carton, 8)
        self.assertEqual(product.dimensions, '60 x 60 cm')
        self.assertEqual(product.material_finish, 'Porcelain, matte')
        self.assertEqual(product.country_of_origin, 'Nigeria')

    def test_product_owner_can_edit_a_listing(self):
        user = get_user_model().objects.create_user(username='product_owner', password='Test123!')
        vendor = Vendor.objects.create(user=user, business_name='Owner Store')
        product = Product.objects.create(
            vendor=vendor,
            name='Old name',
            description='Old description',
            price='10.00',
            stock=2,
            category='Electronics',
        )
        self.client.force_login(user)
        men_section = CategoryNode.objects.get(parent__slug='fashion', slug='men')

        response = self.client.post(reverse('edit_product', kwargs={'id': product.id}), {
            'name': 'Updated name',
            'description': 'Updated description',
            'price': '12.50',
            'stock': 5,
            'category': 'Fashion',
            'section': men_section.pk,
        })

        self.assertRedirects(response, reverse('product_detail', kwargs={'id': product.id}))
        product.refresh_from_db()
        self.assertEqual(product.name, 'Updated name')
        self.assertEqual(product.category, 'Fashion')

    def test_non_owner_cannot_manage_a_listing(self):
        owner = get_user_model().objects.create_user(username='owner', password='Test123!')
        other_user = get_user_model().objects.create_user(username='other_user', password='Test123!')
        vendor = Vendor.objects.create(user=owner, business_name='Owner Store')
        product = Product.objects.create(
            vendor=vendor,
            name='Protected product',
            description='Do not edit',
            price='10.00',
            stock=1,
            category='Electronics',
        )
        self.client.force_login(other_user)

        response = self.client.get(reverse('edit_product', kwargs={'id': product.id}))

        self.assertEqual(response.status_code, 403)


class FashionWorkflowTests(TestCase):
    def setUp(self):
        self.user_model = get_user_model()
        self.seller = self.user_model.objects.create_user(username='fashion_seller', password='Test123!')
        self.vendor = Vendor.objects.create(user=self.seller, business_name='Fashion Store')
        self.men = CategoryNode.objects.get(parent__slug='fashion', slug='men')
        self.women = CategoryNode.objects.get(parent__slug='fashion', slug='women')
        self.unisex = CategoryNode.objects.get(parent__slug='fashion', slug='unisex')

    def make_fashion_product(self, name, section, status=Product.APPROVED):
        return Product.objects.create(
            vendor=self.vendor,
            name=name,
            description='Fashion listing description',
            price='25.00',
            stock=4,
            category='Fashion',
            section=section,
            moderation_status=status,
        )

    def test_fashion_listing_requires_valid_section_and_vendor_audience(self):
        self.vendor.fashion_audience = 'men'
        self.vendor.save(update_fields=('fashion_audience',))
        self.client.force_login(self.seller)
        payload = {
            'name': 'Restricted listing',
            'description': 'A product for the wrong section',
            'price': '25.00',
            'stock': '4',
            'category': 'Fashion',
        }

        automatic = self.client.post(reverse('list_item'), {**payload, 'name': "Men's agbada"})
        self.assertEqual(automatic.status_code, 302)
        automatic_product = Product.objects.get(name="Men's agbada")
        self.assertEqual(automatic_product.section, self.men)
        self.assertEqual(automatic_product.auto_section, self.men)
        self.assertIn('Men', automatic_product.classification_reason)
        automatic_product.delete()

        wrong_section = self.client.post(reverse('list_item'), {**payload, 'section': self.women.pk})
        self.assertEqual(wrong_section.status_code, 200)
        self.assertContains(wrong_section, 'This store can only list Men or Unisex products.')
        self.assertFalse(Product.objects.exists())

        accepted = self.client.post(reverse('list_item'), {**payload, 'name': "Men's senator wear", 'section': self.men.pk})
        product = Product.objects.get(name="Men's senator wear")
        self.assertEqual(accepted.status_code, 302)
        self.assertEqual(product.section, self.men)
        self.assertEqual(product.moderation_status, Product.PENDING)

    def test_classifier_handles_nigerian_phrases_boundaries_and_mixed_signals(self):
        self.assertEqual(classify_fashion_section('Agbada for men')[0], 'men')
        self.assertEqual(classify_fashion_section('Iro and buba with gele')[0], 'women')
        self.assertEqual(classify_fashion_section('Unisex kaftan')[0], 'unisex')
        self.assertEqual(classify_fashion_section('Womenology gadget')[0], 'unisex')
        self.assertEqual(classify_fashion_section('Mens kaftan and women gele')[0], 'unisex')

    def test_classifier_assigns_mens_senator_wear_to_men(self):
        self.assertEqual(classify_fashion_section("Men's Senator Wear")[0], 'men')

    def test_classifier_assigns_ladies_maxi_dress_to_women(self):
        self.assertEqual(classify_fashion_section('Ladies Maxi Dress')[0], 'women')

    def test_classifier_assigns_cotton_top_to_unisex_without_signals(self):
        result = classify_fashion_section('Cotton Top')
        self.assertEqual(result[0], 'unisex')
        self.assertIn('No gender-specific Fashion terms matched', result[1])

    def test_classifier_assigns_explicit_unisex_hoodie_to_unisex(self):
        self.assertEqual(classify_fashion_section('Unisex Hoodie')[0], 'unisex')

    def test_classifier_assigns_mixed_men_and_women_sneakers_to_unisex(self):
        self.assertEqual(classify_fashion_section('Men and Women Sneakers')[0], 'unisex')

    def test_classifier_assigns_agbada_and_gele_set_to_unisex(self):
        self.assertEqual(classify_fashion_section('Agbada and Gele Set')[0], 'unisex')

    def test_fashion_listing_saves_the_automatic_classifier_section(self):
        self.client.force_login(self.seller)
        expected_sections = {
            "Men's Senator Wear": self.men,
            'Ladies Maxi Dress': self.women,
            'Cotton Top': self.unisex,
            'Unisex Hoodie': self.unisex,
            'Men and Women Sneakers': self.unisex,
            'Agbada and Gele Set': self.unisex,
        }

        for title, expected_section in expected_sections.items():
            with self.subTest(title=title):
                response = self.client.post(reverse('list_item'), {
                    'name': title,
                    'description': 'Fashion listing',
                    'price': '25.00',
                    'stock': '4',
                    'category': 'Fashion',
                })
                product = Product.objects.get(name=title)
                self.assertEqual(response.status_code, 302)
                self.assertEqual(product.section, expected_section)
                self.assertEqual(product.auto_section, expected_section)
                self.assertFalse(product.classification_overridden)

    def test_seller_override_is_saved_and_requires_staff_review(self):
        self.client.force_login(self.seller)
        response = self.client.post(reverse('list_item'), {
            'name': "Women's bubu",
            'description': 'Traditional outfit',
            'price': '25.00',
            'stock': 4,
            'category': 'Fashion',
            'section': self.men.pk,
        })
        self.assertEqual(response.status_code, 302)
        product = Product.objects.get(name="Women's bubu")
        self.assertEqual(product.auto_section, self.women)
        self.assertEqual(product.section, self.men)
        self.assertTrue(product.classification_overridden)
        self.assertEqual(product.moderation_status, Product.PENDING)
        staff = self.user_model.objects.create_user(username='override_reviewer', password='Test123!', is_staff=True)
        self.client.force_login(staff)
        queue = self.client.get(reverse('fashion_review_queue'))
        self.assertContains(queue, 'Classifier recommendation: Women')
        self.assertContains(queue, 'seller override requires review')
        self.assertContains(queue, 'Classification:')
        self.assertContains(queue, 'bubu')

    def test_admin_approval_trusts_vendor_after_fifth_approved_fashion_item(self):
        for number in range(4):
            self.make_fashion_product(f'Approved {number}', self.men)
        pending = self.make_fashion_product('Awaiting review', None, Product.PENDING)
        staff = self.user_model.objects.create_user(username='fashion_admin', password='Test123!', is_staff=True)
        self.client.force_login(staff)

        response = self.client.post(reverse('fashion_review_queue'), {
            'product_id': pending.pk,
            'action': 'approve',
            'section_id': self.unisex.pk,
        })

        self.assertRedirects(response, reverse('fashion_review_queue'))
        pending.refresh_from_db()
        self.vendor.refresh_from_db()
        self.assertEqual(pending.moderation_status, Product.APPROVED)
        self.assertEqual(pending.section, self.unisex)
        self.assertTrue(self.vendor.fashion_trusted)

        self.client.force_login(self.seller)
        auto_approved = self.client.post(reverse('list_item'), {
            'name': "Trusted vendor men's listing",
            'description': 'Automatically approved listing',
            'price': '30.00',
            'stock': '2',
            'category': 'Fashion',
            'section': self.men.pk,
        })
        self.assertEqual(auto_approved.status_code, 302)
        self.assertEqual(Product.objects.get(name="Trusted vendor men's listing").moderation_status, Product.APPROVED)

    def test_rejection_requires_reason_and_records_vendor_violation(self):
        pending = self.make_fashion_product('Reject me', None, Product.PENDING)
        staff = self.user_model.objects.create_user(username='fashion_reviewer', password='Test123!', is_staff=True)
        self.client.force_login(staff)

        missing_reason = self.client.post(reverse('fashion_review_queue'), {
            'product_id': pending.pk,
            'action': 'reject',
        })
        self.assertRedirects(missing_reason, reverse('fashion_review_queue'))
        self.assertFalse(pending.violations.exists())

        rejected = self.client.post(reverse('fashion_review_queue'), {
            'product_id': pending.pk,
            'action': 'reject',
            'rejection_reason': 'The selected section does not match the item.',
        })
        self.assertRedirects(rejected, reverse('fashion_review_queue'))
        pending.refresh_from_db()
        self.assertEqual(pending.moderation_status, Product.REJECTED)
        self.assertEqual(pending.violations.get().reason, 'The selected section does not match the item.')

    def test_fashion_storefront_shows_only_approved_products_in_requested_section(self):
        self.make_fashion_product('Men approved', self.men)
        self.make_fashion_product('Men pending', self.men, Product.PENDING)
        self.make_fashion_product('Women approved', self.women)

        response = self.client.get(reverse('fashion_section', kwargs={'section_slug': 'men'}))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Fashion')
        self.assertContains(response, 'Men approved')
        self.assertNotContains(response, 'Men pending')
        self.assertNotContains(response, 'Women approved')

    @override_settings(
        EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
        COMPLAINTS_EMAIL='support@example.test',
        DEFAULT_FROM_EMAIL='noreply@example.test',
    )
    def test_three_distinct_customer_reports_flag_product_and_duplicate_is_idempotent(self):
        product = self.make_fashion_product('Reportable item', self.men)
        for number in range(3):
            customer = self.user_model.objects.create_user(username=f'customer_{number}', password='Test123!')
            self.client.force_login(customer)
            response = self.client.post(reverse('report_product', kwargs={'id': product.pk}), {'reason': 'Wrong Fashion section'})
            self.assertRedirects(response, reverse('product_detail', kwargs={'id': product.pk}))
            if number == 0:
                self.client.post(reverse('report_product', kwargs={'id': product.pk}), {'reason': 'Duplicate report'})

        product.refresh_from_db()
        self.assertEqual(product.reports.count(), 3)
        self.assertEqual(Complaint.objects.filter(category=Complaint.SELLER_COMPLAINT).count(), 3)
        self.assertEqual(len(mail.outbox), 6)
        self.assertTrue(product.flagged_for_review)

    def test_home_and_fashion_detail_hide_pending_products(self):
        visible = self.make_fashion_product('Approved fashion item', self.unisex)
        hidden = self.make_fashion_product('Pending fashion item', self.unisex, Product.PENDING)

        home_response = self.client.get(reverse('home'))
        hidden_response = self.client.get(reverse('product_detail', kwargs={'id': hidden.pk}))

        self.assertContains(home_response, visible.name)
        self.assertNotContains(home_response, hidden.name)
        self.assertEqual(hidden_response.status_code, 404)


class ClearProductsCommandTests(TestCase):
    def setUp(self):
        self.vendor = Vendor.objects.create(user=get_user_model().objects.create_user(username='clear_vendor', password='Test123!'), business_name='Clear Vendor')
        self.product = Product.objects.create(
            vendor=self.vendor,
            name='Demo product',
            description='Will be cleared',
            price='44.00',
            stock=5,
            category='Electronics',
        )
        self.cart_item = CartItem.objects.create(user=self.vendor.user, product=self.product, quantity=1)
        self.report = ProductReport.objects.create(product=self.product, reporter=self.vendor.user, reason='Spam')
        self.order = Order.objects.create(
            full_name='Buyer',
            email='buyer@example.com',
            address='123 Test St',
            total_amount='44.00',
            total_kobo=4400,
            payment_status='paid',
            status='Completed',
        )
        self.order_item = OrderItem.objects.create(
            order=self.order,
            product=self.product,
            price=self.product.price,
            price_kobo=self.product.price_kobo,
            quantity=1,
            vendor=self.vendor,
        )
        self.category_count = CategoryNode.objects.count()
        self.user_count = get_user_model().objects.count()

    @override_settings(DEBUG=True)
    def test_clear_products_dry_run_does_nothing(self):
        out = StringIO()
        call_command('clear_products', stdout=out)
        output = out.getvalue()

        self.assertIn('Dry run', output)
        self.assertEqual(Product.objects.count(), 1)
        self.assertEqual(CartItem.objects.count(), 1)
        self.assertEqual(OrderItem.objects.filter(product=self.product).count(), 1)
        self.assertEqual(CategoryNode.objects.count(), self.category_count)

    @override_settings(DEBUG=True)
    def test_clear_products_confirm_deletes_only_product_rows_and_keeps_history(self):
        out = StringIO()
        call_command('clear_products', confirm=True, stdout=out)

        self.assertEqual(Product.objects.count(), 0)
        self.assertEqual(CartItem.objects.count(), 0)
        self.assertEqual(ProductReport.objects.count(), 0)
        self.assertEqual(CategoryNode.objects.count(), self.category_count)
        self.assertEqual(get_user_model().objects.count(), self.user_count)

        self.order_item.refresh_from_db()
        self.assertIsNone(self.order_item.product)
        self.assertEqual(self.order_item.product_name_snapshot, 'Demo product')


class EscrowAndPayoutSafetyTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='escrow_vendor', password='Test123!')
        self.vendor = Vendor.objects.create(user=self.user, business_name='Escrow Vendor', paystack_recipient_code='rec_123')
        self.wallet, _ = VendorWallet.objects.get_or_create(vendor=self.vendor)
        self.platform_settings = PlatformPaymentSettings.get_solo()
        self.platform_settings.default_commission_bps = 1000
        self.platform_settings.holding_period_days = 2
        self.platform_settings.minimum_withdrawal_kobo = 500000
        self.platform_settings.save(update_fields=('default_commission_bps', 'holding_period_days', 'minimum_withdrawal_kobo'))

    def create_paid_order(self, *, total_kobo=5000, price='50.00', reference='pay_ref_test'):
        order = Order.objects.create(
            user=self.user,
            full_name='Test Buyer',
            email='buyer@example.com',
            address='123 Buyer Lane',
            total_amount=Decimal(price),
            total_kobo=total_kobo,
            payment_status='unpaid',
            status='Pending',
        )
        product = Product.objects.create(
            vendor=self.vendor,
            name='Escrow Product',
            description='Escrow item description',
            price=Decimal(price),
            stock=1,
            category='Electronics',
        )
        OrderItem.objects.create(
            order=order,
            product=product,
            price=Decimal(price),
            price_kobo=total_kobo,
            quantity=1,
            vendor=self.vendor,
            commission_rate_bps=1000,
            commission_kobo=500,
            vendor_net_kobo=4500,
        )
        vendor_order = VendorOrder.objects.create(order=order, vendor=self.vendor, subtotal_kobo=total_kobo)
        payment = Payment.objects.create(
            order=order,
            provider=Payment.PAYSTACK,
            reference=reference,
            amount_kobo=total_kobo,
            currency='NGN',
            status=Payment.PENDING,
        )
        return order, vendor_order, payment

    def test_commission_calculation_uses_vendor_category_and_platform_defaults(self):
        CategoryCommission.objects.create(category='Electronics', commission_bps=1500)
        self.vendor.commission_override_bps = 1800
        self.vendor.save(update_fields=('commission_override_bps',))
        self.assertEqual(commission_rate_bps(self.vendor, 'Electronics'), 1800)

        self.vendor.commission_override_bps = None
        self.vendor.save(update_fields=('commission_override_bps',))
        self.assertEqual(commission_rate_bps(self.vendor, 'Electronics'), 1500)
        self.assertEqual(commission_rate_bps(self.vendor, 'Fashion'), self.platform_settings.default_commission_bps)

        order = Order.objects.create(
            user=self.user,
            full_name='Commission Buyer',
            email='commission@example.com',
            address='99 Commission St',
            total_amount=Decimal('10.00'),
            total_kobo=1000,
        )
        product = Product.objects.create(
            vendor=self.vendor,
            name='Commission Test Product',
            description='Commission calculation check',
            price=Decimal('10.00'),
            stock=1,
            category='Electronics',
        )
        item = OrderItem.objects.create(
            order=order,
            product=product,
            price=Decimal('10.00'),
            price_kobo=1000,
            quantity=1,
            vendor=self.vendor,
        )
        gross_kobo = snapshot_order_item(item)
        self.assertEqual(gross_kobo, 1000)
        self.assertEqual(item.commission_rate_bps, 1500)
        self.assertEqual(item.commission_kobo, 150)
        self.assertEqual(item.vendor_net_kobo, 850)

    @override_settings(PAYSTACK_SECRET_KEY='test-secret')
    @patch('products.views.gateways.verify_paystack_transaction')
    def test_paystack_webhook_verifies_signature_and_rejects_duplicates(self, verify_transaction):
        verify_transaction.return_value = {'status': 'success', 'amount': 5000, 'currency': 'NGN', 'fees': 0, 'id': 101}
        order, vendor_order, payment = self.create_paid_order(reference='sig_ref_1')
        payload = json.dumps({
            'event': 'charge.success',
            'data': {
                'reference': payment.reference,
                'amount': 5000,
                'currency': 'NGN',
                'fees': 0,
                'id': 101,
            },
        }).encode()
        signature = hmac.new(b'test-secret', payload, hashlib.sha512).hexdigest()

        response = self.client.post(
            reverse('paystack_webhook'),
            data=payload,
            content_type='application/json',
            HTTP_X_PAYSTACK_SIGNATURE=signature,
        )
        self.assertEqual(response.status_code, 200)
        payment.refresh_from_db()
        order.refresh_from_db()
        self.assertEqual(payment.status, Payment.SUCCESS)
        self.assertEqual(order.payment_status, 'paid')

        duplicate_response = self.client.post(
            reverse('paystack_webhook'),
            data=payload,
            content_type='application/json',
            HTTP_X_PAYSTACK_SIGNATURE=signature,
        )
        self.assertEqual(duplicate_response.status_code, 200)
        self.assertEqual(
            payment.order.vendor_orders.get().ledger_entries.count(),
            1,
        )
        self.assertEqual(PaymentWebhookEvent.objects.filter(event_key='paystack:charge.success:sig_ref_1').count(), 1)

    def test_holding_period_release_moves_escrow_to_available_balance(self):
        order, vendor_order, payment = self.create_paid_order(reference='release_ref_1')
        record_successful_payment(payment, verified_amount_kobo=5000, currency='NGN')
        mark_vendor_order_delivered(vendor_order)

        released = release_matured_vendor_orders(now=vendor_order.release_at + timezone.timedelta(seconds=1))
        vendor_order.refresh_from_db()
        self.wallet.refresh_from_db()

        self.assertEqual(released, 1)
        self.assertEqual(self.wallet.pending_balance_kobo, 0)
        self.assertEqual(self.wallet.available_balance_kobo, 4500)
        self.assertIsNotNone(vendor_order.released_at)

    def test_open_dispute_blocks_release(self):
        order, vendor_order, payment = self.create_paid_order(reference='dispute_ref_1')
        record_successful_payment(payment, verified_amount_kobo=5000, currency='NGN')
        mark_vendor_order_delivered(vendor_order)
        vendor_order.has_open_dispute = True
        vendor_order.save(update_fields=('has_open_dispute',))

        released = release_matured_vendor_orders(now=vendor_order.release_at + timezone.timedelta(seconds=1))
        self.wallet.refresh_from_db()

        self.assertEqual(released, 0)
        self.assertEqual(self.wallet.pending_balance_kobo, 4500)
        self.assertEqual(self.wallet.available_balance_kobo, 0)

    def test_payout_failure_returns_funds_to_available_balance(self):
        self.wallet.available_balance_kobo = 2000000
        self.wallet.save(update_fields=('available_balance_kobo', 'updated_at'))
        payout = request_payout(self.vendor, 1500000)

        self.wallet.refresh_from_db()
        self.assertEqual(payout.status, Payout.PROCESSING)
        self.assertEqual(self.wallet.available_balance_kobo, 500000)

        self.assertTrue(fail_payout(payout, 'Gateway time-out'))
        payout.refresh_from_db()
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.available_balance_kobo, 2000000)
        self.assertEqual(payout.status, Payout.FAILED)

    def test_double_withdrawal_is_blocked_by_available_balance(self):
        self.wallet.available_balance_kobo = 2000000
        self.wallet.save(update_fields=('available_balance_kobo', 'updated_at'))

        payout = request_payout(self.vendor, 1500000)
        self.wallet.refresh_from_db()
        self.assertEqual(payout.status, Payout.PROCESSING)
        self.assertEqual(self.wallet.available_balance_kobo, 500000)

        with self.assertRaisesMessage(ValueError, 'Insufficient available balance.'):
            request_payout(self.vendor, 1500000)

        self.assertEqual(Payout.objects.filter(vendor=self.vendor).count(), 1)

    def test_fashion_section_validation_works_for_invalid_and_valid_entries(self):
        vendor = Vendor.objects.create(
            user=get_user_model().objects.create_user(username='fashion_validator', password='Test123!'),
            business_name='Fashion Validator',
        )
        vendor.fashion_audience = 'men'
        vendor.save(update_fields=('fashion_audience',))
        men = CategoryNode.objects.get(parent__slug='fashion', slug='men')
        electronics_root = CategoryNode.objects.create(name='Electronics Root', slug='electronics-root')

        invalid = Product(
            vendor=vendor,
            name='Wrong section listing',
            description='Bad section',
            price=Decimal('25.00'),
            stock=2,
            category='Fashion',
            section=electronics_root,
        )
        with self.assertRaises(ValidationError):
            invalid.clean()

        valid = Product(
            vendor=vendor,
            name='Correct section listing',
            description='Good section',
            price=Decimal('25.00'),
            stock=2,
            category='Fashion',
            section=men,
        )
        valid.clean()
        self.assertEqual(valid.section, men)
