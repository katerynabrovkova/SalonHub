"""
Customer resolution for the guest booking flow (docs/ARCHITECTURE.md § 3,
docs/DECISIONS.md § Stage 7.C-bis decisions).

`link_guest_customers` (the cross-salon guest -> `User` merge that ran on
email verification) was removed in Stage 3-R.D.2 (docs/DECISIONS.md); the
same-salon `Account` <-> `Customer` link that replaces it lands in
3-R.D.4.
"""

from accounts.models import Customer
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
    the newly supplied values on every booking, unconditionally — the risk
    (a typo, or someone else's details under a shared email, silently
    overwriting good data) is accepted in exchange for a self-correcting
    default with no per-field logic. A brand-new email creates a guest row
    (user=NULL).
    """
    customer, created = Customer.objects.get_or_create(
        salon=salon, email=email, defaults={"name": name, "phone": phone}
    )
    if not created:
        customer.name = name
        customer.phone = phone
        customer.save(update_fields=["name", "phone"])
    return customer
