from django import forms
from django.core.validators import EmailValidator
from apps.accounts.forms import CaptchaFieldMixin
from apps.administration.models import InquiryRole
from services.security_validator import (
    validate_alphanumeric_name,
    validate_indian_mobile,
    sanitize_text,
    detect_malicious_payload,
    SecurityValidationError
)


class ContactInquiryForm(CaptchaFieldMixin, forms.Form):
    """
    Enterprise-Grade & Regulatory-Compliant Contact Inquiry & Grievance Desk Form.
    Features:
    1. Whitelist-first sanitized name and phone validation.
    2. Deep XSS, SQLi, and Path Traversal detection on all text fields.
    3. Invisible anti-bot honeypot field.
    4. Anti-bot slider CAPTCHA verification.
    """
    full_name = forms.CharField(
        max_length=100,
        label="Full Name",
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'id': 'id_full_name',
            'placeholder': 'Enter your full name',
            'required': True,
            'autocomplete': 'name'
        })
    )
    email = forms.EmailField(
        max_length=254,
        label="Email Address",
        validators=[EmailValidator(message="Please provide a valid email address.")],
        widget=forms.EmailInput(attrs={
            'class': 'form-control',
            'id': 'id_email',
            'placeholder': 'name@example.com',
            'required': True,
            'autocomplete': 'email'
        })
    )
    phone = forms.CharField(
        max_length=15,
        required=False,
        label="Mobile Phone Number (Optional)",
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'id': 'id_phone',
            'placeholder': '10-digit Indian Mobile (e.g. 9822011223)',
            'autocomplete': 'tel'
        })
    )
    user_role = forms.ChoiceField(
        choices=InquiryRole.choices,
        label="Inquiry Category / Department",
        initial=InquiryRole.BUYER,
        widget=forms.Select(attrs={
            'class': 'form-select',
            'id': 'id_user_role'
        })
    )
    subject = forms.CharField(
        max_length=150,
        label="Subject / Topic",
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'id': 'id_subject',
            'placeholder': 'Brief summary of your inquiry or grievance',
            'required': True
        })
    )
    message = forms.CharField(
        max_length=2000,
        label="Detailed Message",
        widget=forms.Textarea(attrs={
            'class': 'form-control',
            'id': 'id_message',
            'rows': 5,
            'placeholder': 'Please describe your request, dispute details, or feedback with relevant reference IDs...',
            'required': True
        })
    )
    # Anti-bot honeypot field (hidden via CSS; must remain empty)
    website = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={
            'style': 'display:none !important;',
            'tabindex': '-1',
            'autocomplete': 'off'
        })
    )

    def clean_website(self):
        val = self.cleaned_data.get('website')
        if val:
            raise forms.ValidationError("Spam bot detected.")
        return val

    def clean_full_name(self):
        raw_name = self.cleaned_data.get('full_name', '').strip()
        is_malicious, reason = detect_malicious_payload(raw_name)
        if is_malicious:
            raise forms.ValidationError("Invalid characters detected in name.")

        is_valid, cleaned, err = validate_alphanumeric_name(raw_name, min_len=2, max_len=100, field_title="Full Name")
        if not is_valid:
            raise forms.ValidationError(err or "Please provide a valid name.")
        return cleaned

    def clean_phone(self):
        raw_phone = self.cleaned_data.get('phone', '').strip()
        if not raw_phone:
            return ''

        is_malicious, reason = detect_malicious_payload(raw_phone)
        if is_malicious:
            raise forms.ValidationError("Invalid characters in phone number.")

        is_valid, cleaned, err = validate_indian_mobile(raw_phone)
        if not is_valid:
            raise forms.ValidationError(err or "Please enter a valid 10-digit Indian mobile number.")
        return cleaned

    def clean_subject(self):
        raw_subj = self.cleaned_data.get('subject', '').strip()
        is_malicious, reason = detect_malicious_payload(raw_subj)
        if is_malicious:
            raise forms.ValidationError("Security check failed on subject field.")

        sanitized = sanitize_text(raw_subj, max_length=150, allow_newlines=False, strip_html=True)
        if not sanitized:
            raise forms.ValidationError("Subject cannot be empty.")
        return sanitized

    def clean_message(self):
        raw_msg = self.cleaned_data.get('message', '').strip()
        is_malicious, reason = detect_malicious_payload(raw_msg, max_length=2000)
        if is_malicious:
            raise forms.ValidationError("Security check failed on message content.")

        sanitized = sanitize_text(raw_msg, max_length=2000, allow_newlines=True, strip_html=True)
        if not sanitized or len(sanitized) < 5:
            raise forms.ValidationError("Please provide a detailed message (minimum 5 characters).")
        return sanitized
