"""
Django settings for the QulaySIM admin panel.

Django is the SCHEMA OWNER for the whole platform: all models and migrations
live here, and the FastAPI service reads/writes the same PostgreSQL tables via
SQLAlchemy. The admin UI is themed with django-unfold ("Signal" palette).
"""

from pathlib import Path

from decouple import Csv, config
from django.templatetags.static import static

BASE_DIR = Path(__file__).resolve().parent.parent

# No fall-back for the secret: a missing value must crash the process rather
# than quietly boot with a key an attacker can look up in the repository.
SECRET_KEY = config("SECRET_KEY")
# Defaults to OFF. A missing DEBUG variable used to mean "debug on", which
# turns every 500 into a full stack trace plus every setting, database
# password included.
DEBUG = config("DEBUG", default=False, cast=bool)
ALLOWED_HOSTS = config("ALLOWED_HOSTS", default="localhost,127.0.0.1", cast=Csv())
CSRF_TRUSTED_ORIGINS = config("CSRF_TRUSTED_ORIGINS", default="", cast=Csv())


INSTALLED_APPS = [
    # Unfold must come before django.contrib.admin
    "unfold",
    "unfold.contrib.filters",
    "unfold.contrib.forms",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # QulaySIM apps
    "catalog",
    "customers",
    "orders",
    "content",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    # Serves the collected static files. Django itself refuses to with
    # DEBUG=False and gunicorn has no opinion about them, so without this the
    # entire admin — Unfold's CSS included — loads unstyled behind a 404.
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": config("DB_NAME", default="fastsim"),
        "USER": config("DB_USER", default="fastsim"),
        "PASSWORD": config("DB_PASSWORD", default="fastsim"),
        "HOST": config("DB_HOST", default="localhost"),
        "PORT": config("DB_PORT", default="5432"),
    }
}

# ---------------------------------------------------------------------------
# Transport and cookie security
#
# Applied only outside DEBUG so local development over plain HTTP still works.
# Behind a reverse proxy Django cannot see the original scheme, so it is told
# to trust the forwarded header — without this SECURE_SSL_REDIRECT would loop.
# ---------------------------------------------------------------------------
if not DEBUG:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SECURE_SSL_REDIRECT = config("SECURE_SSL_REDIRECT", default=True, cast=bool)
    # The container healthcheck runs over plain HTTP from inside the network;
    # redirecting it to https on an internal address just times out.
    SECURE_REDIRECT_EXEMPT = [r"^healthz$"]
    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True

SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"
X_FRAME_OPTIONS = "DENY"

SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"
# Staff sessions are far more valuable than customer ones; expire them daily
# and on browser close rather than leaving them open for two weeks.
SESSION_COOKIE_AGE = 60 * 60 * 24
SESSION_EXPIRE_AT_BROWSER_CLOSE = True

# The admin is not on a guessable path in production.
ADMIN_URL_PATH = config("ADMIN_URL_PATH", default="admin")

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 8},
    },
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]

# Compression without the manifest: Unfold ships CSS that references assets by
# relative path, and the manifest backend turns any missing reference into a
# hard failure at collectstatic time.
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedStaticFilesStorage"},
}
# Static filenames are not hashed, so let the browser revalidate rather than
# cache a stale stylesheet for a year.
WHITENOISE_MAX_AGE = 3600
MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"


# ---------------------------------------------------------------------------
# django-unfold — "Signal" themed admin
# ---------------------------------------------------------------------------
def _badge_pending_orders(request):
    from orders.models import Order

    count = Order.objects.filter(status=Order.Status.PENDING).count()
    return str(count) if count else None


UNFOLD = {
    "SITE_TITLE": "QulaySIM Admin",
    "SITE_HEADER": "QulaySIM",
    "SITE_SUBHEADER": "eSIM commerce control panel",
    "SITE_SYMBOL": "sim_card",
    "THEME": "light",
    "SHOW_HISTORY": True,
    "SHOW_VIEW_ON_SITE": False,
    "BORDER_RADIUS": "12px",
    "DASHBOARD_CALLBACK": "config.dashboard.dashboard_callback",
    "STYLES": [
        lambda request: static("admin/qulaysim-admin.css"),
    ],
    # DON back-office palette: deep navy structure + gold brand accent.
    "COLORS": {
        "base": {
            "50": "245 246 248",
            "100": "235 237 241",   # divider
            "200": "230 232 236",
            "300": "203 209 218",
            "400": "137 148 176",   # gray-2
            "500": "96 107 139",    # secondary
            "600": "63 74 99",
            "700": "37 48 74",
            "800": "28 40 54",      # sidebar hover #1c2836
            "900": "17 30 43",      # primary #111e2b
            "950": "16 16 23",      # darkest #101017
        },
        "primary": {
            "50": "255 248 236",
            "100": "253 236 200",
            "200": "251 216 141",
            "300": "255 203 105",   # brand gold #ffcb69
            "400": "247 183 51",
            "500": "243 156 18",    # #f39c12
            "600": "229 149 0",     # #e59500
            "700": "184 116 10",
            "800": "143 90 6",
            "900": "95 61 0",
            "950": "61 39 0",
        },
        "font": {
            "subtle-light": "var(--color-base-500)",
            "subtle-dark": "var(--color-base-400)",
            "default-light": "var(--color-base-700)",
            "default-dark": "var(--color-base-200)",
            "important-light": "var(--color-base-900)",
            "important-dark": "var(--color-base-50)",
        },
    },
    "SIDEBAR": {
        "show_search": True,
        "show_all_applications": False,
        "navigation": [
            {
                "title": "Overview",
                "separator": False,
                "items": [
                    {
                        "title": "Dashboard",
                        "icon": "dashboard",
                        "link": "/admin/",
                    },
                ],
            },
            {
                "title": "Catalog",
                "separator": True,
                "items": [
                    {"title": "Regions", "icon": "public", "link": "/admin/catalog/region/"},
                    {"title": "Countries", "icon": "flag", "link": "/admin/catalog/country/"},
                    {"title": "Plans", "icon": "sim_card", "link": "/admin/catalog/plan/"},
                ],
            },
            {
                "title": "Commerce",
                "separator": True,
                "items": [
                    {
                        "title": "Orders",
                        "icon": "receipt_long",
                        "link": "/admin/orders/order/",
                        "badge": "config.settings._badge_pending_orders",
                    },
                    {"title": "eSIMs", "icon": "qr_code_2", "link": "/admin/orders/esim/"},
                    {"title": "Payments", "icon": "payments", "link": "/admin/orders/payment/"},
                    {"title": "Payme", "icon": "account_balance", "link": "/admin/orders/paymetransaction/"},
                    {"title": "Promo codes", "icon": "sell", "link": "/admin/orders/promocode/"},
                ],
            },
            {
                "title": "People",
                "separator": True,
                "items": [
                    {"title": "Customers", "icon": "group", "link": "/admin/customers/customer/"},
                    {"title": "Staff users", "icon": "shield_person", "link": "/admin/auth/user/"},
                ],
            },
            {
                "title": "Content",
                "separator": True,
                "items": [
                    {"title": "FAQ", "icon": "quiz", "link": "/admin/content/faq/"},
                    {"title": "Banners", "icon": "ad", "link": "/admin/content/banner/"},
                    {"title": "Testimonials", "icon": "reviews", "link": "/admin/content/testimonial/"},
                ],
            },
        ],
    },
}


# ---------------------------------------------------------------------------
# Logging
#
# There was none, so a production failure left no trace anywhere.
# ---------------------------------------------------------------------------
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {"format": "{levelname} {asctime} {name} {message}", "style": "{"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "verbose"},
    },
    "root": {"handlers": ["console"], "level": config("LOG_LEVEL", default="INFO")},
    "loggers": {
        "django.security": {"handlers": ["console"], "level": "INFO", "propagate": False},
        "django.request": {"handlers": ["console"], "level": "WARNING", "propagate": False},
    },
}
