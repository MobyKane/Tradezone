from unittest.mock import patch
from importlib import import_module
from unittest.mock import Mock

from django.contrib.auth import get_user_model
from django.urls import reverse
from django.test import TestCase
from django.core.cache import cache

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

	def test_completion_migration_preserves_legacy_completed_profiles(self):
		migration = import_module('vendors.migrations.0007_accountprofile_onboarding_completed_at')
		profiles = Mock()
		apps = Mock()
		apps.get_model.return_value.objects = profiles

		migration.preserve_completed_tours(apps, None)
		profiles.filter.assert_called_once_with(tour_completed=True)
		self.assertIsNotNone(profiles.filter.return_value.update.call_args.kwargs['onboarding_completed_at'])

		profiles.reset_mock()
		migration.restore_completed_tours(apps, None)
		profiles.filter.assert_called_once_with(onboarding_completed_at__isnull=False)
		profiles.filter.return_value.update.assert_called_once_with(tour_completed=True)

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
