"""
Settings shared by every environment.

Nothing here should hardcode a secret, a host, or a credential — everything
that varies between local/CI/production comes from the environment. See
docs/DECISIONS.md before changing anything here.
"""

import datetime as dt
from pathlib import Path

import environ

# backend/config/settings/base.py -> backend/config/settings -> backend/config -> backend/
BASE_DIR = Path(__file__).resolve().parent.parent.parent
REPO_ROOT = BASE_DIR.parent

env = environ.Env()
_env_file = REPO_ROOT / ".env"
if _env_file.exists():
    environ.Env.read_env(str(_env_file))

SECRET_KEY = env("DJANGO_SECRET_KEY")

DEBUG = False
ALLOWED_HOSTS: list[str] = []

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.postgres",
    "rest_framework",
    "rest_framework_simplejwt",
    "rest_framework_simplejwt.token_blacklist",
    "corsheaders",
    "core",
    "tenants",
    "accounts",
    "catalog",
    "specialists",
    "scheduling",
    "booking",
    "payments",
    "notifications",
    "reviews",
]

AUTH_USER_MODEL = "accounts.User"

MIDDLEWARE = [
    # As high as possible, and before CommonMiddleware in particular, per
    # django-cors-headers' own docs: it must see the request before any
    # middleware that can short-circuit with a response (CommonMiddleware's
    # APPEND_SLASH / redirect handling), so the CORS headers are attached to
    # those responses too.
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    # Last: see tenants.middleware.TenantResolutionMiddleware's docstring and
    # docs/DECISIONS.md § Stage 3 decisions for why.
    "tenants.middleware.TenantResolutionMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

DATABASES = {
    "default": env.db("DATABASE_URL"),
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# Storage is always UTC. Rendering a salon's local time is an application-
# layer concern (per-Salon timezone field), not a global Django setting —
# see docs/DECISIONS.md.
LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- Redis / Celery -------------------------------------------------------

REDIS_URL = env("REDIS_URL")

CELERY_BROKER_URL = env("CELERY_BROKER_URL")
CELERY_RESULT_BACKEND = env("CELERY_RESULT_BACKEND")
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_TIMEZONE = TIME_ZONE
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True

# --- Cache (DRF throttling) -------------------------------------------------
#
# DRF's throttle classes count requests through the Django cache. A separate
# Redis logical DB from the Celery broker (0) and result backend (1) —
# docs/DECISIONS.md § Stage 3 decisions — to keep throttle keys from ever
# colliding with Celery's own. LocMemCache (the Django default when CACHES is
# unset) is per-process: behind multiple gunicorn/uvicorn workers, each one
# would keep its own counter and the effective rate limit becomes
# (configured rate × worker count), silently.
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": env("REDIS_CACHE_URL"),
    }
}

# --- DRF / JWT ---------------------------------------------------------------

REST_FRAMEWORK = {
    # Fail closed: a future view that forgets its permission class requires
    # auth by default rather than being silently open (docs/DECISIONS.md §
    # Stage 3 decisions). Auth endpoints opt out individually with AllowAny.
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    # Retargeted onto Account in Stage 3-R.E (docs/DECISIONS.md) — see
    # accounts.authentication.AccountJWTAuthentication for the
    # identity_model claim guard this depends on.
    # Cookie transport first (browser clients, docs/DECISIONS.md § Stage 12),
    # header-based Bearer kept as a fallback for non-browser clients — the
    # cookie class returns None when its cookie is absent so DRF falls through.
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "accounts.authentication.AccountJWTCookieAuthentication",
        "accounts.authentication.AccountJWTAuthentication",
    ],
    # Fixed default page size, overridable up to a capped maximum
    # (docs/ARCHITECTURE.md § 13, core/pagination.py).
    "DEFAULT_PAGINATION_CLASS": "core.pagination.DefaultPagination",
    "DEFAULT_THROTTLE_CLASSES": ["rest_framework.throttling.ScopedRateThrottle"],
    # Rates and reasoning recorded in docs/DECISIONS.md § Stage 3 decisions
    # (register: § Stage 3-R.D.3). guest_token is predeclared for the Stage 3
    # sub-step that adds the endpoint it applies to. password_reset /
    # resend_verification are dormant from Stage 3-R.D.2 (the User-based
    # endpoints that used them were removed) until 3-R.D.4 / 3-R.D.5 re-mount
    # them under the salon prefix — kept as the same lines rather than
    # deleted-and-re-added. register matches those two: it also triggers a
    # third-party email send on our bill.
    "DEFAULT_THROTTLE_RATES": {
        "login": "5/min",
        "register": "3/hour",
        "password_reset": "3/hour",
        "resend_verification": "3/hour",
        "guest_token": "20/min",
    },
    "EXCEPTION_HANDLER": "core.exceptions.exception_handler",
}

SIMPLE_JWT = {
    # 15 min access / 7 day refresh, rotating with blacklist-after-rotation:
    # a stolen refresh token is single-use, dead the moment the legitimate
    # client rotates it. docs/DECISIONS.md § Stage 3 decisions.
    "ACCESS_TOKEN_LIFETIME": dt.timedelta(minutes=15),
    "REFRESH_TOKEN_LIFETIME": dt.timedelta(days=7),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
    "UPDATE_LAST_LOGIN": True,
}

# --- Email -------------------------------------------------------------------

EMAIL_BACKEND = env("EMAIL_BACKEND", default="django.core.mail.backends.smtp.EmailBackend")
EMAIL_HOST = env("EMAIL_HOST", default="localhost")
EMAIL_PORT = env.int("EMAIL_PORT", default=25)
EMAIL_HOST_USER = env("EMAIL_HOST_USER", default="")
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", default="")
EMAIL_USE_TLS = env.bool("EMAIL_USE_TLS", default=False)
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", default="no-reply@bella-beauty-salon.example")

# Base URL of the (not-yet-built, see docs/DECISIONS.md § Frontend cadence)
# frontend, used only to build clickable links in outgoing email. One-time
# credential tokens (email verification, password reset) travel in the URL
# fragment, never a query string — see docs/DECISIONS.md § Stage 3
# decisions (guest token transport). The guest access token is the
# exception: it travels in the URL path instead, being a persistent,
# long-lived resource link rather than a one-time credential — see
# docs/DECISIONS.md § Step (d) decisions (guest-token delivery).
FRONTEND_URL = env("FRONTEND_URL", default="http://localhost:3000")

# Django's own token generator (used for password reset) reads this directly.
# Short: a live reset token is the highest-value credential in this scheme
# and is normally acted on within minutes of being requested
# (docs/DECISIONS.md § Stage 3 decisions).
PASSWORD_RESET_TIMEOUT = 60 * 60  # 1 hour

# --- CORS ------------------------------------------------------------------
#
# The browser frontend calls this API from a different origin and must send
# the httpOnly session cookie, so credentialed cross-origin requests are
# allowed — see docs/DECISIONS.md § Stage 12.
CORS_ALLOW_CREDENTIALS = True
# TODO(Stage 12): set CORS_ALLOWED_ORIGINS (per-environment: dev localhost:3000
# vs the prod origin list — apex + per-salon subdomains?). This is an open
# question in docs/DECISIONS.md § Stage 12 ("CORS allowed-origins list, dev vs
# prod") and is deliberately left unset until resolved: with no allowlist
# django-cors-headers fails safe and blocks every cross-origin request. Do NOT
# substitute CORS_ALLOW_ALL_ORIGINS here.

# --- CSRF ------------------------------------------------------------------
#
# The Account session rides an httpOnly cookie, so unsafe cookie-authenticated
# requests are CSRF-checked manually via Django's engine (accounts/csrf.py,
# docs/DECISIONS.md § Stage 12). The csrftoken cookie must be JS-readable —
# the frontend echoes its value in the X-CSRFToken header — so HTTPONLY is
# explicitly False (also the Django default; stated here to make the intent
# visible against the httpOnly session cookies). SameSite matches the auth
# cookies. CSRF_COOKIE_SECURE is set in production.py alongside the other
# _SECURE flags. Cookie/header names stay at their Django defaults
# (csrftoken / X-CSRFToken).
CSRF_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_HTTPONLY = False
