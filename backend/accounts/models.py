from typing import ClassVar

from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.contrib.auth.models import AbstractUser
from django.db import models

from core.models import TenantScopedManager, TenantScopedModel, TimeStamped
from tenants.models import Salon


class UserManager(BaseUserManager["User"]):
    """
    email is USERNAME_FIELD (see User below), so Django's default
    UserManager — built around `username` — doesn't apply. use_in_migrations
    so `migrate`/`createsuperuser` can rely on this manager existing.
    """

    use_in_migrations = True

    def _create_user(self, email: str, password: str | None, **extra_fields: object) -> "User":
        if not email:
            raise ValueError("Users must have an email address.")
        user = self.model(email=self.normalize_email(email), **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(
        self, email: str, password: str | None = None, **extra_fields: object
    ) -> "User":
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)
        return self._create_user(email, password, **extra_fields)

    def create_superuser(
        self, email: str, password: str | None = None, **extra_fields: object
    ) -> "User":
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        if extra_fields.get("is_staff") is not True:
            raise ValueError("Superuser must have is_staff=True.")
        if extra_fields.get("is_superuser") is not True:
            raise ValueError("Superuser must have is_superuser=True.")
        return self._create_user(email, password, **extra_fields)


class User(AbstractUser):
    """
    Platform-wide auth identity (docs/DECISIONS.md § Identity). A thin
    subclass of AbstractUser — made custom from Stage 0, not later, because
    swapping AUTH_USER_MODEL after the first migration is a disruptive,
    hard-to-reverse change; this keeps the door open at zero present cost.

    `username` is dropped and `email` is the login identity (USERNAME_FIELD)
    — docs/ARCHITECTURE.md § 3 already committed to "standard email +
    password" login, which needs a unique, login-bearing email field; this is
    that commitment's implementation, recorded in docs/DECISIONS.md § Stage 3
    decisions.
    """

    username = None  # type: ignore[assignment]
    email = models.EmailField(unique=True)

    # Nullable: unset means unverified. Before verification a user can log
    # in but cannot complete the guest->account merge (docs/ARCHITECTURE.md
    # § 3, docs/DECISIONS.md § Stage 3 decisions).
    email_verified_at = models.DateTimeField(null=True, blank=True)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS: ClassVar[list[str]] = []

    # django-stubs types AbstractUser.objects as django.contrib.auth.models.
    # UserManager[User]; our UserManager is a from-scratch BaseUserManager
    # subclass (Django's own UserManager assumes `username`), so it isn't a
    # subtype of that stub's declared type. Same pattern as `username = None`
    # above — a known, standard friction point with a custom-email-login
    # manager, not a real type error.
    objects = UserManager()  # type: ignore[assignment, misc]


class Customer(TenantScopedModel, TimeStamped):
    """
    Per-salon identity, guest or registered (docs/DECISIONS.md § Identity).
    `Appointment` always references this, never `User` directly.
    """

    name = models.CharField(max_length=255)
    email = models.EmailField()
    phone = models.CharField(max_length=32)

    # Language a notification email to this customer is rendered in, set at
    # booking time (docs/DECISIONS.md § Stage 11.5 "Email language"). Blank/
    # "" means unset -> the send path falls back to English, same as the
    # global `en` fallback. Deliberately NOT `choices=[("en",...),("uk",...)]`:
    # § Stage 11.5 requires that "nothing in the storage or API shape may
    # assume exactly these two [languages]", and a choices= list bakes that
    # assumption into a migration and a validator. A short CharField keeps the
    # column language-list-agnostic, matching the JSONField translatable
    # fields.
    preferred_language = models.CharField(max_length=8, blank=True, default="")

    class Meta(TenantScopedModel.Meta):
        abstract = False
        constraints = [
            *TenantScopedModel.Meta.constraints,
            models.UniqueConstraint(fields=["salon", "email"], name="customer_salon_email_uniq"),
        ]

    def __str__(self) -> str:
        return f"{self.name} @ {self.salon}"


class AccountRole(models.TextChoices):
    CLIENT = "client", "Client"
    ADMIN = "admin", "Admin"


class AccountManager(TenantScopedManager["Account"], BaseUserManager["Account"]):
    """
    Deliberately BOTH TenantScopedManager and BaseUserManager, not just the
    latter: TenantScopedManager.get_queryset() (first in MRO, so it wins) is
    what makes `Account.objects` raise without a bound tenant context and
    filter to it when bound, same as every other TenantScopedModel — a bare
    BaseUserManager subclass would silently replace that with the plain,
    unfiltered models.Manager.get_queryset() instead. BaseUserManager
    contributes normalize_email (docs/DECISIONS.md § Stage 3-R decisions,
    "Account model, settled shape").
    """

    def create_account(
        self,
        *,
        salon: Salon,
        email: str,
        password: str | None = None,
        role: str = AccountRole.CLIENT,
        customer: "Customer | None" = None,
        **extra_fields: object,
    ) -> "Account":
        # password defaults to None (not left required-with-no-default) so
        # an omitted argument reaches this check and raises ValueError,
        # rather than failing at the call boundary with TypeError. salon has
        # no default: provisioning into no salon at all is a caller bug the
        # type system should catch immediately, not a runtime validation
        # outcome.
        if not password:
            raise ValueError("Account.objects.create_account() requires a non-empty password.")
        # Full lowercase — local part AND domain — not BaseUserManager's
        # stock domain-only normalize_email: "Alice@x.com" and "alice@x.com"
        # must collide at (salon, email) per the settled isolation decision.
        email = self.normalize_email(email).lower()
        account = self.model(salon=salon, email=email, role=role, customer=customer, **extra_fields)
        account.set_password(password)
        account.save(using=self._db)
        return account


class Account(AbstractBaseUser, TenantScopedModel, TimeStamped):
    """
    Per-salon login credential (docs/DECISIONS.md § Stage 3-R decisions,
    "Account model, settled shape"). AbstractBaseUser, not the full
    AbstractUser: password hashing and last_login are reused from Django's
    vetted machinery, but is_staff/is_superuser/groups/permissions stay on
    `User` — the deliberate cross-tenant `/admin/` exception, not something
    Account needs.
    """

    email = models.EmailField()
    role = models.CharField(max_length=32, choices=AccountRole.choices, default=AccountRole.CLIENT)
    is_active = models.BooleanField(default=True)
    email_verified_at = models.DateTimeField(null=True, blank=True)
    customer = models.OneToOneField(
        Customer, null=True, blank=True, on_delete=models.SET_NULL, related_name="account"
    )

    USERNAME_FIELD = "email"

    # django-stubs types TenantScopedModel.objects/unscoped_objects as class
    # variables of their declared manager types; redeclaring both here with
    # AccountManager/plain Manager instances reads as an instance-variable
    # override of a base-class variable to mypy. Same known friction as
    # `User.objects = UserManager()` above — not a real type error.
    objects = AccountManager()  # type: ignore[misc]
    # Account is the first TenantScopedModel subclass in this codebase to
    # declare its own `objects` — redeclaring unscoped_objects here too,
    # rather than leaving it to inherit implicitly from TenantScopedModel,
    # keeps the first-declared-manager-is-default rule (core/models.py)
    # explicit on THIS class instead of resting on untested cross-abstract-
    # base inheritance behavior.
    unscoped_objects = models.Manager()  # type: ignore[misc]  # noqa: DJ012

    class Meta(TenantScopedModel.Meta):
        abstract = False
        constraints = [
            *TenantScopedModel.Meta.constraints,
            models.UniqueConstraint(fields=["salon", "email"], name="account_salon_email_uniq"),
        ]

    def __str__(self) -> str:
        return f"{self.email} @ {self.salon} ({self.role})"
