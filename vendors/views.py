from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from products import gateways
from .models import AccountProfile, Vendor


VENDOR_TERMS = (
	'Eligible earnings are held for five days after delivery to allow time for returns and disputes. '
	'Payouts are sent to the verified bank account you provide. Repeated listing or policy violations '
	'may result in listing removal or account restrictions.'
)


@login_required(login_url='login')
def onboarding_view(request):
	profile, _ = AccountProfile.objects.get_or_create(
		user=request.user,
		defaults={'role': AccountProfile.SELLER if hasattr(request.user, 'vendor_profile') else AccountProfile.BUYER},
	)
	vendor, _ = Vendor.objects.get_or_create(
		user=request.user,
		defaults={'business_name': f"{request.user.username}'s Store"},
	)
	if vendor.onboarding_complete:
		return redirect('vendor_dashboard')

	step = min(max(vendor.onboarding_step, 1), 5)
	action = request.POST.get('action') if request.method == 'POST' else None
	errors = {}
	resolved_account = request.session.get('onboarding_resolved_bank')
	banks = gateways.get_paystack_banks() if step == 3 else []
	selected_bank_code = request.POST.get('bank_code', vendor.bank_code or '') if step == 3 else vendor.bank_code or ''

	if request.method == 'POST' and action == 'back':
		vendor.onboarding_step = max(1, step - 1)
		vendor.save(update_fields=('onboarding_step',))
		return redirect('vendors:onboarding')

	if request.method == 'POST' and step == 1 and action == 'next':
		first_name = request.POST.get('first_name', '').strip()
		last_name = request.POST.get('last_name', '').strip()
		email = request.POST.get('email', '').strip()
		if not first_name:
			errors['first_name'] = 'Enter your first name.'
		if not last_name:
			errors['last_name'] = 'Enter your last name.'
		try:
			validate_email(email)
		except ValidationError:
			errors['email'] = 'Enter a valid email address.'
		if not errors:
			user_model = get_user_model()
			if user_model.objects.exclude(pk=request.user.pk).filter(email__iexact=email).exists():
				errors['email'] = 'An account already uses this email address.'
			else:
				request.user.first_name = first_name
				request.user.last_name = last_name
				request.user.email = email
				request.user.save(update_fields=('first_name', 'last_name', 'email'))
				profile.role = AccountProfile.SELLER
				profile.save(update_fields=('role',))
				vendor.onboarding_step = 2
				vendor.save(update_fields=('onboarding_step',))
				return redirect('vendors:onboarding')

	elif request.method == 'POST' and step == 2 and action == 'next':
		business_name = request.POST.get('business_name', '').strip()
		business_description = request.POST.get('business_description', '').strip()
		audience = request.POST.get('fashion_audience', '')
		if not business_name:
			errors['business_name'] = 'Enter a store name.'
		if audience not in {'', 'men', 'women', 'both'}:
			errors['fashion_audience'] = 'Choose a valid store audience.'
		if not errors:
			vendor.business_name = business_name
			vendor.business_description = business_description
			vendor.fashion_audience = audience
			vendor.onboarding_step = 3
			vendor.save(update_fields=('business_name', 'business_description', 'fashion_audience', 'onboarding_step'))
			return redirect('vendors:onboarding')

	elif request.method == 'POST' and step == 3 and action == 'resolve':
		account_number = request.POST.get('account_number', '').strip()
		bank_code = request.POST.get('bank_code', '').strip()
		if not account_number.isdigit() or not 8 <= len(account_number) <= 20 or bank_code not in {bank['code'] for bank in banks}:
			errors['bank'] = 'Choose a bank and enter a valid account number.'
		else:
			try:
				result = gateways.resolve_paystack_account(account_number, bank_code)
			except gateways.GatewayError as error:
				errors['bank'] = str(error)
			else:
				resolved_account = {
					'account_number': result.get('account_number', account_number),
					'bank_code': bank_code,
					'account_name': result.get('account_name', ''),
				}
				request.session['onboarding_resolved_bank'] = resolved_account

	elif request.method == 'POST' and step == 3 and action == 'confirm_bank':
		resolved_account = request.session.get('onboarding_resolved_bank')
		if not resolved_account or request.POST.get('confirmed_account_name', '').strip() != resolved_account.get('account_name'):
			errors['bank'] = 'Confirm the exact account name returned by Paystack.'
		else:
			try:
				recipient = gateways.create_paystack_recipient(
					account_number=resolved_account['account_number'],
					bank_code=resolved_account['bank_code'],
					account_name=resolved_account['account_name'],
					email=request.user.email,
				)
			except gateways.GatewayError as error:
				errors['bank'] = str(error)
			else:
				vendor.bank_account_number = resolved_account['account_number']
				vendor.bank_code = resolved_account['bank_code']
				vendor.bank_account_name = resolved_account['account_name']
				vendor.paystack_recipient_code = recipient['recipient_code']
				vendor.onboarding_step = 4
				vendor.save(update_fields=(
					'bank_account_number', 'bank_code', 'bank_account_name',
					'paystack_recipient_code', 'onboarding_step',
				))
				request.session.pop('onboarding_resolved_bank', None)
				return redirect('vendors:onboarding')

	elif request.method == 'POST' and step == 4 and action == 'next':
		if request.POST.get('accept_vendor_terms') != 'yes':
			errors['accept_vendor_terms'] = 'Accept the vendor policy to continue.'
		else:
			vendor.vendor_terms_accepted_at = timezone.now()
			vendor.onboarding_step = 5
			vendor.save(update_fields=('vendor_terms_accepted_at', 'onboarding_step'))
			return redirect('vendors:onboarding')

	elif request.method == 'POST' and step == 5 and action == 'submit':
		if not vendor.business_name or not vendor.paystack_recipient_code or not vendor.vendor_terms_accepted_at:
			messages.error(request, 'Complete all onboarding steps before submitting.')
			vendor.onboarding_step = 2 if not vendor.business_name else 3 if not vendor.paystack_recipient_code else 4
			vendor.save(update_fields=('onboarding_step',))
			return redirect('vendors:onboarding')
		vendor.onboarding_complete = True
		vendor.save(update_fields=('onboarding_complete',))
		messages.success(request, 'Your vendor profile is ready.')
		return redirect('vendor_dashboard')

	return render(request, 'vendor_onboarding.html', {
		'vendor': vendor,
		'step': step,
		'resolved_account': resolved_account,
		'errors': errors,
		'terms': VENDOR_TERMS,
		'banks': banks,
		'selected_bank_code': selected_bank_code,
	})

# Create your views here.
