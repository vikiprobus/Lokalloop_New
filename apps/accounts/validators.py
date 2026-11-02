import re
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _


class EnterpriseComplexityValidator:
    """
    Enforces OWASP/Enterprise password complexity:
    - Minimum length 8 characters
    - At least 1 numerical digit (0-9)
    - At least 1 special symbol (e.g. @#$%!&*^~_+=-)
    """

    def __init__(self, min_length=8):
        self.min_length = min_length

    def validate(self, password, user=None):
        if not password or len(password) < self.min_length:
            raise ValidationError(
                _(f"Password must be at least {self.min_length} characters long."),
                code='password_too_short',
            )

        if not re.search(r'\d', password):
            raise ValidationError(
                _("Password must contain at least one numerical digit (0-9)."),
                code='password_no_digit',
            )

        if not re.search(r'[@#$%!&*^~_+=\\/\-()[\]{}|:;,.?<>`"]', password):
            raise ValidationError(
                _("Password must contain at least one special character (@, #, $, %, !, &, *, etc.)."),
                code='password_no_special',
            )

    def get_help_text(self):
        return _(
            f"Your password must contain at least {self.min_length} characters, "
            "including at least one number and one special symbol."
        )
