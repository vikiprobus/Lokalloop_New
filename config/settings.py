"""
Django settings for LokalLoop project.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

# Load environment variables from .env file
load_dotenv(BASE_DIR / '.env')

# Quick-start development settings - unsuitable for production
SECRET_KEY = os.environ.get('DJANGO_SECRET_KEY', 'django-insecure-lokalloop-dev-fallback-key-2026')
DEBUG = os.environ.get('DJANGO_DEBUG', 'True') == 'True'

# Configurable built-in admin secret path (masks default /admin/ from automated bot scans)
DJANGO_ADMIN_URL = os.environ.get('DJANGO_ADMIN_URL', 'portal-admin/').strip('/') + '/'

# Decoupled Third-Party Integrations & Platform Secrets (Safe fallbacks in development)
RAZORPAY_KEY_ID = os.environ.get('RAZORPAY_KEY_ID', '')
RAZORPAY_KEY_SECRET = os.environ.get('RAZORPAY_KEY_SECRET', '')
RAZORPAY_WEBHOOK_SECRET = os.environ.get('RAZORPAY_WEBHOOK_SECRET', '')

GOOGLE_MAPS_API_KEY = os.environ.get('GOOGLE_MAPS_API_KEY', '')

AWS_ACCESS_KEY_ID = os.environ.get('AWS_ACCESS_KEY_ID', '')
AWS_SECRET_ACCESS_KEY = os.environ.get('AWS_SECRET_ACCESS_KEY', '')
AWS_STORAGE_BUCKET_NAME = os.environ.get('AWS_STORAGE_BUCKET_NAME', '')
AWS_S3_REGION_NAME = os.environ.get('AWS_S3_REGION_NAME', 'ap-south-1')

FIREBASE_CREDENTIALS_PATH = os.environ.get('FIREBASE_CREDENTIALS_PATH', '')
FIREBASE_API_KEY = os.environ.get('FIREBASE_API_KEY', '')

SMS_GATEWAY_API_KEY = os.environ.get('SMS_GATEWAY_API_KEY', '')
SMS_GATEWAY_SENDER_ID = os.environ.get('SMS_GATEWAY_SENDER_ID', 'LOKALP')
SMS_GATEWAY_PROVIDER = os.environ.get('SMS_GATEWAY_PROVIDER', 'dummy')

allowed_hosts_raw = os.environ.get('DJANGO_ALLOWED_HOSTS', '127.0.0.1,localhost,.trycloudflare.com')
ALLOWED_HOSTS = [host.strip() for host in allowed_hosts_raw.split(',') if host.strip()]
if '.trycloudflare.com' not in ALLOWED_HOSTS:
    ALLOWED_HOSTS.append('.trycloudflare.com')
if 'testserver' not in ALLOWED_HOSTS:
    ALLOWED_HOSTS.append('testserver')

CSRF_TRUSTED_ORIGINS = [
    'https://*.trycloudflare.com',
    'http://127.0.0.1:8000',
    'http://localhost:8000',
]

# Fail-Fast Startup Verification for Mission-Critical Production Configuration
def validate_environment_configuration():
    from django.core.exceptions import ImproperlyConfigured
    if not DEBUG:
        insecure_keys = [
            'django-insecure-lokalloop-dev-fallback-key-2026',
            'lokalloop-dev-secret-key-change-in-production-2026-secure!',
            'secret',
            'changeme'
        ]
        if not SECRET_KEY or any(k in SECRET_KEY.lower() for k in insecure_keys):
            raise ImproperlyConfigured(
                "CRITICAL SECURITY CONFIGURATION ERROR: DJANGO_SECRET_KEY is missing or set to an insecure default in production (DEBUG=False). "
                "Provide a strong 50+ character cryptographic secret in your production .env environment."
            )
        if not ALLOWED_HOSTS or ALLOWED_HOSTS == ['127.0.0.1', 'localhost', 'testserver']:
            raise ImproperlyConfigured(
                "CRITICAL SECURITY CONFIGURATION ERROR: DJANGO_ALLOWED_HOSTS must be set to your public domain(s) in production (DEBUG=False)."
            )

validate_environment_configuration()

# Application definition
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django.contrib.humanize',

    # Third-party
    'rest_framework',

    # LokalLoop Apps
    'apps.accounts',
    'apps.shops',
    'apps.offers',
    'apps.redemptions',
    'apps.wallets',
    'apps.rewards',
    'apps.notifications',
    'apps.analytics',
    'apps.administration',
    'apps.public',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'config.middleware.RateLimitMiddleware',
    'config.middleware.ApiSecurityMiddleware',
    'config.middleware.SecurityHeadersMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'config.middleware.UserNavigationStateMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'apps.administration.middleware.ExceptionLoggingMiddleware',
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'apps.accounts.context_processors.role_context',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'

# Database
# Uses SQLite for initial prototype, architected to switch cleanly to PostgreSQL
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
        'OPTIONS': {
            'timeout': 30,  # 30-second lock wait for concurrent transactions
        }
    }
}

# Password validation
AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
        'OPTIONS': {'min_length': 8},
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
    {
        'NAME': 'apps.accounts.validators.EnterpriseComplexityValidator',
        'OPTIONS': {'min_length': 8},
    },
]

# Internationalization & Indian Localization
LANGUAGE_CODE = 'en-in'
TIME_ZONE = 'Asia/Kolkata'
USE_I18N = True
USE_TZ = True

# Static files (CSS, JavaScript, Images)
STATIC_URL = '/static/'
STATICFILES_DIRS = [BASE_DIR / 'static']
STATIC_ROOT = BASE_DIR / 'staticfiles'

# Media files
MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

# Upload Limits (Modern smartphone camera photos are 3-8MB; allow up to 25MB total payload, 15MB file)
DATA_UPLOAD_MAX_MEMORY_SIZE = 26214400   # 25 MB
FILE_UPLOAD_MAX_MEMORY_SIZE = 15728640   # 15 MB

# Default primary key field type
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# Authentication URLs
LOGIN_URL = '/buyer/login/'
LOGIN_REDIRECT_URL = '/'
LOGOUT_REDIRECT_URL = '/'

# Django REST Framework Settings
REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'rest_framework.authentication.SessionAuthentication',
    ],
    'DEFAULT_PERMISSION_CLASSES': [
        'rest_framework.permissions.IsAuthenticated',
    ],
}

# Security, Session, and Cookie Hardening
SESSION_COOKIE_AGE = 86400 * 7  # 7 days
SESSION_EXPIRE_AT_BROWSER_CLOSE = False
SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = 'Lax'
CSRF_COOKIE_SAMESITE = 'Lax'
SECURE_BROWSER_XSS_FILTER = True
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = 'DENY'

# Message tags for Bootstrap / Tailwind-compatible styling
from django.contrib.messages import constants as messages
MESSAGE_TAGS = {
    messages.DEBUG: 'secondary',
    messages.INFO: 'info',
    messages.SUCCESS: 'success',
    messages.WARNING: 'warning',
    messages.ERROR: 'danger',
}

# Self-Hosted Enterprise Secure CAPTCHA Configuration
CAPTCHA_ENFORCE = True
CAPTCHA_EXPIRY_SECONDS = 300  # 5 minutes
CAPTCHA_ALLOW_TEST_BYPASS = False  # Enabled selectively in tests

# Fast password hasher for automated tests (Official Django Recommendation)
import sys
if 'test' in sys.argv:
    PASSWORD_HASHERS = [
        'django.contrib.auth.hashers.MD5PasswordHasher',
    ]


