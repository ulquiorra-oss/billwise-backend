"""
Django settings for billwise project.
"""

import os
from pathlib import Path
from dotenv import load_dotenv
from datetime import timedelta

import pymysql
pymysql.install_as_MySQLdb()

# Loads a local ".env" file (kept out of Git) into the environment, so secrets never go in this file.
load_dotenv()

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = os.getenv('SECRET_KEY')

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = os.getenv('DEBUG') == 'True'

ALLOWED_HOSTS = ['*', 'billwise-backend-production.up.railway.app']


# Application definition

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'rest_framework',
    'rest_framework_simplejwt',
    'corsheaders',
    'social_django',
    'rest_framework_simplejwt.token_blacklist',
    'api',
]

MIDDLEWARE = [
    'corsheaders.middleware.CorsMiddleware',
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'billwise.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'billwise.wsgi.application'


# Database
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.mysql',
        'NAME': os.getenv('DB_NAME'),
        'USER': os.getenv('DB_USER'),
        'PASSWORD': os.getenv('DB_PASSWORD'),
        'HOST': os.getenv('DB_HOST'),
        'PORT': os.getenv('DB_PORT', '3306'),
    }
}

# REST Framework settings
REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': (
        'api.authentication.HouseholdJWTAuthentication',
    ),
    'DEFAULT_PERMISSION_CLASSES': (
        'rest_framework.permissions.IsAuthenticated',
    ),
}

# CORS
CORS_ALLOW_ALL_ORIGINS = True

# --- STATIC FILES CONFIGURATION FOR RAILWAY ---
STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / "staticfiles"

STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'
# -----------------------------------------------

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'


# Password validation
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
LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True


# ============================================================
# Email settings: Google Apps Script mailer (works on Railway)
# ============================================================
# Railway blocks outbound SMTP, so OTP emails go out over HTTPS through a small Google Apps Script
# web app (see Code.gs). Every existing send_mail(...) call keeps working unchanged.
#
# Set these two as environment variables, in Railway's Variables tab and in your local ".env" file.
# NEVER type their values into this file:
#   APPS_SCRIPT_MAIL_URL     the web app URL, ends in /exec
#   APPS_SCRIPT_MAIL_SECRET  the same value as the MAIL_SECRET script property
EMAIL_BACKEND = 'api.email_backend.AppsScriptEmailBackend'

# Informational only: Apps Script always sends from the Gmail account that owns the script.
DEFAULT_FROM_EMAIL = os.getenv('DEFAULT_FROM_EMAIL', 'BillWise <no-reply@billwise.app>')
# ============================================================


# JWT Settings
SIMPLE_JWT = {
    'ACCESS_TOKEN_LIFETIME': timedelta(days=1),
    'REFRESH_TOKEN_LIFETIME': timedelta(days=7),
    'ROTATE_REFRESH_TOKENS': True,
    'BLACKLIST_AFTER_ROTATION': False,
    'USER_ID_FIELD': 'household_id',
    'USER_ID_CLAIM': 'household_id',
}

# Google OAuth
GOOGLE_CLIENT_ID = os.getenv('GOOGLE_CLIENT_ID')
GOOGLE_CLIENT_SECRET = os.getenv('GOOGLE_CLIENT_SECRET')

# ============================================================
# Other environment variables this project reads (set in ".env" locally, Railway Variables online)
# ============================================================
#   SECRET_KEY, DEBUG, DB_NAME, DB_USER, DB_PASSWORD, DB_HOST, DB_PORT
#   GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET
#   APPS_SCRIPT_MAIL_URL, APPS_SCRIPT_MAIL_SECRET          (email, above)
#   ANTHROPIC_API_KEY, BILLWISE_LLM_MODE, BILLWISE_LLM_MODEL   (Claude bill reader, see llm_reader.py)
#   BILLWISE_OCR_DEBUG_DIR                                  (optional, saves OCR debug images)