from django import template

register = template.Library()


@register.filter
def naira(kobo):
    try:
        amount = int(kobo)
    except (TypeError, ValueError):
        return '₦0.00'
    naira_amount, fractional_kobo = divmod(amount, 100)
    return f'₦{naira_amount:,}.{fractional_kobo:02d}'


@register.filter
def multiply(value, multiplier):
    try:
        return int(value) * int(multiplier)
    except (TypeError, ValueError):
        return 0
