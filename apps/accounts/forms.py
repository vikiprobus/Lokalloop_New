import re
from django import forms
from django.contrib.auth.models import User
from django.utils import timezone
from .models import Profile, Role, BuyerProfile, SellerProfile, DeletionReasonCategory, AccountDeletionLog
from apps.shops.models import ShopCategory, ShopSubcategory


from .shopper_utils import SECURITY_QUESTIONS, validate_strong_password
from services.captcha_service import validate_captcha
from services.security_validator import (
    validate_indian_mobile,
    validate_indian_pan,
    validate_indian_gstin,
    validate_fssai_license,
    detect_malicious_payload,
    SecurityValidator
)


MONTH_CHOICES = [
    (1, '01 - January'),
    (2, '02 - February'),
    (3, '03 - March'),
    (4, '04 - April'),
    (5, '05 - May'),
    (6, '06 - June'),
    (7, '07 - July'),
    (8, '08 - August'),
    (9, '09 - September'),
    (10, '10 - October'),
    (11, '11 - November'),
    (12, '12 - December'),
]

GENDER_CHOICES = [
    ('', '-- Select Gender --'),
    ('MALE', 'Male'),
    ('FEMALE', 'Female'),
    ('OTHER', 'Other'),
]


class CaptchaFieldMixin(forms.Form):
    captcha_token = forms.CharField(
        required=False,
        widget=forms.HiddenInput(attrs={'id': 'id_captcha_token'})
    )
    captcha_answer = forms.CharField(
        required=False,
        label="CAPTCHA",
        widget=forms.HiddenInput(attrs={
            'id': 'id_captcha_answer'
        })
    )

    def clean(self):
        cleaned_data = super().clean()
        from django.conf import settings

        if getattr(settings, 'CAPTCHA_ENFORCE', True):
            token = cleaned_data.get('captcha_token')
            answer = cleaned_data.get('captcha_answer')

            if not token or not answer:
                self.add_error('captcha_answer', "Please complete the CAPTCHA slider to proceed.")
                return cleaned_data

            is_valid, error_msg = validate_captcha(token, answer)
            if not is_valid:
                self.add_error('captcha_answer', error_msg)

        return cleaned_data


class AnonymousBuyerRegistrationForm(CaptchaFieldMixin, forms.Form):
    first_name = forms.CharField(
        max_length=50,
        required=False,
        label="First Name",
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'id': 'id_first_name',
            'placeholder': 'Enter your first name',
            'autocomplete': 'given-name'
        })
    )
    gender = forms.ChoiceField(
        choices=GENDER_CHOICES,
        required=False,
        label="Gender",
        widget=forms.Select(attrs={
            'class': 'form-select',
            'id': 'id_gender'
        })
    )
    birth_month = forms.TypedChoiceField(
        choices=[('', '-- Select Month --')] + MONTH_CHOICES,
        coerce=lambda x: int(x) if x else None,
        required=False,
        label="Birth Month",
        widget=forms.Select(attrs={
            'class': 'form-select',
            'id': 'id_birth_month'
        })
    )
    birth_year = forms.IntegerField(
        required=False,
        min_value=1920,
        max_value=2026,
        label="Birth Year",
        widget=forms.NumberInput(attrs={
            'class': 'form-control',
            'id': 'id_birth_year',
            'placeholder': 'e.g. 1998'
        })
    )
    age = forms.IntegerField(
        required=False,
        widget=forms.HiddenInput(attrs={'id': 'id_age'})
    )
    state = forms.CharField(max_length=100, initial="Maharashtra", widget=forms.HiddenInput())
    district = forms.CharField(max_length=100, initial="Pune", widget=forms.HiddenInput())
    shopper_id = forms.CharField(
        max_length=30,
        required=False,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'readonly': 'readonly',
            'id': 'id_shopper_id',
            'style': 'font-weight: 800; font-family: monospace; letter-spacing: 0.1em; font-size: 1.25rem; color: var(--brand-saffron-600); background-color: var(--bg-subtle); text-align: center;'
        })
    )
    password = forms.CharField(widget=forms.PasswordInput(attrs={
        'class': 'form-control',
        'id': 'id_password',
        'placeholder': 'Create strong password',
        'autocomplete': 'new-password'
    }))
    confirm_password = forms.CharField(
        required=True,
        widget=forms.PasswordInput(attrs={
            'class': 'form-control',
            'id': 'id_confirm_password',
            'placeholder': 'Confirm strong password',
            'autocomplete': 'new-password'
        })
    )
    security_question = forms.ChoiceField(
        choices=SECURITY_QUESTIONS,
        required=False,
        widget=forms.Select(attrs={'class': 'form-select', 'id': 'id_security_question'})
    )
    security_answer = forms.CharField(
        max_length=200,
        required=False,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'id': 'id_security_answer',
            'placeholder': 'Your secret answer'
        })
    )
    # Anti-bot honeypot field
    website = forms.CharField(required=False, widget=forms.TextInput(attrs={'style': 'display:none !important;', 'tabindex': '-1', 'autocomplete': 'off'}))

    def clean_website(self):
        val = self.cleaned_data.get('website')
        if val:
            raise forms.ValidationError("Spam bot detected.")
        return val

    def clean_first_name(self):
        import re
        from .shopper_utils import sanitize_user_input
        name = self.cleaned_data.get('first_name', '').strip()
        if name:
            name = sanitize_user_input(name, max_length=30)
            if not re.match(r'^[A-Za-z\s\-]+$', name):
                raise forms.ValidationError("First name should only contain letters and spaces.")
        return name or ''

    def clean_gender(self):
        g = self.cleaned_data.get('gender', '').strip().upper()
        if g and g not in {'MALE', 'FEMALE', 'OTHER'}:
            raise forms.ValidationError("Please select a valid gender option.")
        return g


    def clean_password(self):
        password = self.cleaned_data.get('password')
        is_valid, msg = validate_strong_password(password)
        if not is_valid:
            raise forms.ValidationError(msg)
        return password

    def clean(self):
        cleaned_data = super().clean()
        password = cleaned_data.get('password')
        confirm = cleaned_data.get('confirm_password')
        if password and not confirm:
            self.add_error('confirm_password', "Please confirm your password.")
        elif confirm and password and password != confirm:
            self.add_error('confirm_password', "Passwords do not match.")
        return cleaned_data

    def clean_security_answer(self):
        ans = self.cleaned_data.get('security_answer', '').strip()
        if ans and len(ans) < 2:
            raise forms.ValidationError("Please provide a memorable answer with at least 2 characters.")
        return ans


class BuyerRecoveryLookupForm(CaptchaFieldMixin, forms.Form):
    shopper_id = forms.CharField(
        max_length=100,
        label="Public Unique ID",
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'id': 'id_shopper_id',
            'placeholder': 'e.g. LQ7M-8K2R-5D9X',
            'autocomplete': 'username',
            'required': True
        })
    )
    # Anti-bot honeypot
    website = forms.CharField(required=False, widget=forms.TextInput(attrs={'style': 'display:none !important;', 'tabindex': '-1', 'autocomplete': 'off'}))

    def clean_website(self):
        val = self.cleaned_data.get('website')
        if val:
            raise forms.ValidationError("Spam bot detected.")
        return val

    def clean_shopper_id(self):
        sid = self.cleaned_data.get('shopper_id', '').strip()
        if not sid:
            raise forms.ValidationError("Please enter your Shopper ID, email, or mobile number.")
        is_malicious, reason = detect_malicious_payload(sid)
        if is_malicious:
            raise forms.ValidationError("Invalid characters detected in account identifier.")
        return sid


class BuyerRecoveryResetForm(forms.Form):
    security_answer = forms.CharField(
        max_length=200,
        label="Secret Recovery Answer",
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'Enter your secret recovery answer'
        })
    )
    new_password = forms.CharField(
        label="New Strong Password",
        widget=forms.PasswordInput(attrs={
            'class': 'form-control',
            'id': 'id_new_password',
            'placeholder': 'Create new strong password',
            'autocomplete': 'new-password'
        })
    )
    # Anti-bot honeypot
    website = forms.CharField(required=False, widget=forms.TextInput(attrs={'style': 'display:none !important;', 'tabindex': '-1', 'autocomplete': 'off'}))

    def clean_website(self):
        val = self.cleaned_data.get('website')
        if val:
            raise forms.ValidationError("Spam bot detected.")
        return val

    def clean_new_password(self):
        password = self.cleaned_data.get('new_password')
        is_valid, msg = validate_strong_password(password)
        if not is_valid:
            raise forms.ValidationError(msg)
        return password


class BuyerRegistrationForm(CaptchaFieldMixin, forms.Form):
    first_name = forms.CharField(max_length=50, label="First Name", widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'First Name'}))
    last_name = forms.CharField(max_length=50, label="Last Name", widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Last Name'}))
    gender = forms.ChoiceField(choices=GENDER_CHOICES, required=False, label="Gender", widget=forms.Select(attrs={'class': 'form-select'}))
    email = forms.EmailField(label="Email Address", widget=forms.EmailInput(attrs={'class': 'form-control', 'placeholder': 'Enter your email'}))
    phone = forms.CharField(max_length=15, label="Mobile Number", widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Enter mobile number'}))
    city = forms.CharField(max_length=100, label="City", initial="Bengaluru", widget=forms.TextInput(attrs={'class': 'form-control'}))
    password = forms.CharField(label="Password", widget=forms.PasswordInput(attrs={'class': 'form-control', 'placeholder': 'Min 8 characters', 'autocomplete': 'new-password'}))
    # Anti-bot honeypot field (hidden via CSS; must remain empty)
    website = forms.CharField(required=False, widget=forms.TextInput(attrs={'style': 'display:none !important;', 'tabindex': '-1', 'autocomplete': 'off'}))

    def clean_website(self):
        val = self.cleaned_data.get('website')
        if val:
            raise forms.ValidationError("Spam bot detected.")
        return val

    def clean_email(self):
        email = self.cleaned_data.get('email').lower()
        if User.objects.filter(username=email).exists() or User.objects.filter(email=email).exists():
            raise forms.ValidationError("An account with this email already exists.")
        return email


class SellerRegistrationForm(CaptchaFieldMixin, forms.Form):
    # STEP 1: Business Profile & Category
    business_name = forms.CharField(
        max_length=200,
        label="Shop / Business Name",
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'id': 'id_business_name',
            'placeholder': 'e.g. Pune Supreme Supermarket',
            'autocomplete': 'organization'
        })
    )
    category = forms.ModelChoiceField(
        queryset=ShopCategory.objects.filter(is_active=True),
        label="Business Category",
        empty_label="-- Select Business Category --",
        widget=forms.Select(attrs={
            'class': 'form-select',
            'id': 'id_category',
            'onchange': 'onCategoryChange(this.value)'
        })
    )
    subcategory = forms.ModelChoiceField(
        queryset=ShopSubcategory.objects.filter(is_active=True),
        required=False,
        label="Specific Subcategory",
        empty_label="-- Select Subcategory (Optional) --",
        widget=forms.Select(attrs={
            'class': 'form-select',
            'id': 'id_subcategory'
        })
    )

    # STEP 2: Outlet Location & Hours
    address = forms.CharField(
        max_length=255,
        label="Detailed Street Address / Landmark",
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'id': 'id_address',
            'placeholder': 'Shop No. 4, Ground Floor, Royal Plaza, MG Road',
            'autocomplete': 'street-address'
        })
    )
    locality = forms.CharField(
        max_length=150,
        label="Area / Neighborhood",
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'id': 'id_locality',
            'placeholder': 'e.g. Kothrud, Indiranagar, Bandra West'
        })
    )
    city = forms.CharField(
        max_length=100,
        initial="Pune",
        label="City",
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'id': 'id_city',
            'placeholder': 'e.g. Pune, Bengaluru, Mumbai'
        })
    )
    state = forms.CharField(
        max_length=100,
        initial="Maharashtra",
        required=False,
        label="State",
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'id': 'id_state',
            'placeholder': 'Maharashtra'
        })
    )
    pincode = forms.CharField(
        max_length=10,
        initial="411001",
        label="Pincode",
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'id': 'id_pincode',
            'placeholder': '411001'
        })
    )
    latitude = forms.DecimalField(
        max_digits=9,
        decimal_places=6,
        required=False,
        initial=18.5204,
        widget=forms.HiddenInput(attrs={'id': 'id_latitude'})
    )
    longitude = forms.DecimalField(
        max_digits=9,
        decimal_places=6,
        required=False,
        initial=73.8567,
        widget=forms.HiddenInput(attrs={'id': 'id_longitude'})
    )
    operating_hours = forms.CharField(
        max_length=150,
        required=False,
        initial="09:00 AM - 10:00 PM (All Days)",
        label="Operating Hours & Open Days",
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'id': 'id_operating_hours',
            'placeholder': 'e.g. 09:00 AM - 10:00 PM (Mon-Sun)'
        })
    )
    display_phone = forms.CharField(
        max_length=20,
        required=False,
        label="Public Support / Counter Phone",
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'id': 'id_display_phone',
            'placeholder': '+91 20 2544 0000 or mobile'
        })
    )

    # STEP 3: Ownership Details
    full_name = forms.CharField(
        max_length=100,
        label="Owner / Authorized Signatory Name",
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'id': 'id_full_name',
            'placeholder': 'Legal full name of signatory',
            'autocomplete': 'name'
        })
    )
    designation = forms.ChoiceField(
        choices=[('OWNER', 'Store Owner'), ('MANAGER', 'Authorized Manager / Partner')],
        initial='OWNER',
        label="Signatory Role / Designation",
        widget=forms.Select(attrs={
            'class': 'form-select',
            'id': 'id_designation'
        })
    )
    email = forms.EmailField(
        label="Official Business & Login Email",
        widget=forms.EmailInput(attrs={
            'class': 'form-control',
            'id': 'id_email',
            'placeholder': 'store.manager@example.com',
            'autocomplete': 'email'
        })
    )
    phone = forms.CharField(
        max_length=15,
        label="Primary Mobile Number",
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'id': 'id_phone',
            'placeholder': '9822011223 (10 digits)',
            'autocomplete': 'tel'
        })
    )
    password = forms.CharField(
        label="Account Password",
        widget=forms.PasswordInput(attrs={
            'class': 'form-control',
            'id': 'id_password',
            'placeholder': 'Min 8 chars, 1 digit, 1 special symbol',
            'autocomplete': 'new-password'
        })
    )
    confirm_password = forms.CharField(
        required=True,
        label="Confirm Password",
        widget=forms.PasswordInput(attrs={
            'class': 'form-control',
            'id': 'id_confirm_password',
            'placeholder': 'Re-enter your password',
            'autocomplete': 'new-password'
        })
    )
    security_question = forms.ChoiceField(
        choices=SECURITY_QUESTIONS,
        required=False,
        label="Security Recovery Question",
        initial=SECURITY_QUESTIONS[0][0],
        widget=forms.Select(attrs={
            'class': 'form-select',
            'id': 'id_security_question'
        })
    )
    security_answer = forms.CharField(
        max_length=200,
        required=False,
        label="Secret Recovery Answer",
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'id': 'id_security_answer',
            'placeholder': 'Your secret answer'
        })
    )

    # STEP 4: Statutory Compliance & Document Numbers
    shop_act_license_number = forms.CharField(
        max_length=50,
        label="Shop Act / Gumasta License Number",
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'id': 'id_shop_act_license_number',
            'placeholder': 'e.g. MH/PUN/2024/0987123',
            'style': 'text-transform: uppercase; font-family: monospace; font-weight: 700;'
        })
    )
    shop_act_valid_till = forms.DateField(
        required=False,
        label="Valid Till",
        input_formats=['%Y-%m-%d', '%Y-%m', '%d/%m/%Y', '%m/%Y'],
        widget=forms.DateInput(attrs={
            'class': 'form-control valid-till-picker',
            'id': 'id_shop_act_valid_till',
            'type': 'date'
        })
    )
    fssai_license_number = forms.CharField(
        max_length=16,
        required=False,
        label="FSSAI Food License Number",
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'id': 'id_fssai_license_number',
            'maxlength': '16',
            'inputmode': 'numeric',
            'oninput': "this.value = this.value.replace(/[^0-9]/g, '').slice(0, 16);",
            'placeholder': '16-digit numeric FSSAI License No.',
            'style': 'font-family: monospace;'
        })
    )
    fssai_valid_till = forms.DateField(
        required=False,
        label="Valid Till",
        input_formats=['%Y-%m-%d', '%Y-%m', '%d/%m/%Y', '%m/%Y'],
        widget=forms.DateInput(attrs={
            'class': 'form-control valid-till-picker',
            'id': 'id_fssai_valid_till',
            'type': 'date'
        })
    )
    fda_license_number = forms.CharField(
        max_length=40,
        required=False,
        label="FDA Drug License Number",
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'id': 'id_fda_license_number',
            'placeholder': 'State FDA License (e.g. MH-PUN-20B-123456)',
            'style': 'text-transform: uppercase; font-family: monospace;'
        })
    )
    fda_valid_till = forms.DateField(
        required=False,
        label="Valid Till",
        input_formats=['%Y-%m-%d', '%Y-%m', '%d/%m/%Y', '%m/%Y'],
        widget=forms.DateInput(attrs={
            'class': 'form-control valid-till-picker',
            'id': 'id_fda_valid_till',
            'type': 'date'
        })
    )
    gst_number = forms.CharField(
        max_length=15,
        required=False,
        label="GSTIN",
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'id': 'id_gst_number',
            'maxlength': '12',
            'oninput': "this.value = this.value.toUpperCase().slice(0, 12);",
            'placeholder': 'GSTIN',
            'style': 'text-transform: uppercase; font-family: monospace;'
        })
    )
    business_pan = forms.CharField(
        max_length=12,
        label="Business PAN",
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'id': 'id_business_pan',
            'maxlength': '12',
            'oninput': "this.value = this.value.toUpperCase().slice(0, 12);",
            'placeholder': 'Business PAN',
            'style': 'text-transform: uppercase; font-family: monospace; font-weight: 700;'
        })
    )

    # Anti-bot honeypot field
    website = forms.CharField(required=False, widget=forms.TextInput(attrs={'style': 'display:none !important;', 'tabindex': '-1', 'autocomplete': 'off'}))

    def clean_website(self):
        val = self.cleaned_data.get('website')
        if val:
            raise forms.ValidationError("Spam bot detected.")
        return val

    def clean_email(self):
        email = self.cleaned_data.get('email', '').strip().lower()
        if User.objects.filter(username__iexact=email).exists() or User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("An account with this email address already exists. Please log in instead.")
        return email

    def clean_phone(self):
        raw_phone = self.cleaned_data.get('phone', '')
        ok, cleaned_phone, err = validate_indian_mobile(raw_phone)
        if not ok:
            raise forms.ValidationError(err)

        if Profile.objects.filter(phone=cleaned_phone).exists() or SellerProfile.objects.filter(phone=cleaned_phone).exists():
            raise forms.ValidationError("This mobile number is already registered with another account.")

        return cleaned_phone

    def clean_password(self):
        password = self.cleaned_data.get('password')
        is_valid, msg = validate_strong_password(password)
        if not is_valid:
            raise forms.ValidationError(msg)
        return password

    def clean_business_pan(self):
        pan = self.cleaned_data.get('business_pan', '')
        ok, cleaned_pan, err = validate_indian_pan(pan)
        if not ok:
            raise forms.ValidationError(err)
        return cleaned_pan

    def clean_gst_number(self):
        gst = self.cleaned_data.get('gst_number', '')
        ok, cleaned_gst, err = validate_indian_gstin(gst)
        if not ok:
            raise forms.ValidationError(err)
        return cleaned_gst

    def clean_fssai_license_number(self):
        fssai = self.cleaned_data.get('fssai_license_number', '').strip()
        if fssai:
            ok, cleaned_fssai, err = validate_fssai_license(fssai)
            if not ok:
                raise forms.ValidationError(err)
            return cleaned_fssai
        return fssai

    def clean_shop_act_license_number(self):
        shop_act = self.cleaned_data.get('shop_act_license_number', '').strip().upper()
        if not shop_act or len(shop_act) < 4:
            raise forms.ValidationError("Compulsory Shop Act / Gumasta License Number is required.")
        return shop_act

    def clean_shop_act_valid_till(self):
        val = self.cleaned_data.get('shop_act_valid_till')
        if val and val < timezone.now().date():
            raise forms.ValidationError("Valid Till date cannot be in the past.")
        return val

    def clean_fssai_valid_till(self):
        val = self.cleaned_data.get('fssai_valid_till')
        if val and val < timezone.now().date():
            raise forms.ValidationError("Valid Till date cannot be in the past.")
        return val

    def clean_fda_valid_till(self):
        val = self.cleaned_data.get('fda_valid_till')
        if val and val < timezone.now().date():
            raise forms.ValidationError("Valid Till date cannot be in the past.")
        return val

    def clean(self):
        cleaned_data = super().clean()
        password = cleaned_data.get('password')
        confirm_password = cleaned_data.get('confirm_password')

        if password and not confirm_password:
            self.add_error('confirm_password', "Please confirm your password.")
        elif password and confirm_password and password != confirm_password:
            self.add_error('confirm_password', "Passwords do not match.")

        category = cleaned_data.get('category')
        subcategory = cleaned_data.get('subcategory')
        fssai = cleaned_data.get('fssai_license_number', '').strip()
        fssai_valid_till = cleaned_data.get('fssai_valid_till')
        fda = cleaned_data.get('fda_license_number', '').strip()
        fda_valid_till = cleaned_data.get('fda_valid_till')

        # Category-dependent compliance enforcement
        if category:
            if category.requires_fssai:
                if not fssai or len(fssai) < 8:
                    self.add_error(
                        'fssai_license_number',
                        f"FSSAI License is mandatory for '{category.name}' businesses. Please enter a valid registration/license number."
                    )
                if not fssai_valid_till:
                    self.add_error(
                        'fssai_valid_till',
                        f"Valid Till date is mandatory for FSSAI License in '{category.name}' businesses."
                    )
            elif fssai and not fssai_valid_till:
                self.add_error(
                    'fssai_valid_till',
                    "Valid Till date is required for the provided FSSAI License."
                )

            if category.requires_fda:
                if not fda or len(fda) < 4:
                    self.add_error(
                        'fda_license_number',
                        f"FDA License is mandatory for '{category.name}' businesses. Please enter your valid state drug license number."
                    )
                if not fda_valid_till:
                    self.add_error(
                        'fda_valid_till',
                        f"Valid Till date is mandatory for FDA Drug License in '{category.name}' businesses."
                    )
            elif fda and not fda_valid_till:
                self.add_error(
                    'fda_valid_till',
                    "Valid Till date is required for the provided FDA Drug License."
                )

            if subcategory and subcategory.category_id != category.id:
                self.add_error(
                    'subcategory',
                    f"Selected subcategory '{subcategory.name}' does not belong to category '{category.name}'."
                )

        return cleaned_data


class LoginForm(CaptchaFieldMixin, forms.Form):
    identifier = forms.CharField(
        label="Email or Username",
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'Enter email or username',
            'autocomplete': 'username'
        })
    )
    password = forms.CharField(
        label="Password",
        widget=forms.PasswordInput(attrs={
            'class': 'form-control',
            'placeholder': '••••••••',
            'autocomplete': 'current-password'
        })
    )


class ChangePasswordForm(forms.Form):
    current_password = forms.CharField(
        label="Current Password",
        widget=forms.PasswordInput(attrs={
            'class': 'form-control',
            'id': 'id_current_password',
            'placeholder': 'Enter your current password',
            'autocomplete': 'current-password',
            'required': 'required'
        })
    )
    new_password = forms.CharField(
        label="New Password",
        widget=forms.PasswordInput(attrs={
            'class': 'form-control',
            'id': 'id_new_password',
            'placeholder': 'Min 8 chars, 1 digit & 1 symbol',
            'autocomplete': 'new-password',
            'required': 'required'
        })
    )
    confirm_password = forms.CharField(
        label="Confirm New Password",
        widget=forms.PasswordInput(attrs={
            'class': 'form-control',
            'id': 'id_confirm_password',
            'placeholder': 'Re-enter your new password',
            'autocomplete': 'new-password',
            'required': 'required'
        })
    )

    def __init__(self, user, *args, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

    def clean_current_password(self):
        current_password = self.cleaned_data.get('current_password')
        if not self.user or not self.user.check_password(current_password):
            raise forms.ValidationError("Your current password is incorrect. Please verify and try again.")
        return current_password

    def clean_new_password(self):
        new_password = self.cleaned_data.get('new_password')
        current_password = self.cleaned_data.get('current_password')

        is_valid, msg = validate_strong_password(new_password)
        if not is_valid:
            raise forms.ValidationError(msg)

        if current_password and new_password == current_password:
            raise forms.ValidationError("New password cannot be the same as your current password.")

        return new_password

    def clean(self):
        cleaned_data = super().clean()
        new_password = cleaned_data.get('new_password')
        confirm_password = cleaned_data.get('confirm_password')

        if new_password and confirm_password and new_password != confirm_password:
            self.add_error('confirm_password', "New passwords do not match. Please ensure both fields are identical.")

        return cleaned_data


class SellerRecoveryLookupForm(CaptchaFieldMixin, forms.Form):
    """
    Step 1 form for merchant password recovery lookup.
    Accepts Email, Mobile Phone Number, or Merchant ID.
    Includes CAPTCHA verification and honeypot protection.
    """
    identifier = forms.CharField(
        max_length=100,
        label="Merchant Email, Mobile Number, or Merchant ID",
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'id': 'id_identifier',
            'placeholder': 'e.g. store@example.com, 9822011223, or PUN-123456',
            'autocomplete': 'username',
            'required': True
        })
    )
    # Anti-bot honeypot
    website = forms.CharField(required=False, widget=forms.TextInput(attrs={'style': 'display:none !important;', 'tabindex': '-1', 'autocomplete': 'off'}))

    def clean_website(self):
        val = self.cleaned_data.get('website')
        if val:
            raise forms.ValidationError("Spam bot detected.")
        return val

    def clean_identifier(self):
        val = self.cleaned_data.get('identifier', '').strip()
        if not val:
            raise forms.ValidationError("Please provide your email, phone number, or Merchant ID.")
        is_malicious, reason = detect_malicious_payload(val)
        if is_malicious:
            raise forms.ValidationError("Invalid characters detected in account identifier.")
        return val


class SellerRecoveryResetForm(forms.Form):
    """
    Step 2 form for merchant password recovery.
    Prompts for secret security answer and new password + confirm password.
    """
    security_answer = forms.CharField(
        max_length=200,
        label="Secret Recovery Answer",
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'id': 'id_security_answer',
            'placeholder': 'Enter your secret recovery answer',
            'required': True,
            'autocomplete': 'off'
        })
    )
    new_password = forms.CharField(
        label="New Strong Password",
        widget=forms.PasswordInput(attrs={
            'class': 'form-control',
            'id': 'id_new_password',
            'placeholder': 'Create new strong password (min 8 chars)',
            'autocomplete': 'new-password',
            'required': True
        })
    )
    confirm_password = forms.CharField(
        label="Confirm New Password",
        widget=forms.PasswordInput(attrs={
            'class': 'form-control',
            'id': 'id_confirm_password',
            'placeholder': 'Re-enter new password',
            'autocomplete': 'new-password',
            'required': True
        })
    )
    # Anti-bot honeypot
    website = forms.CharField(required=False, widget=forms.TextInput(attrs={'style': 'display:none !important;', 'tabindex': '-1', 'autocomplete': 'off'}))

    def clean_website(self):
        val = self.cleaned_data.get('website')
        if val:
            raise forms.ValidationError("Spam bot detected.")
        return val

    def clean_security_answer(self):
        val = self.cleaned_data.get('security_answer', '').strip()
        if not val or len(val) < 2:
            raise forms.ValidationError("Please provide your secret recovery answer (min 2 characters).")
        is_malicious, reason = detect_malicious_payload(val)
        if is_malicious:
            raise forms.ValidationError("Invalid characters detected in recovery answer.")
        return val

    def clean_new_password(self):
        password = self.cleaned_data.get('new_password')
        is_valid, msg = validate_strong_password(password)
        if not is_valid:
            raise forms.ValidationError(msg)
        return password

    def clean(self):
        cleaned_data = super().clean()
        p1 = cleaned_data.get('new_password')
        p2 = cleaned_data.get('confirm_password')
        if p1 and p2 and p1 != p2:
            self.add_error('confirm_password', "Passwords do not match.")
        return cleaned_data



class SellerAccountDeletionForm(forms.Form):
    AUTH_METHOD_CHOICES = (
        ('PASSWORD', 'Current Password'),
        ('SECURITY_QUESTION', 'Security Question & Answer'),
    )

    reason_category = forms.ChoiceField(
        choices=DeletionReasonCategory.choices,
        widget=forms.Select(attrs={
            'class': 'form-select',
            'id': 'id_seller_del_reason'
        })
    )
    reason_notes = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={
            'class': 'form-control',
            'id': 'id_seller_del_notes',
            'rows': 3,
            'placeholder': 'Optional: Please share why you are closing your store account...'
        })
    )
    auth_method = forms.ChoiceField(
        choices=AUTH_METHOD_CHOICES,
        initial='PASSWORD',
        widget=forms.RadioSelect(attrs={'class': 'form-check-input'})
    )
    current_password = forms.CharField(
        required=False,
        widget=forms.PasswordInput(attrs={
            'class': 'form-control',
            'id': 'id_seller_del_password',
            'placeholder': 'Enter your current account password'
        })
    )
    security_answer = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'id': 'id_seller_del_answer',
            'placeholder': 'Enter your secret security answer'
        })
    )
    confirm_bulk_unpublish = forms.BooleanField(
        required=False,
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input', 'id': 'id_confirm_bulk_unpublish'})
    )
    confirm_closure = forms.BooleanField(
        required=True,
        error_messages={'required': 'You must acknowledge the legal consequences to proceed.'},
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input', 'id': 'id_confirm_seller_closure'})
    )

    def __init__(self, user, *args, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

    def clean(self):
        cleaned_data = super().clean()
        auth_method = cleaned_data.get('auth_method', 'PASSWORD')
        current_password = cleaned_data.get('current_password', '')
        security_answer = cleaned_data.get('security_answer', '')

        if auth_method == 'PASSWORD':
            if not current_password:
                self.add_error('current_password', 'Please enter your current account password.')
            elif not self.user.check_password(current_password):
                self.add_error('current_password', 'Your current password is incorrect.')
        elif auth_method == 'SECURITY_QUESTION':
            if not security_answer:
                self.add_error('security_answer', 'Please enter your secret recovery answer.')
            else:
                seller_profile = getattr(self.user, 'seller_profile', None)
                if not seller_profile or not seller_profile.security_question:
                    self.add_error('security_answer', 'No security question is configured on your merchant profile. Please use your password.')
                elif not seller_profile.check_security_answer(security_answer):
                    self.add_error('security_answer', 'Secret recovery answer is incorrect.')

        return cleaned_data


class BuyerAccountDeletionForm(forms.Form):
    AUTH_METHOD_CHOICES = (
        ('PASSWORD', 'Current Password'),
        ('SECURITY_QUESTION', 'Secret Recovery Answer'),
    )

    reason_category = forms.ChoiceField(
        choices=DeletionReasonCategory.choices,
        widget=forms.Select(attrs={
            'class': 'form-select',
            'id': 'id_buyer_del_reason'
        })
    )
    reason_notes = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={
            'class': 'form-control',
            'id': 'id_buyer_del_notes',
            'rows': 3,
            'placeholder': 'Optional: Please share why you are deleting your account...'
        })
    )
    auth_method = forms.ChoiceField(
        choices=AUTH_METHOD_CHOICES,
        initial='PASSWORD',
        widget=forms.RadioSelect(attrs={'class': 'form-check-input'})
    )
    current_password = forms.CharField(
        required=False,
        widget=forms.PasswordInput(attrs={
            'class': 'form-control',
            'id': 'id_buyer_del_password',
            'placeholder': 'Enter your password'
        })
    )
    security_answer = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'id': 'id_buyer_del_answer',
            'placeholder': 'Enter your secret recovery answer'
        })
    )
    confirm_forfeiture = forms.BooleanField(
        required=True,
        error_messages={'required': 'You must acknowledge the forfeiture of all rewards and points.'},
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input', 'id': 'id_confirm_buyer_forfeiture'})
    )

    def __init__(self, user, *args, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

    def clean(self):
        cleaned_data = super().clean()
        auth_method = cleaned_data.get('auth_method', 'PASSWORD')
        current_password = cleaned_data.get('current_password', '')
        security_answer = cleaned_data.get('security_answer', '')

        if auth_method == 'PASSWORD':
            if not current_password:
                self.add_error('current_password', 'Please enter your account password.')
            elif not self.user.check_password(current_password):
                self.add_error('current_password', 'Your password is incorrect.')
        elif auth_method == 'SECURITY_QUESTION':
            if not security_answer:
                self.add_error('security_answer', 'Please enter your secret recovery answer.')
            else:
                buyer_profile = getattr(self.user, 'buyer_profile', None)
                if not buyer_profile or not buyer_profile.security_question:
                    self.add_error('security_answer', 'No security question is configured on your shopper account. Please use your password.')
                elif not buyer_profile.check_security_answer(security_answer):
                    self.add_error('security_answer', 'Secret recovery answer is incorrect.')

        return cleaned_data
