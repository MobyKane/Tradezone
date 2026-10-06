from vendors.models import AccountProfile


def account_role(request):
    user = request.user
    if not user.is_authenticated:
        return {
            'can_sell': True,
            'account_role': None,
            'buyer_tour_autostart': False,
            'buyer_tour_prompt': request.COOKIES.get('tradezone_buyer_tour') != 'done',
        }
    if user.is_staff:
        return {'can_sell': True, 'account_role': AccountProfile.SELLER, 'buyer_tour_autostart': False, 'buyer_tour_prompt': False}

    profile, _ = AccountProfile.objects.get_or_create(
        user=user,
        defaults={
            'role': AccountProfile.SELLER if hasattr(user, 'vendor_profile') else AccountProfile.BUYER,
        },
    )
    buyer = profile.role == AccountProfile.BUYER
    return {
        'can_sell': not buyer,
        'account_role': profile.role,
        'buyer_tour_autostart': bool(buyer and not profile.onboarding_completed_at and request.session.get('buyer_tour_autostart')),
        'buyer_tour_prompt': bool(buyer and not profile.onboarding_completed_at),
    }
