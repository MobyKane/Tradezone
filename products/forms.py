from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm
from django.core.exceptions import ValidationError
from django.contrib.auth.password_validation import validate_password
from django.utils.text import slugify

from vendors.models import AccountProfile, Vendor
from .models import CategoryNode, Complaint, Product


ROLE_CHOICES = AccountProfile.ROLE_CHOICES


class BuyerSignupForm(forms.Form):
    name = forms.CharField(max_length=150, label='Your name', widget=forms.TextInput(attrs={
        'autocomplete': 'name', 'class': 'w-full rounded-lg border border-outline-variant bg-surface px-3 py-2.5 text-on-surface',
    }))
    email = forms.EmailField(widget=forms.EmailInput(attrs={
        'autocomplete': 'email', 'class': 'w-full rounded-lg border border-outline-variant bg-surface px-3 py-2.5 text-on-surface',
    }))
    password = forms.CharField(widget=forms.PasswordInput(attrs={
        'autocomplete': 'new-password', 'class': 'w-full rounded-lg border border-outline-variant bg-surface px-3 py-2.5 text-on-surface',
    }), strip=False)

    def clean_email(self):
        email = self.cleaned_data['email'].strip()
        user_model = get_user_model()
        if user_model.objects.filter(email__iexact=email).exists():
            raise ValidationError('An account already uses this email address.')
        return email

    def clean_password(self):
        password = self.cleaned_data['password']
        validate_password(password)
        return password

    def save(self):
        user_model = get_user_model()
        name = self.cleaned_data['name'].strip()
        email = self.cleaned_data['email']
        base_username = slugify(email.split('@', 1)[0])[:120] or 'buyer'
        username = base_username
        suffix = 1
        while user_model.objects.filter(username=username).exists():
            username = f'{base_username[:110]}-{suffix}'
            suffix += 1
        name_parts = name.split(maxsplit=1)
        user = user_model.objects.create_user(
            username=username,
            email=email,
            password=self.cleaned_data['password'],
            first_name=name_parts[0],
            last_name=name_parts[1] if len(name_parts) > 1 else '',
        )
        AccountProfile.objects.create(user=user, role=AccountProfile.BUYER)
        return user


class RoleSignupForm(UserCreationForm):
    role = forms.ChoiceField(choices=ROLE_CHOICES, label='I want to use TradeZone as a')

    class Meta(UserCreationForm.Meta):
        fields = ('username', 'role', 'password1', 'password2')

    def save(self, commit=True):
        user = super().save(commit=commit)
        if commit:
            AccountProfile.objects.create(user=user, role=self.cleaned_data['role'])
        return user


class RoleAuthenticationForm(AuthenticationForm):
    role = forms.ChoiceField(choices=ROLE_CHOICES, label='Sign in as')

    def confirm_login_allowed(self, user):
        super().confirm_login_allowed(user)
        profile, _ = AccountProfile.objects.get_or_create(
            user=user,
            defaults={
                'role': AccountProfile.SELLER if hasattr(user, 'vendor_profile') else AccountProfile.BUYER,
            },
        )
        if not user.is_staff and profile.role != self.cleaned_data['role']:
            raise ValidationError(
                f"This account is registered as a {profile.get_role_display().lower()}. Choose that role to sign in.",
                code='invalid_role',
            )


class ProductListingForm(forms.Form):
    name = forms.CharField(max_length=255)
    description = forms.CharField(widget=forms.Textarea)
    price = forms.DecimalField(max_digits=10, decimal_places=2, min_value=0.01)
    stock = forms.IntegerField(min_value=1, initial=10)
    category = forms.ChoiceField(choices=Product.CATEGORY_CHOICES)
    section = forms.ModelChoiceField(queryset=CategoryNode.objects.none(), required=False)
    image = forms.ImageField(required=False)
    unit_of_sale = forms.CharField(max_length=40, required=False)
    quantity_per_carton = forms.IntegerField(min_value=1, required=False)
    dimensions = forms.CharField(max_length=120, required=False)
    material_finish = forms.CharField(max_length=120, required=False)
    country_of_origin = forms.CharField(max_length=80, required=False)

    def __init__(self, *args, vendor=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.vendor = vendor
        category = self.data.get(self.add_prefix('category')) if self.is_bound else self.initial.get('category')
        self.fields['section'].queryset = CategoryNode.objects.filter(
            parent__slug__in=('fashion', 'building-materials'),
        )
        self.fields['section'].label_from_instance = self.section_label

    @staticmethod
    def section_label(section):
        names = [section.name]
        parent = section.parent
        while parent and parent.parent:
            names.append(parent.name)
            parent = parent.parent
        return ' > '.join(reversed(names))

    def clean(self):
        cleaned_data = super().clean()
        category = cleaned_data.get('category')
        section = cleaned_data.get('section')
        root_slug = {'Fashion': 'fashion', 'Building Materials': 'building-materials'}.get(category)
        if root_slug:
            section_root = CategoryNode.objects.filter(parent__isnull=True, slug=root_slug).first()
            if not section and category == 'Building Materials':
                self.add_error('section', f'Choose a {category} section.')
            elif section and (not section_root or not section.is_under(section_root) or section.pk == section_root.pk):
                self.add_error('section', f'Choose a valid {category} section.')
            elif category == 'Fashion' and section and self.vendor and self.vendor.fashion_audience in {'men', 'women'}:
                first_section = section
                while first_section.parent_id and first_section.parent_id != section_root.pk:
                    first_section = first_section.parent
                allowed = {'Unisex', 'Men' if self.vendor.fashion_audience == 'men' else 'Women'}
                if first_section.name not in allowed:
                    self.add_error('section', f"This store can only list {self.vendor.get_fashion_audience_display()} or Unisex products.")
        elif section:
            self.add_error('section', 'A section may only be selected for Fashion or Building Materials products.')
        return cleaned_data


class ComplaintForm(forms.Form):
    name = forms.CharField(max_length=150)
    email = forms.EmailField(max_length=254)
    order_number = forms.CharField(max_length=64, required=False)
    category = forms.ChoiceField(choices=Complaint.CATEGORY_CHOICES)
    message = forms.CharField(max_length=5000, widget=forms.Textarea)
    website = forms.CharField(required=False, max_length=200)

    def clean_order_number(self):
        return self.cleaned_data['order_number'].strip()

    def clean_message(self):
        message = self.cleaned_data['message'].strip()
        if not message:
            raise ValidationError('Enter a message describing how we can help.')
        return message


class VendorStoreSettingsForm(forms.ModelForm):
    class Meta:
        model = Vendor
        fields = ('fashion_audience',)
        labels = {'fashion_audience': 'This store sells'}
