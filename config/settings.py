"""
Django settings for FastSIM admin panel.

Django is the SCHEMA OWNER for the whole platform: all models and migrations
live here, and the FastAPI service reads/writes the same PostgreSQL tables via
SQLAlchemy. The admin UI is themed with django-unfold ("Signal" palette).
"""

from pathlib import Path

from decouple import Csv, config
from django.templatetags.static import static

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = config("SECRET_KEY", default="dev-secret-key")
DEBUG = config("DEBUG", default=True, cast=bool)
ALLOWED_HOSTS = config("ALLOWED_HOSTS", default="*", cast=Csv())


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
    # FastSIM apps
    "catalog",
    "customers",
    "orders",
    "content",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
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

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
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
    "SITE_TITLE": "FastSIM Admin",
    "SITE_HEADER": "FastSIM",
    "SITE_SUBHEADER": "eSIM commerce control panel",
    "SITE_SYMBOL": "sim_card",
    "THEME": "light",
    "SHOW_HISTORY": True,
    "SHOW_VIEW_ON_SITE": False,
    "BORDER_RADIUS": "12px",
    "DASHBOARD_CALLBACK": "config.dashboard.dashboard_callback",
    "STYLES": [
        lambda request: static("admin/fastsim-admin.css"),
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
