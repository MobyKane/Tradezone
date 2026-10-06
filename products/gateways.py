import hashlib
import hmac

import requests
from django.conf import settings
from django.core.cache import cache


class GatewayError(Exception):
    pass


class GatewayNotConfigured(GatewayError):
    pass


BANK_LIST_CACHE_KEY = 'paystack:nigeria:banks:v1'
BANK_LIST_CACHE_SECONDS = 24 * 60 * 60
FALLBACK_NIGERIAN_BANKS = (
    {'name': 'Access Bank', 'code': '044'},
    {'name': 'Ecobank Nigeria', 'code': '050'},
    {'name': 'Fidelity Bank', 'code': '070'},
    {'name': 'First Bank of Nigeria', 'code': '011'},
    {'name': 'First City Monument Bank', 'code': '214'},
    {'name': 'Guaranty Trust Bank', 'code': '058'},
    {'name': 'Jaiz Bank', 'code': '301'},
    {'name': 'Keystone Bank', 'code': '082'},
    {'name': 'Kuda', 'code': '50211'},
    {'name': 'Moniepoint MFB', 'code': '50515'},
    {'name': 'OPay', 'code': '999992'},
    {'name': 'PalmPay', 'code': '100033'},
    {'name': 'Polaris Bank', 'code': '076'},
    {'name': 'Providus Bank', 'code': '101'},
    {'name': 'Stanbic IBTC Bank', 'code': '221'},
    {'name': 'Sterling Bank', 'code': '232'},
    {'name': 'Union Bank of Nigeria', 'code': '032'},
    {'name': 'United Bank for Africa', 'code': '033'},
    {'name': 'Unity Bank', 'code': '215'},
    {'name': 'Wema Bank', 'code': '035'},
    {'name': 'Zenith Bank', 'code': '057'},
)


def _response_json(response):
    try:
        data = response.json()
    except ValueError as error:
        raise GatewayError('Payment provider returned an unreadable response.') from error
    if not response.ok:
        raise GatewayError('Payment provider request failed.')
    return data


def _paystack_request(method, path, *, params=None, payload=None):
    secret_key = getattr(settings, 'PAYSTACK_SECRET_KEY', '')
    if not secret_key:
        raise GatewayNotConfigured('Paystack is not configured.')
    try:
        response = requests.request(
            method,
            f'https://api.paystack.co/{path.lstrip("/")}',
            headers={'Authorization': f'Bearer {secret_key}', 'Content-Type': 'application/json'},
            params=params,
            json=payload,
            timeout=20,
        )
    except requests.RequestException as error:
        raise GatewayError('Could not reach Paystack. Please retry shortly.') from error
    result = _response_json(response)
    if not result.get('status'):
        raise GatewayError('Paystack rejected the request.')
    return result.get('data', {})


def initialize_paystack_payment(payment, email, callback_url):
    data = _paystack_request('POST', 'transaction/initialize', payload={
        'email': email,
        'amount': payment.amount_kobo,
        'currency': 'NGN',
        'reference': payment.reference,
        'callback_url': callback_url,
        'metadata': {'order_id': payment.order_id},
    })
    return data['authorization_url']


def verify_paystack_transaction(reference):
    return _paystack_request('GET', f'transaction/verify/{reference}')


def get_paystack_banks():
    banks = cache.get(BANK_LIST_CACHE_KEY)
    if banks is not None:
        return banks

    try:
        result = _paystack_request('GET', 'bank', params={'country': 'nigeria', 'perPage': 200})
        banks = [
            {'name': str(bank.get('name', '')).strip(), 'code': str(bank.get('code', '')).strip()}
            for bank in result
            if isinstance(bank, dict)
            and bank.get('name')
            and bank.get('code') is not None
            and str(bank.get('code')).strip()
            and str(bank.get('country', 'nigeria')).lower() in {'nigeria', 'ng'}
        ]
        if not banks:
            raise GatewayError('Paystack returned no Nigerian banks.')
        banks.sort(key=lambda bank: bank['name'].casefold())
    except (GatewayError, TypeError):
        banks = [dict(bank) for bank in FALLBACK_NIGERIAN_BANKS]

    cache.set(BANK_LIST_CACHE_KEY, banks, BANK_LIST_CACHE_SECONDS)
    return banks


def resolve_paystack_account(account_number, bank_code):
    return _paystack_request(
        'GET',
        'bank/resolve',
        params={'account_number': account_number, 'bank_code': bank_code},
    )


def create_paystack_recipient(*, account_number, bank_code, account_name, email=''):
    payload = {
        'type': 'nuban',
        'name': account_name,
        'account_number': account_number,
        'bank_code': bank_code,
        'currency': 'NGN',
        'description': 'TradeZone vendor payout',
    }
    if email:
        payload['email'] = email
    return _paystack_request('POST', 'transferrecipient', payload=payload)


def initiate_paystack_transfer(payout, vendor):
    return _paystack_request('POST', 'transfer', payload={
        'source': 'balance',
        'amount': payout.amount_kobo,
        'recipient': vendor.paystack_recipient_code,
        'reason': f'TradeZone vendor payout {payout.reference}',
        'currency': 'NGN',
        'reference': payout.reference.lower(),
    })


def finalize_paystack_transfer(transfer_code, otp):
    return _paystack_request('POST', 'transfer/finalize_transfer', payload={
        'transfer_code': transfer_code,
        'otp': otp,
    })


