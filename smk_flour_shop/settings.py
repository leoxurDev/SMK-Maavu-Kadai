import os
from pathlib import Path
import environ

# Initialize environ
env = environ.Env(
    # set casting, default value
    DEBUG=(bool, False)
)

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

# Read .env file if it exists
environ.Env.read_env(os.path.join(BASE_DIR, '.env'))

# Quick-start development settings - unsuitable for production
# See https://docs.djangoproject.com/en/4.2/howto/deployment/checklist/

SECRET_KEY = env.str('SECRET_KEY', default='django-insecure-default-change-me')

DEBUG = env.bool('DEBUG', default=True)

ALLOWED_HOSTS = env.list('ALLOWED_HOSTS', default=['localhost', '127.0.0.1'])

# Application definition
INSTALLED_APPS = [
    # Jazzmin admin needs to be loaded before django.contrib.admin
    'jazzmin',
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    
    # Third party apps
    'django_htmx',
    
    # Project apps
    'shop',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'django_htmx.middleware.HtmxMiddleware',
]

ROOT_URLCONF = 'smk_flour_shop.urls'

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
                'shop.context_processors.cart_processor',
                'shop.context_processors.customer_processor',
            ],
        },
    },
]

WSGI_APPLICATION = 'smk_flour_shop.wsgi.py' # wait, should be 'smk_flour_shop.wsgi.application'
# Let's fix this detail. Django wsgi string references the module and variable name:
WSGI_APPLICATION = 'smk_flour_shop.wsgi.application'
ASGI_APPLICATION = 'smk_flour_shop.asgi.application'

# Database Setup
# Fallback to sqlite if DB_ENGINE is not set to mysql or if DB_NAME is missing
DB_ENGINE = env.str('DB_ENGINE', default='sqlite')
if DB_ENGINE == 'mysql' and env.str('DB_NAME', default=''):
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.mysql',
            'NAME': env.str('DB_NAME'),
            'USER': env.str('DB_USER', default=''),
            'PASSWORD': env.str('DB_PASSWORD', default=''),
            'HOST': env.str('DB_HOST', default='127.0.0.1'),
            'PORT': env.str('DB_PORT', default='3306'),
            'OPTIONS': {
                'charset': 'utf8mb4',
            }
        }
    }
else:
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': BASE_DIR / 'db.sqlite3',
        }
    }

# Password validation
# https://docs.djangoproject.com/en/4.2/ref/settings/#auth-password-validators
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
# https://docs.djangoproject.com/en/4.2/topics/i18n/
LANGUAGE_CODE = 'en-us'

TIME_ZONE = 'Asia/Kolkata'

USE_I18N = True

USE_TZ = True

# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/4.2/howto/static-files/
STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
STATICFILES_DIRS = [BASE_DIR / 'static']

# Media files (User uploaded files: product images, QR codes)
MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

# Default primary key field type
# https://docs.djangoproject.com/en/4.2/ref/settings/#default-auto-field
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# Celery & Cache Settings
CELERY_BROKER_URL = env.str('CELERY_BROKER_URL', default='memory://')
CELERY_RESULT_BACKEND = env.str('CELERY_RESULT_BACKEND', default='')
CELERY_ACCEPT_CONTENT = ['json']
CELERY_TASK_SERIALIZER = 'json'

# Razorpay Keys
RAZORPAY_KEY_ID = env.str('RAZORPAY_KEY_ID', default='')
RAZORPAY_KEY_SECRET = env.str('RAZORPAY_KEY_SECRET', default='')

# Public host URL for QR landing links
SITE_URL = env.str('SITE_URL', default='http://127.0.0.1:8000')

# Jazzmin settings for nice admin UI
JAZZMIN_SETTINGS = {
    "site_title": "SMK Flour Shop Admin",
    "site_header": "SMK Flour Shop",
    "site_brand": "SMK Admin",
    "welcome_sign": "Welcome to SMK Flour Shop Management Panel",
    "copyright": "SMK Flour Shop",
    "search_model": ["shop.Product", "shop.Order"],
    "topmenu_links": [
        {"name": "Home",  "url": "admin:index", "permissions": ["auth.view_user"]},
        {"name": "Operations Dashboard", "url": "/admin-dashboard/", "permissions": ["auth.view_user"]},
        {"model": "shop.Order"},
    ],
    "show_sidebar": True,
    "navigation_expanded": True,
    "order_with_respect_to": ["shop", "shop.Shop", "shop.Category", "shop.Product", "shop.PriceSlab", "shop.Order"],
    "icons": {
        "auth": "fas fa-users-cog",
        "auth.user": "fas fa-user",
        "auth.Group": "fas fa-users",
        "shop.Shop": "fas fa-store",
        "shop.Category": "fas fa-list",
        "shop.Product": "fas fa-box",
        "shop.PriceSlab": "fas fa-tags",
        "shop.Customer": "fas fa-user-friends",
        "shop.Address": "fas fa-map-marker-alt",
        "shop.Order": "fas fa-shopping-cart",
        "shop.OrderItem": "fas fa-shopping-basket",
        "shop.Payment": "fas fa-credit-card",
    },
    "default_icon_parents": "fas fa-chevron-circle-right",
    "default_icon_children": "fas fa-circle",
}

JAZZMIN_UI_TWEAKS = {
    "theme": "flatly",
}
