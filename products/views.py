from django.shortcuts import render, redirect, get_object_or_404, reverse
from django.contrib.auth.decorators import login_required
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth import login, logout
from django.contrib import messages
from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django.db import transaction
from django.db.models import Q
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.mail import send_mail
from django.utils import timezone
from django.utils.text import slugify
from django.core.exceptions import PermissionDenied
import csv
import hashlib
import hmac
import json
import logging
import uuid
from decimal import Decimal

from .models import CategoryNode, Product, CartItem, Order, OrderItem, ProductReport, Complaint, ProductViolation, Payment, PaymentWebhookEvent, Payout, VendorOrder, VendorWallet, LedgerTransaction, VendorDispute
from vendors.models import Vendor, AccountProfile
from .forms import BuyerSignupForm, ComplaintForm, ProductListingForm, RoleAuthenticationForm, RoleSignupForm, VendorStoreSettingsForm
from . import gateways
from .money import (
    complete_payout,
    create_vendor_orders,
    fail_payout,
    naira_to_kobo,
    record_successful_payment,
    release_matured_vendor_orders,
    request_payout,
    snapshot_order_item,
)
from .fashion_classifier import classify_fashion_section

SUBCATEGORY_CHOICES = tuple(
    choice for choice in Product.CATEGORY_CHOICES
    if choice[0] not in {'Automotive', 'Toys & Games'}
)

logger = logging.getLogger(__name__)
COMPLAINT_RATE_LIMIT = 3
COMPLAINT_RATE_WINDOW_SECONDS = 60 * 60


def _request_client_ip(request):
    return request.META.get('REMOTE_ADDR') or None


def _complaint_rate_limit_allows(request):
    client_ip = _request_client_ip(request) or 'unknown'
    key_digest = hashlib.sha256(client_ip.encode('utf-8')).hexdigest()
    cache_key = f'complaint-rate:{key_digest}'
    if cache.add(cache_key, 1, timeout=COMPLAINT_RATE_WINDOW_SECONDS):
        return True
    try:
        return cache.incr(cache_key) <= COMPLAINT_RATE_LIMIT
    except ValueError:
        return cache.add(cache_key, 1, timeout=COMPLAINT_RATE_WINDOW_SECONDS)


def _send_complaint_emails(complaint):
    complaint_body = (
        f'Reference: {complaint.reference}\n'
        f'Name: {complaint.name}\n'
        f'Email: {complaint.email}\n'
        f'Order number: {complaint.order_number or "Not provided"}\n'
        f'Category: {complaint.get_category_display()}\n'
        f'Status: {complaint.get_status_display()}\n\n'
        f'{complaint.message}'
    )
    if settings.COMPLAINTS_EMAIL:
        try:
            sent = send_mail(
                f'[{complaint.reference}] {complaint.get_category_display()}',
                complaint_body,
                settings.DEFAULT_FROM_EMAIL,
                [settings.COMPLAINTS_EMAIL],
                fail_silently=False,
            )
            if not sent:
                logger.error('Complaint notification email was not sent for %s.', complaint.reference)
        except Exception:
            logger.exception('Failed to send support notification for complaint %s.', complaint.reference)
    else:
        logger.error(
            'COMPLAINTS_EMAIL is not configured; complaint %s was saved without staff notification.',
            complaint.reference,
        )

    try:
        sent = send_mail(
            f'TradeZone support received your message ({complaint.reference})',
            (
                f'Hello {complaint.name},\n\n'
                f'We received your message and recorded it as {complaint.reference}. '
                'Our support team will review it.\n\n'
                f'Reference: {complaint.reference}\n'
                'TradeZone Support'
            ),
            settings.DEFAULT_FROM_EMAIL,
            [complaint.email],
            fail_silently=False,
        )
        if not sent:
            logger.error('Complaint acknowledgement email was not sent for %s.', complaint.reference)
    except Exception:
        logger.exception('Failed to send acknowledgement for complaint %s.', complaint.reference)


def _create_complaint(
    *,
    name,
    email,
    category,
    message,
    order_number='',
    user=None,
    client_ip=None,
    product_report=None,
):
    values = {
        'name': name,
        'email': email,
        'category': category,
        'message': message,
        'order_number': str(order_number or ''),
        'user': user,
        'client_ip': client_ip,
    }
    if product_report is None:
        complaint = Complaint.objects.create(**values)
        created = True
    else:
        complaint, created = Complaint.objects.get_or_create(
            product_report=product_report,
            defaults=values,
        )
    if created:
        _send_complaint_emails(complaint)
    return complaint


def _user_can_sell(user):
    if not user.is_authenticated:
        return True
    if user.is_staff:
        return True
    profile, _ = AccountProfile.objects.get_or_create(
        user=user,
        defaults={
            'role': AccountProfile.SELLER if hasattr(user, 'vendor_profile') else AccountProfile.BUYER,
        },
    )
    return profile.role == AccountProfile.SELLER


def _guest_transaction_limit_reached(request, action_name):
    if request.user.is_authenticated:
        return False

    key = f'guest_{action_name}_count'
    guest_count = int(request.session.get(key, 0))
    if guest_count >= 2:
        messages.error(request, 'You have used your two guest transactions. Please create an account to continue.')
        return True
    return False


def _force_relogin_if_stale(request, user):
    if not user.is_authenticated:
        return False

    if user.last_login is None or timezone.now() - user.last_login > timezone.timedelta(hours=24):
        messages.error(request, 'Your session expired. Please log in again to continue.')
        return True

    return False


def _record_guest_transaction(request, action_name):
    if request.user.is_authenticated:
        return

    key = f'guest_{action_name}_count'
    request.session[key] = int(request.session.get(key, 0)) + 1
    request.session.modified = True


def _fashion_root():
    return CategoryNode.objects.filter(parent__isnull=True, slug='fashion').first()


def _category_descendant_ids(node, include_self=True):
    ids = [node.pk] if include_self else []
    pending = list(node.children.all())
    while pending:
        child = pending.pop()
        ids.append(child.pk)
        pending.extend(child.children.all())
    return ids

def _get_cart_items(request):
    if request.user.is_authenticated:
        return CartItem.objects.filter(
            user=request.user,
            product__is_active=True,
            product__moderation_status=Product.APPROVED,
            product__vendor__is_suspended=False,
        )
    else:
        if not request.session.session_key:
            request.session.create()
        return CartItem.objects.filter(
            session_key=request.session.session_key,
            product__is_active=True,
            product__moderation_status=Product.APPROVED,
            product__vendor__is_suspended=False,
        )

def home_view(request):
    query = request.GET.get('q', '')
    category = request.GET.get('category', '')
    buyer_tour_enabled = (
        request.COOKIES.get('tradezone_buyer_tour') != 'done'
        if not request.user.is_authenticated else False
    )
    buyer_tour_prompt = buyer_tour_enabled if not request.user.is_authenticated else False
    buyer_tour_autostart = False
    onboarding_content_visible = not request.user.is_authenticated

    if request.user.is_authenticated and not request.user.is_staff:
        profile, _ = AccountProfile.objects.get_or_create(
            user=request.user,
            defaults={
                'role': AccountProfile.SELLER if hasattr(request.user, 'vendor_profile') else AccountProfile.BUYER,
            },
        )
        first_buyer_render = (
            profile.role == AccountProfile.BUYER
            and profile.onboarding_completed_at is None
        )
        if first_buyer_render:
            profile.onboarding_completed_at = timezone.now()
            profile.save(update_fields=('onboarding_completed_at',))
            buyer_tour_enabled = True
            buyer_tour_prompt = True
            buyer_tour_autostart = bool(request.session.pop('buyer_tour_autostart', False))
            onboarding_content_visible = True
        else:
            if 'tour' in request.GET:
                params = request.GET.copy()
                params.pop('tour', None)
                target = request.path
                if params:
                    target = f'{target}?{params.urlencode()}'
                return redirect(target)
            if profile.role == AccountProfile.SELLER:
                vendor = Vendor.objects.filter(user=request.user).only('onboarding_complete').first()
                onboarding_content_visible = not (vendor and vendor.onboarding_complete)

    products = Product.objects.filter(
        is_active=True,
        moderation_status=Product.APPROVED,
        vendor__is_suspended=False,
    ).select_related('vendor', 'section')
    
    if query:
        products = products.filter(
            Q(name__icontains=query) | Q(description__icontains=query) | Q(vendor__business_name__icontains=query)
        )
    if category and category != 'All Categories':
        products = products.filter(category__iexact=category)
        
    vendors = Vendor.objects.all()[:3]
    return render(request, 'home.html', {
        'products': products,
        'query': query,
        'selected_category': category,
        'vendors': vendors,
        'buyer_tour_enabled': buyer_tour_enabled,
        'buyer_tour_prompt': buyer_tour_prompt,
        'buyer_tour_autostart': buyer_tour_autostart,
        'onboarding_content_visible': onboarding_content_visible,
    })


def contact_support_view(request):
    initial = {}
    if request.user.is_authenticated:
        initial = {
            'name': request.user.get_full_name() or request.user.get_username(),
            'email': request.user.email,
        }
    form = ComplaintForm(request.POST or None, initial=initial)
    if request.method == 'POST' and form.is_valid():
        if form.cleaned_data['website']:
            form.add_error(None, 'We could not submit your message. Please try again.')
            return render(request, 'contact_support.html', {'form': form}, status=400)
        if not _complaint_rate_limit_allows(request):
            form.add_error(None, 'Too many messages have been sent from this network. Please try again later.')
            return render(request, 'contact_support.html', {'form': form}, status=429)

        complaint = _create_complaint(
            name=form.cleaned_data['name'].strip(),
            email=form.cleaned_data['email'].strip(),
            order_number=form.cleaned_data['order_number'],
            category=form.cleaned_data['category'],
            message=form.cleaned_data['message'],
            user=request.user if request.user.is_authenticated else None,
            client_ip=_request_client_ip(request),
        )
        return render(request, 'complaint_confirmation.html', {'complaint': complaint})
    return render(request, 'contact_support.html', {'form': form})


def subcategory_view(request, category_slug):
    category = next(
        (name for value, name in SUBCATEGORY_CHOICES if slugify(value) == category_slug),
        None,
    )
    if category is None:
        from django.http import Http404
        raise Http404('Subcategory not found.')

    products = Product.objects.filter(
        is_active=True,
        moderation_status=Product.APPROVED,
        vendor__is_suspended=False,
        category=category,
    ).select_related('vendor', 'section')
    if category == 'Fashion':
        products = products.filter(section__isnull=False)
    category_root = CategoryNode.objects.filter(parent__isnull=True, slug=category_slug).first()
    return render(request, 'subcategory.html', {
        'category': category,
        'products': products,
        'fashion_sections': _fashion_root().children.all() if category == 'Fashion' and _fashion_root() else (),
        'category_sections': category_root.children.all() if category_root and category != 'Fashion' else (),
    })


def fashion_section_view(request, section_slug):
    fashion_root = _fashion_root()
    if not fashion_root:
        from django.http import Http404
        raise Http404('Fashion sections are not configured.')
    section = get_object_or_404(CategoryNode, parent=fashion_root, slug=section_slug)
    products = Product.objects.filter(
        is_active=True,
        moderation_status=Product.APPROVED,
        vendor__is_suspended=False,
        category='Fashion',
        section_id__in=_category_descendant_ids(section),
    ).select_related('vendor', 'section')
    return render(request, 'subcategory.html', {
        'category': section.name,
        'products': products,
        'fashion_section': section,
        'fashion_sections': fashion_root.children.all(),
    })


def category_section_view(request, category_slug, section_slug):
    category = next(
        (name for value, name in SUBCATEGORY_CHOICES if slugify(value) == category_slug),
        None,
    )
    if category is None or category == 'Fashion':
        from django.http import Http404
        raise Http404('Category section not found.')
    root = CategoryNode.objects.filter(parent__isnull=True, slug=category_slug).first()
    if not root:
        from django.http import Http404
        raise Http404('Category sections are not configured.')
    section = get_object_or_404(CategoryNode, parent=root, slug=section_slug)
    products = Product.objects.filter(
        is_active=True,
        moderation_status=Product.APPROVED,
        vendor__is_suspended=False,
        category=category,
        section_id__in=_category_descendant_ids(section),
    ).select_related('vendor', 'section')
    return render(request, 'subcategory.html', {
        'category': section.name,
        'products': products,
        'category_root': root,
        'category_sections': root.children.all(),
    })

def list_item_view(request):
    if request.method == 'POST' and _force_relogin_if_stale(request, request.user):
        return redirect(f"{reverse('login')}?next={request.path}")

    if not _user_can_sell(request.user):
        messages.error(request, 'Buyer accounts cannot add products. Please sign in with a seller account.')
        return redirect('home')

    valid_categories = {value for value, _ in SUBCATEGORY_CHOICES}
    selected_category = request.GET.get('category', '')
    if selected_category not in valid_categories:
        selected_category = ''

    vendor = None
    if request.user.is_authenticated:
        vendor, _ = Vendor.objects.get_or_create(
            user=request.user,
            defaults={'business_name': f"{request.user.username}'s Store"},
        )
        if vendor.is_suspended:
            messages.error(request, 'This vendor account is suspended and cannot publish listings.')
            return redirect('home')

    if request.method == 'POST' and _guest_transaction_limit_reached(request, 'sale'):
        return redirect('signup')

    if request.method == 'POST' and vendor is None:
        from django.contrib.auth.models import User
        default_user, _ = User.objects.get_or_create(username='default_vendor', defaults={'email': 'vendor@tradezone.com'})
        vendor, _ = Vendor.objects.get_or_create(user=default_user, defaults={'business_name': 'Default Vendor Store'})

    form = ProductListingForm(
        request.POST or None,
        request.FILES or None,
        vendor=vendor,
        initial={'category': selected_category or 'Electronics'},
    )
    if request.method == 'POST' and form.is_valid():
        category = form.cleaned_data['category']
        section = form.cleaned_data['section']
        auto_section = None
        classification_reason = ''
        classification_overridden = False
        audience_requires_review = False
        if category == 'Fashion':
            section_slug, classification_reason = classify_fashion_section(
                form.cleaned_data['name'], form.cleaned_data['description']
            )
            fashion_root = _fashion_root()
            auto_section = CategoryNode.objects.get(parent=fashion_root, slug=section_slug)
            classification_overridden = bool(section and section.pk != auto_section.pk)
            if section is None:
                section = auto_section
            if vendor.fashion_audience in {'men', 'women'}:
                first_section = section
                while first_section.parent_id and first_section.parent_id != fashion_root.pk:
                    first_section = first_section.parent
                audience_requires_review = first_section.name not in {
                    'Unisex', 'Men' if vendor.fashion_audience == 'men' else 'Women'
                }
                if audience_requires_review:
                    classification_reason += ' Automatic result conflicts with this store audience; staff review required.'
        product = Product(
            vendor=vendor,
            name=form.cleaned_data['name'],
            description=form.cleaned_data['description'],
            price=form.cleaned_data['price'],
            stock=form.cleaned_data['stock'],
            category=category,
            section=section if category in {'Fashion', 'Building Materials'} else None,
            auto_section=auto_section,
            classification_reason=classification_reason,
            classification_overridden=classification_overridden,
            image=form.cleaned_data.get('image'),
            unit_of_sale=form.cleaned_data['unit_of_sale'],
            quantity_per_carton=form.cleaned_data['quantity_per_carton'],
            dimensions=form.cleaned_data['dimensions'],
            material_finish=form.cleaned_data['material_finish'],
            country_of_origin=form.cleaned_data['country_of_origin'],
            moderation_status=(
                Product.PENDING
                if category == 'Fashion' and (not vendor.fashion_trusted or classification_overridden or audience_requires_review)
                else Product.APPROVED
            ),
        )
        try:
            product.full_clean()
        except ValidationError as error:
            for field_errors in error.message_dict.values():
                for error_message in field_errors:
                    messages.error(request, error_message)
        else:
            product.save()
            if not request.user.is_authenticated:
                _record_guest_transaction(request, 'sale')
            if product.moderation_status == Product.PENDING:
                messages.success(request, f"Product '{product.name}' was submitted for Fashion review.")
            else:
                messages.success(request, f"Product '{product.name}' published successfully!")
            return redirect('product_detail', id=product.id)

    return render(request, 'list_item.html', {
        'form': form,
        'product': {
            'name': '',
            'description': '',
            'price': '',
            'stock': 10,
            'unit_of_sale': '',
            'quantity_per_carton': '',
            'dimensions': '',
            'material_finish': '',
            'country_of_origin': '',
        },
        'selected_category': form.data.get('category') or selected_category or 'Electronics',
        'selected_section': form.data.get('section') or '',
        'category_choices': SUBCATEGORY_CHOICES,
        'section_choices': form.fields['section'].queryset,
        'building_materials_category': selected_category == 'Building Materials',
    })

def product_detail_view(request, id):
    product = get_object_or_404(Product.objects.select_related('vendor__user'), pk=id)
    if (not product.is_active or product.moderation_status != Product.APPROVED or product.vendor.is_suspended) and not (
        request.user.is_authenticated and (request.user.is_staff or product.vendor.user_id == request.user.id)
    ):
        from django.http import Http404
        raise Http404('Product not found.')
    related_products = Product.objects.filter(
        category=product.category,
        is_active=True,
        moderation_status=Product.APPROVED,
        vendor__is_suspended=False,
    ).exclude(id=product.id)[:4]
    profile = None
    if request.user.is_authenticated and not request.user.is_staff and product.vendor.user_id != request.user.id:
        profile, _ = AccountProfile.objects.get_or_create(
            user=request.user,
            defaults={'role': AccountProfile.SELLER if hasattr(request.user, 'vendor_profile') else AccountProfile.BUYER},
        )
    return render(request, 'product_detail.html', {
        'product': product,
        'related_products': related_products,
        'can_report_product': bool(profile and profile.role == AccountProfile.BUYER),
    })


def _require_product_manager(request, product):
    if not request.user.is_authenticated:
        return redirect(f"{reverse('login')}?next={request.path}")
    if request.user.is_staff or product.vendor.user_id == request.user.id:
        return None
    raise PermissionDenied('You do not have permission to manage this product.')


def edit_product_view(request, id):
    product = get_object_or_404(Product.objects.select_related('vendor__user'), pk=id)
    denied_response = _require_product_manager(request, product)
    if denied_response:
        return denied_response

    if product.vendor.is_suspended and not request.user.is_staff:
        messages.error(request, 'This vendor account is suspended and cannot edit listings.')
        return redirect('product_detail', id=product.id)

    form = ProductListingForm(
        request.POST or None,
        request.FILES or None,
        vendor=product.vendor,
        initial={
            'name': product.name,
            'description': product.description,
            'price': product.price,
            'stock': product.stock,
            'category': product.category,
            'section': product.section_id,
            'unit_of_sale': product.unit_of_sale,
            'quantity_per_carton': product.quantity_per_carton,
            'dimensions': product.dimensions,
            'material_finish': product.material_finish,
            'country_of_origin': product.country_of_origin,
        },
    )
    if request.method == 'POST' and form.is_valid():
        category = form.cleaned_data['category']
        section = form.cleaned_data['section']
        auto_section = None
        classification_reason = ''
        classification_overridden = False
        audience_requires_review = False
        if category == 'Fashion':
            section_slug, classification_reason = classify_fashion_section(
                form.cleaned_data['name'], form.cleaned_data['description']
            )
            fashion_root = _fashion_root()
            auto_section = CategoryNode.objects.get(parent=fashion_root, slug=section_slug)
            classification_overridden = bool(section and section.pk != auto_section.pk)
            if section is None:
                section = auto_section
            if product.vendor.fashion_audience in {'men', 'women'}:
                first_section = section
                while first_section.parent_id and first_section.parent_id != fashion_root.pk:
                    first_section = first_section.parent
                audience_requires_review = first_section.name not in {
                    'Unisex', 'Men' if product.vendor.fashion_audience == 'men' else 'Women'
                }
                if audience_requires_review:
                    classification_reason += ' Automatic result conflicts with this store audience; staff review required.'
        product.name = form.cleaned_data['name']
        product.description = form.cleaned_data['description']
        product.price = form.cleaned_data['price']
        product.stock = form.cleaned_data['stock']
        product.category = category
        product.section = section if category in {'Fashion', 'Building Materials'} else None
        product.auto_section = auto_section
        product.classification_reason = classification_reason
        product.classification_overridden = classification_overridden
        product.unit_of_sale = form.cleaned_data['unit_of_sale']
        product.quantity_per_carton = form.cleaned_data['quantity_per_carton']
        product.dimensions = form.cleaned_data['dimensions']
        product.material_finish = form.cleaned_data['material_finish']
        product.country_of_origin = form.cleaned_data['country_of_origin']
        product.rejection_reason = ''
        product.moderation_status = (
            Product.PENDING
            if category == 'Fashion' and (not product.vendor.fashion_trusted or classification_overridden or audience_requires_review)
            else Product.APPROVED
        )
        if image := form.cleaned_data.get('image'):
            product.image = image
        try:
            product.full_clean()
        except ValidationError as error:
            for field_errors in error.message_dict.values():
                for error_message in field_errors:
                    messages.error(request, error_message)
        else:
            product.save()
            messages.success(request, f"Product '{product.name}' updated successfully!")
            return redirect('product_detail', id=product.id)

    return render(request, 'list_item.html', {
        'form': form,
        'product': product,
        'edit_mode': True,
        'selected_category': form.data.get('category') or product.category,
        'selected_section': form.data.get('section') or product.section_id or '',
        'category_choices': SUBCATEGORY_CHOICES,
        'section_choices': form.fields['section'].queryset,
        'building_materials_category': (form.data.get('category') or product.category) == 'Building Materials',
    })


def delete_product_view(request, id):
    product = get_object_or_404(Product.objects.select_related('vendor__user'), pk=id)
    denied_response = _require_product_manager(request, product)
    if denied_response:
        return denied_response
    if request.method != 'POST':
        return redirect('product_detail', id=product.id)

    product_name = product.name
    product.delete()
    messages.success(request, f"Product '{product_name}' deleted successfully.")
    return redirect('home')

def add_to_cart_view(request, product_id):
    if _force_relogin_if_stale(request, request.user):
        return redirect(f"{reverse('login')}?next={request.path}")

    if _guest_transaction_limit_reached(request, 'purchase'):
        return redirect('signup')

    product = get_object_or_404(
        Product,
        id=product_id,
        is_active=True,
        moderation_status=Product.APPROVED,
        vendor__is_suspended=False,
    )
    
    if request.user.is_authenticated:
        cart_item, created = CartItem.objects.get_or_create(
            user=request.user,
            product=product
        )
    else:
        if not request.session.session_key:
            request.session.create()
        cart_item, created = CartItem.objects.get_or_create(
            session_key=request.session.session_key,
            product=product
        )
        
    if not created:
        cart_item.quantity += 1
        cart_item.save()
        
    messages.success(request, f"Added '{product.name}' to your cart.")
    return redirect('cart')

def remove_from_cart_view(request, item_id):
    cart_item = get_object_or_404(CartItem, id=item_id)
    cart_item.delete()
    messages.info(request, "Item removed from cart.")
    return redirect('cart')


def update_cart_item_view(request, item_id):
    if request.method != 'POST':
        return redirect('cart')
    cart_item = get_object_or_404(_get_cart_items(request), pk=item_id)
    try:
        quantity = int(request.POST.get('quantity', ''))
    except (TypeError, ValueError):
        quantity = 0
    if quantity < 1 or quantity > 999:
        messages.error(request, 'Choose a quantity between 1 and 999.')
    else:
        cart_item.quantity = quantity
        cart_item.save(update_fields=('quantity',))
        messages.success(request, 'Cart quantity updated.')
    return redirect('cart')

def cart_view(request):
    cart_items = list(_get_cart_items(request).select_related('product', 'product__vendor'))
    subtotal_kobo = sum(item.get_total_price_kobo() for item in cart_items)
    shipping_kobo = 1250 if cart_items else 0
    return render(request, 'cart.html', {
        'cart_items': cart_items,
        'subtotal_kobo': subtotal_kobo,
        'shipping_kobo': shipping_kobo,
        'total_kobo': subtotal_kobo + shipping_kobo,
    })

def checkout_view(request):
    cart_items = list(_get_cart_items(request).select_related('product', 'product__vendor'))
    subtotal_kobo = sum(item.get_total_price_kobo() for item in cart_items)
    shipping_kobo = 1250 if cart_items else 0
    total_kobo = subtotal_kobo + shipping_kobo

    if request.method == 'POST':
        if _force_relogin_if_stale(request, request.user):
            return redirect(f"{reverse('login')}?next={request.path}")

        if _guest_transaction_limit_reached(request, 'purchase'):
            return redirect('signup')

        if not cart_items:
            messages.error(request, 'Your cart is empty.')
            return redirect('cart')

        full_name = request.POST.get('full_name', 'Customer')
        email = request.POST.get('email', 'customer@example.com')
        address = request.POST.get('address', 'Standard Shipping Address')
        provider = request.POST.get('provider')
        if provider != Payment.PAYSTACK:
            messages.error(request, 'Choose Paystack to pay in NGN.')
            return render(request, 'checkout.html', _checkout_context(cart_items, subtotal_kobo, shipping_kobo, total_kobo))

        with transaction.atomic():
            order = Order.objects.create(
                user=request.user if request.user.is_authenticated else None,
                full_name=full_name,
                email=email,
                address=address,
                total_amount=Decimal(total_kobo) / Decimal(100),
                total_kobo=total_kobo,
                shipping_kobo=shipping_kobo,
                status='Pending',
                payment_status='unpaid',
            )

            for cart_item in cart_items:
                product = cart_item.product
                order_item = OrderItem.objects.create(
                    order=order,
                    product=product,
                    price=product.current_price,
                    price_kobo=product.current_price_kobo,
                    quantity=cart_item.quantity,
                    vendor=product.vendor,
                )
                snapshot_order_item(order_item)
            create_vendor_orders(order)
            payment = Payment.objects.create(
                order=order,
                provider=provider,
                reference=f'tz-{uuid.uuid4().hex}',
                amount_kobo=total_kobo,
            )

        try:
            checkout_url = gateways.initialize_paystack_payment(
                payment,
                email,
                request.build_absolute_uri(reverse('payment_return')),
            )
        except gateways.GatewayError as error:
            payment.status = Payment.FAILED
            payment.save(update_fields=('status',))
            messages.error(request, str(error))
            return render(request, 'checkout.html', _checkout_context(cart_items, subtotal_kobo, shipping_kobo, total_kobo))

        _get_cart_items(request).delete()

        if not request.user.is_authenticated:
            _record_guest_transaction(request, 'purchase')
        return redirect(checkout_url)

    return render(request, 'checkout.html', _checkout_context(cart_items, subtotal_kobo, shipping_kobo, total_kobo))


def _checkout_context(cart_items, subtotal_kobo, shipping_kobo, total_kobo):
    return {
        'cart_items': cart_items,
        'subtotal_kobo': subtotal_kobo,
        'shipping_kobo': shipping_kobo,
        'total_kobo': total_kobo,
    }


def _confirm_paystack_payment(payment, verified_data):
    if verified_data.get('status') != 'success':
        payment.status = Payment.FAILED
        payment.save(update_fields=('status',))
        return False
    return record_successful_payment(
        payment,
        verified_amount_kobo=int(verified_data.get('amount', -1)),
        currency=verified_data.get('currency', ''),
        gateway_fee_kobo=int(verified_data.get('fees') or 0),
        provider_transaction_id=str(verified_data.get('id') or ''),
    )


def payment_return_view(request):
    reference = request.GET.get('reference', '').strip()
    payment = get_object_or_404(Payment.objects.select_related('order'), reference=reference)
    try:
        verified = gateways.verify_paystack_transaction(reference)
        paid = _confirm_paystack_payment(payment, verified)
    except gateways.GatewayError as error:
        messages.error(request, str(error))
        return redirect('profile')
    if paid or payment.status == Payment.SUCCESS:
        messages.success(request, f'Payment confirmed for order #{payment.order_id}.')
    else:
        messages.error(request, 'Payment has not been confirmed. Your order remains unpaid.')
    if payment.order.user_id == getattr(request.user, 'id', None):
        return redirect('profile')
    return redirect('home')


def _claim_webhook_event(provider, key):
    _, created = PaymentWebhookEvent.objects.get_or_create(
        event_key=key,
        defaults={'provider': provider},
    )
    return created


@csrf_exempt
def paystack_webhook_view(request):
    if request.method != 'POST':
        return HttpResponse(status=405)
    secret_key = getattr(settings, 'PAYSTACK_SECRET_KEY', '')
    signature = request.headers.get('x-paystack-signature', '')
    expected = hmac.new(secret_key.encode(), request.body, hashlib.sha512).hexdigest() if secret_key else ''
    if not signature or not expected or not hmac.compare_digest(expected, signature):
        return HttpResponse(status=401)
    try:
        event = json.loads(request.body)
    except (TypeError, ValueError):
        return HttpResponse(status=400)
    event_name = event.get('event')
    data = event.get('data') or {}
    reference = str(data.get('reference') or '')

    if event_name == 'charge.success' and reference:
        payment = Payment.objects.filter(reference=reference, provider=Payment.PAYSTACK).first()
        if not payment:
            return HttpResponse(status=200)
        try:
            verified = gateways.verify_paystack_transaction(reference)
            with transaction.atomic():
                event_key = f'paystack:charge.success:{reference}'
                if not _claim_webhook_event(Payment.PAYSTACK, event_key):
                    return HttpResponse(status=200)
                _confirm_paystack_payment(payment, verified)
        except gateways.GatewayError:
            return HttpResponse(status=503)
        except ValueError:
            return HttpResponse(status=400)
    elif event_name in {'transfer.success', 'transfer.failed', 'transfer.reversed'} and reference:
        payout = Payout.objects.filter(reference=reference).first()
        if not payout:
            return HttpResponse(status=200)
        with transaction.atomic():
            event_key = f'paystack:{event_name}:{reference}'
            if not _claim_webhook_event(Payment.PAYSTACK, event_key):
                return HttpResponse(status=200)
            if event_name == 'transfer.success':
                complete_payout(payout)
            else:
                fail_payout(
                    payout,
                    data.get('reason') or event_name,
                    reversed_transfer=event_name == 'transfer.reversed',
                )
    return HttpResponse(status=200)


@login_required(login_url='login')
def vendor_bank_view(request):
    vendor = get_object_or_404(Vendor, user=request.user)
    resolved_account = request.session.get('resolved_vendor_bank')
    if request.method == 'POST':
        action = request.POST.get('action')
        account_number = request.POST.get('account_number', '').strip()
        bank_code = request.POST.get('bank_code', '').strip()
        if action == 'resolve':
            if not account_number.isdigit() or len(account_number) < 8 or len(account_number) > 20 or not bank_code:
                messages.error(request, 'Enter a valid account number and Paystack bank code.')
            else:
                try:
                    resolved = gateways.resolve_paystack_account(account_number, bank_code)
                except gateways.GatewayError as error:
                    messages.error(request, str(error))
                else:
                    resolved_account = {
                        'account_number': resolved.get('account_number', account_number),
                        'bank_code': bank_code,
                        'account_name': resolved.get('account_name', ''),
                    }
                    request.session['resolved_vendor_bank'] = resolved_account
        elif action == 'confirm':
            resolved_account = request.session.get('resolved_vendor_bank')
            if not resolved_account or request.POST.get('confirmed_account_name', '').strip() != resolved_account.get('account_name'):
                messages.error(request, 'Confirm the exact account name returned by Paystack before saving.')
            else:
                try:
                    recipient = gateways.create_paystack_recipient(
                        account_number=resolved_account['account_number'],
                        bank_code=resolved_account['bank_code'],
                        account_name=resolved_account['account_name'],
                        email=request.user.email,
                    )
                except gateways.GatewayError as error:
                    messages.error(request, str(error))
                else:
                    vendor.bank_account_number = resolved_account['account_number']
                    vendor.bank_code = resolved_account['bank_code']
                    vendor.bank_account_name = resolved_account['account_name']
                    vendor.paystack_recipient_code = recipient['recipient_code']
                    vendor.save(update_fields=(
                        'bank_account_number', 'bank_code', 'bank_account_name', 'paystack_recipient_code',
                    ))
                    request.session.pop('resolved_vendor_bank', None)
                    messages.success(request, 'Your verified Paystack payout account has been saved.')
                    return redirect('vendor_dashboard')
    return render(request, 'vendor_bank.html', {
        'vendor': vendor,
        'resolved_account': resolved_account,
    })


def _start_vendor_payout(payout):
    try:
        result = gateways.initiate_paystack_transfer(payout, payout.vendor)
    except gateways.GatewayError as error:
        fail_payout(payout, str(error))
        return False, str(error)
    payout.transfer_code = result.get('transfer_code', '')
    provider_status = result.get('status')
    if provider_status in {'success', 'pending', 'otp'}:
        payout.status = Payout.PROCESSING
        payout.save(update_fields=('transfer_code', 'status'))
        return True, 'Transfer submitted to Paystack; final status will arrive by webhook.'
    fail_payout(payout, f'Unexpected Paystack transfer status: {provider_status}')
    return False, 'Paystack did not accept the transfer.'


@login_required(login_url='login')
def vendor_dashboard_view(request):
    vendor = get_object_or_404(Vendor, user=request.user)
    wallet, _ = VendorWallet.objects.get_or_create(vendor=vendor)
    if request.method == 'POST':
        try:
            amount_kobo = naira_to_kobo(request.POST.get('amount', '0'))
            payout = request_payout(vendor, amount_kobo)
        except (ValueError, ArithmeticError) as error:
            messages.error(request, str(error) or 'Enter a valid withdrawal amount in NGN.')
        else:
            if payout.status == Payout.AWAITING_APPROVAL:
                messages.success(request, 'Withdrawal request submitted for administrator approval.')
            else:
                success, message = _start_vendor_payout(payout)
                (messages.success if success else messages.error)(request, message)
            return redirect('vendor_dashboard')
    return render(request, 'vendor_dashboard.html', {
        'vendor': vendor,
        'wallet': wallet,
        'payouts': vendor.payouts.order_by('-requested_at')[:50],
        'earned_items': OrderItem.objects.filter(vendor=vendor, order__legacy=False, order__payment_status='paid').select_related('order', 'product').order_by('-order__created_at')[:50],
    })


@staff_member_required
def payout_approval_view(request, payout_id):
    if request.method != 'POST':
        return redirect('finance_dashboard')
    payout = get_object_or_404(Payout.objects.select_related('vendor'), pk=payout_id)
    action = request.POST.get('action')
    if action == 'approve' and payout.status == Payout.AWAITING_APPROVAL:
        success, message = _start_vendor_payout(payout)
        (messages.success if success else messages.error)(request, message)
    elif action == 'finalize' and payout.transfer_code:
        try:
            result = gateways.finalize_paystack_transfer(payout.transfer_code, request.POST.get('otp', '').strip())
        except gateways.GatewayError as error:
            messages.error(request, str(error))
        else:
            messages.success(request, result.get('message', 'Paystack accepted transfer finalization.'))
    elif action == 'reject' and payout.status == Payout.AWAITING_APPROVAL:
        fail_payout(payout, request.POST.get('reason', 'Rejected by administrator.'))
        messages.success(request, 'Payout rejected and reserved funds returned to the vendor balance.')
    else:
        messages.error(request, 'This payout is not awaiting approval.')
    return redirect('finance_dashboard')


@staff_member_required
def finance_dashboard_view(request):
    paid_items = OrderItem.objects.filter(order__legacy=False, order__payment_status='paid')
    if request.GET.get('format') == 'csv':
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = 'attachment; filename="tradezone-finance.csv"'
        writer = csv.writer(response)
        writer.writerow(('vendor', 'pending_kobo', 'available_kobo', 'total_paid_out_kobo'))
        for wallet in VendorWallet.objects.select_related('vendor').order_by('vendor__business_name'):
            writer.writerow((wallet.vendor.business_name, wallet.pending_balance_kobo, wallet.available_balance_kobo, wallet.total_paid_out_kobo))
        return response

    return render(request, 'finance_dashboard.html', {
        'commission_total_kobo': sum(paid_items.values_list('commission_kobo', flat=True)),
        'pending_liability_kobo': sum(VendorWallet.objects.values_list('pending_balance_kobo', flat=True)),
        'available_liability_kobo': sum(VendorWallet.objects.values_list('available_balance_kobo', flat=True)),
        'payouts': Payout.objects.select_related('vendor').exclude(status__in=(Payout.SUCCESS, Payout.FAILED, Payout.REVERSED)).order_by('requested_at'),
        'failed_payouts': Payout.objects.filter(status__in=(Payout.FAILED, Payout.REVERSED)).select_related('vendor').order_by('-completed_at')[:50],
    })

def login_view(request):
    if request.method == 'POST':
        form = RoleAuthenticationForm(request, data=request.POST)
        if form.is_valid():
            login(request, form.get_user())
            profile, _ = AccountProfile.objects.get_or_create(
                user=request.user,
                defaults={'role': AccountProfile.SELLER if hasattr(request.user, 'vendor_profile') else AccountProfile.BUYER},
            )
            if profile.role == AccountProfile.BUYER and not profile.onboarding_completed_at:
                request.session['buyer_tour_autostart'] = True
                return redirect('home')
            if profile.role == AccountProfile.SELLER:
                vendor, _ = Vendor.objects.get_or_create(
                    user=request.user,
                    defaults={'business_name': f"{request.user.username}'s Store"},
                )
                if not vendor.onboarding_complete:
                    return redirect('vendors:onboarding')
            next_url = request.POST.get('next') or request.GET.get('next') or 'home'
            return redirect(next_url)
    else:
        form = RoleAuthenticationForm()

    return render(request, 'login.html', {'form': form})


def signup_view(request):
    seller_signup = request.GET.get('role') == AccountProfile.SELLER or request.POST.get('signup_role') == AccountProfile.SELLER
    form_class = RoleSignupForm if seller_signup else BuyerSignupForm
    if request.method == 'POST':
        form = form_class(request.POST)
        if seller_signup:
            form.fields['role'].choices = ((AccountProfile.SELLER, 'Seller'),)
            form.fields['role'].initial = AccountProfile.SELLER
        if form.is_valid():
            user = form.save()
            login(request, user)
            if seller_signup:
                return redirect('vendors:onboarding')
            request.session['buyer_tour_autostart'] = True
            return redirect('home')
    else:
        form = form_class()
        if seller_signup:
            form.fields['role'].choices = ((AccountProfile.SELLER, 'Seller'),)
            form.fields['role'].initial = AccountProfile.SELLER

    return render(request, 'signup.html', {'form': form, 'seller_signup': seller_signup})


@require_POST
def buyer_tour_complete_view(request):
    if request.user.is_authenticated and not request.user.is_staff:
        profile, _ = AccountProfile.objects.get_or_create(
            user=request.user,
            defaults={'role': AccountProfile.SELLER if hasattr(request.user, 'vendor_profile') else AccountProfile.BUYER},
        )
        if profile.role == AccountProfile.BUYER:
            if profile.onboarding_completed_at is None:
                profile.onboarding_completed_at = timezone.now()
                profile.save(update_fields=('onboarding_completed_at',))
    request.session.pop('buyer_tour_autostart', None)
    response = HttpResponse(status=204)
    response.set_cookie('tradezone_buyer_tour', 'done', max_age=60 * 60 * 24 * 365, samesite='Lax')
    return response


def logout_view(request):
    logout(request)
    return redirect('login')


@login_required(login_url='login')
def profile_view(request):
    user_orders = Order.objects.filter(user=request.user).order_by('-created_at')
    vendor_form = None
    if not request.user.is_staff and getattr(request.user, 'account_profile', None) and request.user.account_profile.role == AccountProfile.SELLER:
        vendor, _ = Vendor.objects.get_or_create(
            user=request.user,
            defaults={'business_name': f"{request.user.username}'s Store"},
        )
        vendor_form = VendorStoreSettingsForm(
            request.POST if request.method == 'POST' else None,
            instance=vendor,
        )
        if request.method == 'POST' and vendor_form.is_valid():
            vendor_form.save()
            messages.success(request, 'Store audience setting saved.')
            return redirect('profile')
    return render(request, 'profile.html', {
        'orders': user_orders,
        'vendor_form': vendor_form,
    })


@login_required(login_url='login')
@require_POST
def report_order_issue_view(request, order_id):
    order = get_object_or_404(Order, pk=order_id, user=request.user)
    reason = request.POST.get('reason', '').strip()
    if not reason or len(reason) > 2000:
        messages.error(request, 'Describe the order issue in 1 to 2,000 characters.')
        return redirect('profile')

    vendor_orders = list(order.vendor_orders.all())
    if not vendor_orders:
        messages.error(request, 'This order has no vendor shipment to report yet. Contact TradeZone support for help.')
        return redirect('profile')

    with transaction.atomic():
        for vendor_order in vendor_orders:
            if vendor_order.disputes.filter(is_open=True).exists():
                continue
            VendorDispute.objects.create(
                vendor_order=vendor_order,
                opened_by=request.user,
                reason=reason,
            )
            vendor_order.has_open_dispute = True
            vendor_order.save(update_fields=('has_open_dispute',))
    _create_complaint(
        name=order.full_name,
        email=order.email,
        category=Complaint.ORDER_ISSUE,
        message=reason,
        order_number=order.pk,
        user=request.user,
        client_ip=_request_client_ip(request),
    )
    messages.success(request, f'Your issue for order #{order.pk} has been sent for review.')
    return redirect('profile')


@login_required(login_url='login')
def report_product_view(request, id):
    product = get_object_or_404(Product.objects.select_related('vendor'), pk=id)
    if request.method != 'POST':
        return redirect('product_detail', id=id)
    profile, _ = AccountProfile.objects.get_or_create(
        user=request.user,
        defaults={'role': AccountProfile.SELLER if hasattr(request.user, 'vendor_profile') else AccountProfile.BUYER},
    )
    if profile.role != AccountProfile.BUYER or product.vendor.user_id == request.user.id:
        messages.error(request, 'Only customers can report another vendor product.')
        return redirect('product_detail', id=id)

    reason = request.POST.get('reason', '').strip()
    if not reason:
        messages.error(request, 'Please describe why this product is in the wrong category.')
        return redirect('product_detail', id=id)
    report, created = ProductReport.objects.get_or_create(
        product=product,
        reporter=request.user,
        defaults={'reason': reason},
    )
    _create_complaint(
        name=request.user.get_full_name() or request.user.get_username(),
        email=request.user.email or settings.DEFAULT_FROM_EMAIL,
        category=Complaint.SELLER_COMPLAINT,
        message=f'Wrong category report for product "{product.name}" (#{product.pk}): {reason}',
        user=request.user,
        client_ip=_request_client_ip(request),
        product_report=report,
    )
    if created and product.reports.count() >= 3 and not product.flagged_for_review:
        product.flagged_for_review = True
        product.save(update_fields=('flagged_for_review',))
    messages.success(request, 'Thank you. Your report has been sent for review.')
    return redirect('product_detail', id=id)


@staff_member_required
def fashion_review_queue_view(request):
    if request.method == 'POST':
        product = get_object_or_404(Product.objects.select_related('vendor', 'section'), pk=request.POST.get('product_id'))
        action = request.POST.get('action')
        if product.category != 'Fashion':
            messages.error(request, 'Only Fashion products can be processed in this queue.')
            return redirect('fashion_review_queue')

        if action == 'reject':
            reason = request.POST.get('rejection_reason', '').strip()
            if not reason:
                messages.error(request, 'A rejection reason is required.')
                return redirect('fashion_review_queue')
            product.moderation_status = Product.REJECTED
            product.rejection_reason = reason
            product.flagged_for_review = True
            product.save(update_fields=('moderation_status', 'rejection_reason', 'flagged_for_review', 'updated_at'))
            ProductViolation.objects.create(
                vendor=product.vendor,
                product=product,
                reason=reason,
                issued_by=request.user,
            )
            messages.success(request, f"'{product.name}' was rejected and a vendor violation was recorded.")
        elif action == 'approve':
            section = get_object_or_404(CategoryNode, pk=request.POST.get('section_id'))
            fashion_root = _fashion_root()
            if not fashion_root or not section.is_under(fashion_root) or section.pk == fashion_root.pk:
                messages.error(request, 'Choose a valid Fashion section before approval.')
                return redirect('fashion_review_queue')
            old_section_id = product.section_id
            try:
                product.section = section
                product.moderation_status = Product.APPROVED
                product.rejection_reason = ''
                product.flagged_for_review = False
                product.full_clean()
            except ValidationError as error:
                for field_errors in error.message_dict.values():
                    for error_message in field_errors:
                        messages.error(request, error_message)
                return redirect('fashion_review_queue')
            with transaction.atomic():
                product.save()
                if old_section_id and old_section_id != section.pk:
                    ProductViolation.objects.create(
                        vendor=product.vendor,
                        product=product,
                        reason=f'Admin corrected Fashion section to {section}.',
                        issued_by=request.user,
                    )
                approved_count = Product.objects.filter(
                    vendor=product.vendor,
                    category='Fashion',
                    moderation_status=Product.APPROVED,
                    section__isnull=False,
                ).count()
                if approved_count >= 5 and not product.vendor.fashion_trust_revoked:
                    product.vendor.fashion_trusted = True
                    product.vendor.save(update_fields=('fashion_trusted',))
            messages.success(request, f"'{product.name}' was approved in {section}.")
        else:
            messages.error(request, 'Choose approve or reject.')
        return redirect('fashion_review_queue')

    fashion_root = _fashion_root()
    section_ids = _category_descendant_ids(fashion_root) if fashion_root else []
    sections = CategoryNode.objects.filter(pk__in=section_ids).exclude(pk=fashion_root.pk if fashion_root else None)
    products = Product.objects.filter(category='Fashion').filter(
        Q(moderation_status=Product.PENDING) | Q(flagged_for_review=True)
    ).select_related('vendor', 'section').prefetch_related('reports').order_by('created_at')
    return render(request, 'fashion_review.html', {'products': products, 'sections': sections})
