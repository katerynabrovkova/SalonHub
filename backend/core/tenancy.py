"""
The tenant-context mechanism that makes an unscoped query the hard path.

See docs/ARCHITECTURE.md § 5. `tenants.middleware.TenantResolutionMiddleware`
binds this for requests under /api/v1/salons/<slug>/... (Stage 3); everything
else — tests, Celery tasks, and Django admin (core.admin) — binds it
explicitly via `tenant_context`, or bypasses it deliberately via
`Model.unscoped_objects`.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

_current_salon_id: ContextVar[int | None] = ContextVar("current_salon_id", default=None)


class TenantContextMissingError(RuntimeError):
    """
    Raised when tenant-scoped ORM access happens with no bound tenant context.

    Deliberately a RuntimeError, not a core.exceptions.DomainError: this is a
    programming mistake (a code path that forgot to bind the tenant, or that
    should have used `unscoped_objects`), never a condition an API view should
    translate into a response for a client.
    """


class TenantContextMismatchError(RuntimeError):
    """
    Raised when a function given an explicit salon runs under a different
    bound tenant context. A programming mistake, like
    TenantContextMissingError: tenant-scoped queries would silently use
    the bound salon while writes used the given one.
    """


@contextmanager
def tenant_context(salon_id: int) -> Iterator[None]:
    """Bind the current tenant for the duration of the block. Also usable as a decorator."""
    token = _current_salon_id.set(salon_id)
    try:
        yield
    finally:
        _current_salon_id.reset(token)


def get_current_salon_id() -> int | None:
    return _current_salon_id.get()
