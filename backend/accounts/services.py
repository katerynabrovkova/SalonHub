"""
Customer resolution for the guest booking flow (docs/ARCHITECTURE.md § 3,
docs/DECISIONS.md § Stage 7.C-bis decisions).

`link_guest_customers` (the cross-salon guest -> `User` merge that ran on
email verification) was removed in Stage 3-R.D.2 (docs/DECISIONS.md); the
same-salon `Account` <-> `Customer` link that replaces it lands in
3-R.D.4.
"""

from accounts.models import Account, Customer
from tenants.models import Salon


def get_or_create_guest_customer(*, salon: Salon, name: str, email: str, phone: str) -> Customer:
    """
    docs/DECISIONS.md § Stage 7.C-bis decisions. Uses the ORM's own
    get_or_create() (not a hand-written check-then-create) so the
    (salon, email) unique constraint (customer_salon_email_uniq) plus its
    internal savepoint-and-retry-on-IntegrityError closes the
    concurrent-same-email race with no extra code here. `salon` is passed
    explicitly to both the lookup and the create defaults: the tenant-scoped
    manager filters reads but never injects `salon` on write.

    Option A (decided): a returning guest's name/phone are overwritten with
    the newly supplied values on every booking — the risk (a typo, or
    someone else's details under a shared email, silently overwriting good
    data) is accepted in exchange for a self-correcting default with no
    per-field logic. A brand-new email creates a guest row (no linked
    Account).

    Exception: an existing Customer linked to an Account keeps its
    name/phone, since for a registered user the profile is the single
    source of truth (docs/DECISIONS.md § "Guest booking keeps a linked
    Customer's name and phone"). The booking still goes ahead with that
    Customer; the name/phone typed for it are not stored. The link is looked
    up through the tenant-scoped `Account.objects`, so only an Account in
    this salon counts.
    """
    customer, created = Customer.objects.get_or_create(
        salon=salon, email=email, defaults={"name": name, "phone": phone}
    )
    if not created and not Account.objects.filter(customer=customer).exists():
        customer.name = name
        customer.phone = phone
        customer.save(update_fields=["name", "phone"])
    return customer


def is_email_taken(*, account: Account, email: str) -> bool:
    """
    Whether ``email`` belongs to another Account or another Customer in the
    bound salon (docs/DECISIONS.md § "Item 8 decisions (change email)").
    The account itself and its own linked Customer do not count.

    Case-insensitive (``iexact``): guest booking stores ``Customer.email``
    as typed, so an exact match would miss ``Alice@X.com``. Both lookups go
    through the tenant-scoped managers, so other salons never count.
    """
    other_accounts = Account.objects.filter(email__iexact=email).exclude(pk=account.pk)
    other_customers = Customer.objects.filter(email__iexact=email)
    if account.customer_id is not None:
        other_customers = other_customers.exclude(pk=account.customer_id)
    return other_accounts.exists() or other_customers.exists()
