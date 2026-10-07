from unittest.mock import call, patch
from importlib import import_module
from unittest.mock import Mock

from django.contrib.auth import get_user_model
from django.urls import reverse
from django.test import TestCase
from django.core.cache import cache
from django.utils import timezone

from .models import AccountProfile, Vendor
from products import gateways


class VendorOnboardingTests(TestCase):
	def setUp(self):
		cache.clear()
		self.user = get_user_model().objects.create_user(
			username='wizard_vendor',
			email='vendor@example.test',
			password='Test123!',
		)
		AccountProfile.objects.create(user=self.user, role=AccountProfile.SELLER)
		self.client.force_login(self.user)
		self.url = reverse('vendors:onboarding')

	def test_existing_users_and_vendors_are_marked_complete_by_migration(self):
		migration = import_module('vendors.migrations.0008_complete_existing_onboarding')
		profiles = Mock()
		vendors = Mock()
		users = Mock()
		profile_model = Mock(objects=profiles)
		apps = Mock()
		apps.get_model.side_effect = [
			profile_model,
			Mock(objects=vendors),
			Mock(objects=users),
		]
		vendors.values_list.return_value = [10]
		missing_user_ids = Mock()
		missing_user_ids.iterator.return_value = iter([10, 11])
		users.exclude.return_value.values_list.return_value = missing_user_ids

		migration.complete_existing_onboarding(apps, None)

		self.assertEqual(apps.get_model.call_args_list, [
			call('vendors', 'AccountProfile'),
			call('vendors', 'Vendor'),
			call('auth', 'User'),
		])
		profiles.update.assert_called_once()
		self.assertIsNotNone(profiles.update.call_args.kwargs['onboarding_completed_at'])
		vendors.update.assert_called_once_with(onboarding_complete=True)
		created_profiles = profile_model.call_args_list
		self.assertEqual([(profile.kwargs['user_id'], profile.kwargs['role']) for profile in created_profiles], [
			(10, 'seller'),
			(11, 'buyer'),
		])
		self.assertTrue(all(
			profile.kwargs['onboarding_completed_at'] is not None
			for profile in created_profiles
		))

	def test_wizard_saves_each_server_step_and_resolves_bank_before_submit(self):
		response = self.client.get(self.url)
		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'Step 1 of 5')

		response = self.client.post(self.url, {
			'action': 'next',
			'first_name': 'Amina',
			'last_name': 'Seller',
			'email': 'amina@example.test',
		})
		self.assertRedirects(response, self.url)
		vendor = Vendor.objects.get(user=self.user)
		self.assertEqual(vendor.onboarding_step, 2)
		self.user.refresh_from_db()
		self.assertEqual(self.user.first_name, 'Amina')

		response = self.client.post(self.url, {
			'action': 'next',
			'business_name': 'Amina Home Store',
			'business_description': 'Home goods',
			'fashion_audience': 'women',
		})
		self.assertRedirects(response, self.url)
		vendor.refresh_from_db()
		self.assertEqual(vendor.onboarding_step, 3)

		banks = [{'name': 'Test Bank', 'code': '001'}]
		with patch('vendors.views.gateways.get_paystack_banks', return_value=banks), \
				patch('vendors.views.gateways.resolve_paystack_account', return_value={
			'account_number': '0123456789', 'account_name': 'Amina Seller',
		}) as resolve, patch('vendors.views.gateways.create_paystack_recipient', return_value={
			'recipient_code': 'RCP_TEST',
		}) as create_recipient:
			self.client.post(self.url, {'action': 'resolve', 'account_number': '0123456789', 'bank_code': '001'})
			resolve.assert_called_once()
			create_recipient.assert_not_called()
			response = self.client.post(self.url, {
				'action': 'confirm_bank',
				'confirmed_account_name': 'Amina Seller',
			})
			create_recipient.assert_called_once()
			self.assertRedirects(response, self.url)

		vendor.refresh_from_db()
		self.assertEqual(vendor.onboarding_step, 4)
		response = self.client.post(self.url, {'action': 'next', 'accept_vendor_terms': 'yes'})
		self.assertRedirects(response, self.url)
		vendor.refresh_from_db()
		self.assertEqual(vendor.onboarding_step, 5)
		self.assertIsNotNone(vendor.vendor_terms_accepted_at)

		response = self.client.get(self.url)
		self.assertContains(response, 'Step 5 of 5')
		response = self.client.post(self.url, {'action': 'submit'})
		self.assertRedirects(response, reverse('vendor_dashboard'))
		vendor.refresh_from_db()
		self.assertTrue(vendor.onboarding_complete)

	def test_account_step_validation_keeps_progress_on_current_step(self):
		response = self.client.post(self.url, {
			'action': 'next',
			'first_name': '',
			'last_name': 'Seller',
			'email': 'not-an-email',
		})
		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'Enter your first name.')
		self.assertEqual(Vendor.objects.get(user=self.user).onboarding_step, 1)

	def test_seller_signup_enters_wizard_without_duplicate_policy_checkbox(self):
		response = self.client.get(reverse('signup') + '?role=seller')
		self.assertEqual(response.status_code, 200)
		self.assertNotContains(response, 'accept_vendor_terms')
		self.client.logout()
		response = self.client.post(reverse('signup') + '?role=seller', {
			'signup_role': 'seller',
			'username': 'new_wizard_seller',
			'role': AccountProfile.SELLER,
			'password1': 'Strong!Vendor2026',
			'password2': 'Strong!Vendor2026',
		})
		self.assertRedirects(response, reverse('vendors:onboarding'))
		self.assertFalse(Vendor.objects.get(user__username='new_wizard_seller').onboarding_complete)

	def test_policy_step_requires_explicit_acceptance(self):
		vendor = Vendor.objects.create(
			user=self.user,
			business_name='Policy Store',
			onboarding_step=4,
			paystack_recipient_code='RCP_READY',
		)
		response = self.client.post(self.url, {'action': 'next'})
		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'Accept the vendor policy to continue.')
		vendor.refresh_from_db()
		self.assertEqual(vendor.onboarding_step, 4)

	def test_seller_login_resumes_incomplete_vendor_wizard(self):
		vendor = Vendor.objects.create(
			user=self.user,
			business_name='Resume Store',
			onboarding_step=3,
		)
		self.client.logout()
		response = self.client.post(reverse('login'), {
			'username': self.user.username,
			'password': 'Test123!',
			'role': AccountProfile.SELLER,
		})
		self.assertRedirects(response, self.url)
		vendor.refresh_from_db()
		self.assertEqual(vendor.onboarding_step, 3)
		response = self.client.get(self.url)
		self.assertContains(response, 'Step 3 of 5')

	def test_submitted_vendor_cannot_reopen_wizard_or_see_its_steps(self):
		vendor = Vendor.objects.create(
			user=self.user,
			business_name='Submitted Store',
			onboarding_step=5,
			paystack_recipient_code='RCP_READY',
			vendor_terms_accepted_at=timezone.now(),
		)

		response = self.client.post(self.url, {'action': 'submit'})

		self.assertRedirects(response, reverse('vendor_dashboard'))
		vendor.refresh_from_db()
		self.assertTrue(vendor.onboarding_complete)
		self.user.account_profile.refresh_from_db()
		self.assertIsNone(self.user.account_profile.onboarding_completed_at)

		response = self.client.get(self.url, follow=True)
		self.assertRedirects(response, reverse('vendor_dashboard'))
		self.assertNotContains(response, 'Set up your vendor account')
		self.assertNotContains(response, 'Step 5 of 5')
		self.assertNotContains(response, 'Submit vendor profile')

	def test_buyer_onboarding_flag_does_not_complete_vendor_wizard(self):
		buyer = get_user_model().objects.create_user(
			username='independent_buyer',
			password='Test123!',
		)
		profile = AccountProfile.objects.create(user=buyer, role=AccountProfile.BUYER)
		vendor = Vendor.objects.create(user=buyer, business_name='Independent Draft')
		self.client.force_login(buyer)

		response = self.client.get(reverse('home'))

		self.assertEqual(response.status_code, 200)
		profile.refresh_from_db()
		vendor.refresh_from_db()
		self.assertIsNotNone(profile.onboarding_completed_at)
		self.assertFalse(vendor.onboarding_complete)

	def test_account_profile_onboarding_timestamp_is_editable_in_admin(self):
		admin_user = get_user_model().objects.create_superuser(
			username='onboarding_admin',
			email='admin@example.test',
			password='Test123!',
		)
		profile = self.user.account_profile
		self.client.force_login(admin_user)

		response = self.client.get(reverse(
			'admin:vendors_accountprofile_change',
			args=(profile.pk,),
		))

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'name="onboarding_completed_at_0"')

	def test_bank_list_uses_paystack_cache_and_fallback(self):
		with patch('products.gateways.cache.set', wraps=cache.set) as cache_set, \
				patch('products.gateways._paystack_request', return_value=[
			{'name': 'Sample Commercial Bank', 'code': '123'},
			{'name': 'Sample MFB', 'code': '456', 'country': 'Ghana'},
		]) as paystack_request:
			banks = gateways.get_paystack_banks()
			self.assertEqual(banks, [{'name': 'Sample Commercial Bank', 'code': '123'}])
			self.assertEqual(gateways.get_paystack_banks(), banks)
			paystack_request.assert_called_once_with(
				'GET', 'bank', params={'country': 'nigeria', 'perPage': 200}
			)
			cache_set.assert_called_once_with(
				gateways.BANK_LIST_CACHE_KEY,
				banks,
				gateways.BANK_LIST_CACHE_SECONDS,
			)
			self.assertEqual(gateways.BANK_LIST_CACHE_SECONDS, 24 * 60 * 60)

		cache.clear()
		with patch('products.gateways._paystack_request', side_effect=gateways.GatewayError('offline')):
			banks = gateways.get_paystack_banks()
		self.assertTrue(any(bank['name'] == 'OPay' for bank in banks))
		self.assertTrue(any(bank['name'] == 'Moniepoint MFB' for bank in banks))
		self.assertTrue(any(bank['name'] == 'PalmPay' for bank in banks))
		self.assertTrue(any(bank['name'] == 'Kuda' for bank in banks))
		self.assertTrue(any(bank['name'] == 'Access Bank' for bank in banks))

	def test_bank_picker_renders_names_and_rejects_unknown_codes(self):
		banks = [{'name': 'Test Commercial Bank', 'code': '321'}]
		vendor = Vendor.objects.create(user=self.user, business_name='Picker Store', onboarding_step=3)
		with patch('vendors.views.gateways.get_paystack_banks', return_value=banks):
			response = self.client.get(self.url)
			self.assertContains(response, 'Search banks')
			self.assertContains(response, 'id="bank-select"')
			self.assertContains(response, 'Test Commercial Bank')
			self.assertNotContains(response, 'Bank code')
			with patch('vendors.views.gateways.resolve_paystack_account') as resolve:
				response = self.client.post(self.url, {
					'action': 'resolve',
					'account_number': '0123456789',
					'bank_code': '999999',
				})
				self.assertEqual(response.status_code, 200)
				resolve.assert_not_called()
		vendor.refresh_from_db()
		self.assertEqual(vendor.onboarding_step, 3)
