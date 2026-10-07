from pathlib import Path
import os

import dj_database_url
from dotenv import load_dotenv
from django.core.exceptions import ImproperlyConfigured

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / '.env')


def _env_bool(value, default=False):
    return str(value).strip().lower() in {'1', 'true', 'yes', 'on'} if value is not None else default


# Quick-start development settings - unsuitable for production
# See https://docs.djangoproject.com/en/6.1/howto/deployment/checklist/

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = os.environ.get('SECRET_KEY') or 'django-insecure-dev-secret-key-change-me'

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = _env_bool(os.environ.get('DEBUG') or None, True)
if not DEBUG and SECRET_KEY == 'django-insecure-dev-secret-key-change-me':
    raise ImproperlyConfigured('SECRET_KEY must be configured when DEBUG is False.')

ALLOWED_HOSTS = [
    host.strip()
    for host in (os.environ.get('ALLOWED_HOSTS') or 'localhost,127.0.0.1,[::1]').split(',')
    if host.strip()
]

CSRF_TRUSTED_ORIGINS = [
    origin.strip()
    for origin in os.environ.get('CSRF_TRUSTED_ORIGINS', '').split(',')
    if origin.strip()
]

CLOUDINARY_CREDENTIALS = {
    'CLOUD_NAME': os.environ.get('CLOUDINARY_CLOUD_NAME', '').strip(),
    'API_KEY': os.environ.get('CLOUDINARY_API_KEY', '').strip(),
    'API_SECRET': os.environ.get('CLOUDINARY_API_SECRET', '').strip(),
}
CLOUDINARY_ENABLED = any(CLOUDINARY_CREDENTIALS.values())
if CLOUDINARY_ENABLED and not all(CLOUDINARY_CREDENTIALS.values()):
    raise ImproperlyConfigured(
        'Set CLOUDINARY_CLOUD_NAME, CLOUDINARY_API_KEY, and CLOUDINARY_API_SECRET together.'
    )

# Application definition

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',

    # My Apps
    'vendors',
    'products',
]
if CLOUDINARY_ENABLED:
    INSTALLED_APPS += ['cloudinary', 'cloudinary_storage']

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'tradezone_core.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],  # Pointing to the templates directory
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'products.context_processors.account_role',
            ],
        },
    },
]


STATICFILES_DIRS = [
    BASE_DIR / 'static',  # Pointing to the static directory
]

WSGI_APPLICATION = 'tradezone_core.wsgi.application'


# Database
# https://docs.djangoproject.com/en/6.1/ref/settings/#databases

DATABASE_URL = os.environ.get('DATABASE_URL', '').strip()
if DATABASE_URL:
    default_database = dj_database_url.parse(
        DATABASE_URL,
        conn_max_age=600,
        conn_health_checks=True,
        ssl_require=not DEBUG,
    )
else:
    default_database = dj_database_url.parse(
        f"sqlite:///{BASE_DIR.as_posix()}/db.sqlite3",
        conn_max_age=600,
        conn_health_checks=True,
    )
DATABASES = {'default': default_database}


# Password validation
# https://docs.djangoproject.com/en/6.1/ref/settings/#auth-password-validators

AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]


# Internationalization
# https://docs.djangoproject.com/en/6.1/topics/i18n/

LANGUAGE_CODE = 'en-us'

TIME_ZONE = 'UTC'

USE_I18N = True

USE_TZ = True


# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/6.1/howto/static-files/

STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'

# Media files (User uploads)
MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'
STORAGES = {
    'default': {
        'BACKEND': (
            'cloudinary_storage.storage.MediaCloudinaryStorage'
            if CLOUDINARY_ENABLED else 'django.core.files.storage.FileSystemStorage'
        ),
    },
    'staticfiles': {
        'BACKEND': (
            'whitenoise.storage.CompressedManifestStaticFilesStorage'
            if not DEBUG else 'django.contrib.staticfiles.storage.StaticFilesStorage'
        ),
    },
}
if CLOUDINARY_ENABLED:
    CLOUDINARY_STORAGE = CLOUDINARY_CREDENTIALS


# Email Configuration
# https://docs.djangoproject.com/en/6.1/topics/email/#topic-email-configuration

EMAIL_HOST = os.environ.get('EMAIL_HOST', '').strip()
EMAIL_PORT = int(os.environ.get('EMAIL_PORT') or 587)
EMAIL_USE_TLS = _env_bool(os.environ.get('EMAIL_USE_TLS') or None, True)
EMAIL_HOST_USER = os.environ.get('EMAIL_HOST_USER', '')
EMAIL_HOST_PASSWORD = os.environ.get('EMAIL_HOST_PASSWORD', '')
DEFAULT_FROM_EMAIL = os.environ.get('DEFAULT_FROM_EMAIL', 'webmaster@localhost')
COMPLAINTS_EMAIL = os.environ.get('COMPLAINTS_EMAIL', '').strip()
EMAIL_BACKEND = (
    'django.core.mail.backends.smtp.EmailBackend'
    if EMAIL_HOST else 'django.core.mail.backends.console.EmailBackend'
)

PAYSTACK_SECRET_KEY = os.environ.get('PAYSTACK_SECRET_KEY', '')
PAYSTACK_PUBLIC_KEY = os.environ.get('PAYSTACK_PUBLIC_KEY', '')

if not DEBUG:
    SECURE_SSL_REDIRECT = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
