"""
Django settings for the QulaySIM admin panel.

Django is the SCHEMA OWNER for the whole platform: all models and migrations
live here, and the FastAPI service reads/writes the same PostgreSQL tables via
SQLAlchemy. The admin UI is themed with django-unfold ("Signal" palette).
"""

from datetime import timedelta
from pathlib import Path

from django.urls import reverse_lazy
from django.utils.translation import gettext_lazy as _

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
    # Locks the admin login out after repeated failures.
    "axes",
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
    # Order matters: after the session, which is where the chosen language is
    # stored, and before CommonMiddleware.
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    # Must be last: it wraps authentication so a lockout is enforced after the
    # session and auth middleware have run.
    "axes.middleware.AxesMiddleware",
]

# Axes must come first so a locked-out attempt is refused before the password
# is ever checked. ModelBackend stays as the fallback that actually
# authenticates the survivors.
AUTHENTICATION_BACKENDS = [
    "axes.backends.AxesStandaloneBackend",
    "django.contrib.auth.backends.ModelBackend",
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

# Suppliers the fulfilment service can actually place orders with. The FastAPI
# side registers its supplier integrations in app/integrations/suppliers.py;
# this list must not name anyone missing from that registry. Offers from other
# suppliers may be recorded for price comparison, but they must never win a
# plan's sourcing: a plan priced from a supplier we cannot buy from is either
# sold at a loss (the order falls back to a dearer route) or, with no fallback,
# paid for and never provisioned.
#
# eSIMCard joined the list once its ordering code existed (an API client, and the
# purchase ledger that stops a retry buying a second eSIM from an endpoint with no
# idempotency key). Listed here means "we have code that can buy from it" and
# nothing more — the API still needs a token in the backend's environment and a
# funded wallet, and neither of those is something this setting can know about.
# The price sheet posts one field per tariff, and the catalogue is 1377 tariffs
# and growing. Django's default of 1000 made "all on one page" silently refuse
# every save with TooManyFieldsSent — the operator typed a price, pressed save,
# and got a bare 400 with no hint that the page size was the reason.
#
# Raised rather than removed: the cap exists to blunt hash-collision DoS, and a
# ceiling of 8000 still bounds that while leaving room for the catalogue to
# double. The per-row save button means a normal edit sends three fields, so
# this only matters for a bulk save.
DATA_UPLOAD_MAX_NUMBER_FIELDS = config(
    "DATA_UPLOAD_MAX_NUMBER_FIELDS", default=8000, cast=int
)

FULFILLABLE_PROVIDERS = config(
    "FULFILLABLE_PROVIDERS", default="esimaccess,esimcard", cast=Csv()
)

# ---------------------------------------------------------------------------
# Wholesaler API credentials, read by `manage.py sync_catalog`.
#
# The catalogue is supposed to come from these, not from a CSV somebody
# remembers to download: a hand-maintained catalogue drifts the moment the
# person maintaining it is busy, and the destinations that never got typed in
# were simply not for sale.
#
# Same variable names as the backend uses, so one .env file serves both and
# there is no second copy of a credential to get out of step.
# ---------------------------------------------------------------------------
ESIMACCESS_BASE_URL = config("ESIMACCESS_BASE_URL", default="https://api.esimaccess.com")
ESIMACCESS_ACCESS_CODE = config("ESIMACCESS_ACCESS_CODE", default="")
ESIMACCESS_SECRET_KEY = config("ESIMACCESS_SECRET_KEY", default="")

# NOT esimcard.com — that host answers every API path with HTTP 410.
ESIMCARD_BASE_URL = config(
    "ESIMCARD_BASE_URL", default="https://portal.esimcard.com/api/developer/reseller"
)
ESIMCARD_API_TOKEN = config("ESIMCARD_API_TOKEN", default="")

# ---------------------------------------------------------------------------
# Brute-force protection (django-axes)
#
# Without this the admin login accepts unlimited attempts, which leaves a strong
# password as the only barrier and makes the obscure path do security work it
# should not have to.
#
# Locking on username AND IP together, rather than IP alone: a single attacker
# behind one address should not be able to lock a real administrator out of
# their own account by failing logins against it.
# ---------------------------------------------------------------------------
AXES_FAILURE_LIMIT = config("AXES_FAILURE_LIMIT", default=6, cast=int)
AXES_COOLOFF_TIME = timedelta(minutes=config("AXES_COOLOFF_MINUTES", default=30, cast=int))
AXES_LOCKOUT_PARAMETERS = [["username", "ip_address"]]
AXES_RESET_ON_SUCCESS = True
AXES_ENABLE_ADMIN = True
AXES_VERBOSE = True
# Caddy terminates TLS, so REMOTE_ADDR is the proxy. Left uncorrected, every
# attempt looks like one client and the per-IP lockout becomes a global account
# lockout that anyone could trigger against a known username.
#
# django-axes reads the client address through django-ipware, which it treats as
# an OPTIONAL dependency — without it installed these settings are ignored
# entirely and it falls back to REMOTE_ADDR without complaint. It is a hard
# requirement here.
#
# Caddy appends the real peer address to X-Forwarded-For, so a client that sends
# its own header ends up left of the trustworthy entry. Reading RIGHT-most
# therefore takes Caddy's value and ignores anything the client seeded.
AXES_IPWARE_META_PRECEDENCE_ORDER = ["HTTP_X_FORWARDED_FOR", "REMOTE_ADDR"]
AXES_IPWARE_PROXY_ORDER = "right-most"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 8},
    },
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# The staff who use this admin work in Uzbek; Russian and English are here
# because the back office is shared with the supplier side. Django ships
# translations for all three, so most of the interface is covered without any
# .po file of our own — only our own strings need one.
LANGUAGE_CODE = "uz"
LANGUAGES = [
    ("uz", "O‘zbekcha"),
    ("ru", "Русский"),
    ("en", "English"),
]
LOCALE_PATHS = [BASE_DIR / "locale"]
# Display only — USE_TZ keeps storage in UTC. Staff read "today" as Tashkent
# today, which is what the dashboard's day boundaries should mean.
TIME_ZONE = "Asia/Tashkent"
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
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}
# Hashed filenames, so a year of caching is safe: a changed file gets a new name
# and the browser fetches it immediately, while an unchanged one is never asked
# about again.
#
# The alternative — revalidating thirteen assets on every page load — costs a
# round trip each, and this admin is used over a phone tether where a round trip
# is 130 ms. WhiteNoise serves the hashed copies with `immutable` on its own; this
# value covers the handful of files that keep their plain name.
WHITENOISE_MAX_AGE = 31536000

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


# Django's default is /accounts/profile/, which this project does not serve — so
# signing in from a login page that carries no ?next= landed on a 404. The normal
# path (admin.qulaysim.uz → admin → login?next=…) was unaffected, which is why it
# went unnoticed; a bookmarked login URL was not.
LOGIN_REDIRECT_URL = reverse_lazy("admin:index")
LOGOUT_REDIRECT_URL = reverse_lazy("admin:login")


UNFOLD = {
    "SITE_TITLE": _("QulaySIM Admin"),
    "SITE_HEADER": _("QulaySIM"),
    "SITE_SUBHEADER": _("eSIM commerce control panel"),
    "SITE_SYMBOL": "sim_card",
    # The real wordmark, so the admin and the storefront carry the same brand.
    #
    # One file per theme, not currentColor. The trick of filling "Qulay" with
    # currentColor works for an inlined SVG but not for one referenced from an
    # <img>: that document cannot see the page's text colour, so the glyphs fell
    # back to black and disappeared against the dark sidebar. Unfold takes a
    # {light, dark} pair and swaps them with a CSS class, which also means the
    # right file is chosen by Unfold's own theme toggle rather than by the
    # operating system's preference.
    "SITE_ICON": lambda request: static("admin/qulaysim-favicon.svg"),
    "SITE_LOGO": {
        "light": lambda request: static("admin/qulaysim-logo.svg"),
        "dark": lambda request: static("admin/qulaysim-logo-dark.svg"),
    },
    "SITE_FAVICONS": [
        {
            "rel": "icon",
            "type": "image/svg+xml",
            "href": lambda request: static("admin/qulaysim-favicon.svg"),
        }
    ],
    # The "THEME" key is deliberately absent: setting it *forces* that theme and
    # removes Unfold's light/dark switcher from the header. Leaving it out gives
    # each staff member auto|light|dark, persisted per browser.
    "SHOW_LANGUAGES": True,
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
                "title": _("Overview"),
                "separator": False,
                "items": [
                    {
                        "title": _("Dashboard"),
                        "icon": "dashboard",
                        "link": reverse_lazy("admin:index"),
                    },
                ],
            },
            {
                "title": _("Catalog"),
                "separator": True,
                "items": [
                    {"title": _("Regions"), "icon": "public", "link": reverse_lazy("admin:catalog_region_changelist")},
                    {
                        "title": _("Destinations"),
                        "icon": "grid_view",
                        "link": reverse_lazy("admin:catalog_country_board"),
                    },
                    {"title": _("Countries"), "icon": "flag", "link": reverse_lazy("admin:catalog_country_changelist")},
                    {"title": _("Plans"), "icon": "sim_card", "link": reverse_lazy("admin:catalog_plan_changelist")},
                    {
                        "title": _("Cost and price sheet"),
                        "icon": "price_change",
                        "link": reverse_lazy("admin:catalog_plan_price_sheet"),
                    },
                    {
                        "title": _("Supplier prices"),
                        "icon": "compare_arrows",
                        "link": reverse_lazy("admin:catalog_supplieroffer_changelist"),
                    },
                    {
                        "title": _("Sync prices"),
                        "icon": "upload_file",
                        "link": reverse_lazy("admin:catalog_plan_import_prices"),
                    },
                    {
                        "title": _("Pricing rules"),
                        "icon": "percent",
                        "link": reverse_lazy("admin:catalog_pricingrule_changelist"),
                    },
                ],
            },
            {
                "title": _("Commerce"),
                "separator": True,
                "items": [
                    {
                        "title": _("Orders"),
                        "icon": "receipt_long",
                        "link": reverse_lazy("admin:orders_order_changelist"),
                        "badge": "config.settings._badge_pending_orders",
                    },
                    {"title": _("eSIMs"), "icon": "qr_code_2", "link": reverse_lazy("admin:orders_esim_changelist")},
                    {"title": _("Payments"), "icon": "payments", "link": reverse_lazy("admin:orders_payment_changelist")},
                    {
                        "title": _("Card transactions"),
                        "icon": "credit_card",
                        "link": reverse_lazy("admin:orders_atmostransaction_changelist"),
                    },
                    {
                        "title": _("Supplier purchases"),
                        "icon": "shopping_cart_checkout",
                        "link": reverse_lazy("admin:orders_supplierpurchase_changelist"),
                    },
                    {"title": _("Promo codes"), "icon": "sell", "link": reverse_lazy("admin:orders_promocode_changelist")},
                ],
            },
            {
                "title": _("People"),
                "separator": True,
                "items": [
                    {"title": _("Customers"), "icon": "group", "link": reverse_lazy("admin:customers_customer_changelist")},
                    {
                        "title": _("Cashback & referrals"),
                        "icon": "redeem",
                        "link": reverse_lazy("admin:customers_referral_changelist"),
                    },
                    {"title": _("Staff users"), "icon": "shield_person", "link": reverse_lazy("admin:auth_user_changelist")},
                    {
                        "title": _("Social logins"),
                        "icon": "link",
                        "link": reverse_lazy("admin:customers_socialaccount_changelist"),
                    },
                ],
            },
            {
                "title": _("Content"),
                "separator": True,
                "items": [
                    {"title": _("FAQ"), "icon": "quiz", "link": reverse_lazy("admin:content_faq_changelist")},
                    {"title": _("Banners"), "icon": "ad", "link": reverse_lazy("admin:content_banner_changelist")},
                    {"title": _("Testimonials"), "icon": "reviews", "link": reverse_lazy("admin:content_testimonial_changelist")},
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
