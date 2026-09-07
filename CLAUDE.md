# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

**Before changing anything architectural — tenancy, data model shape, payment/notification
abstractions, or anything else that isn't a local implementation detail — read
`docs/DECISIONS.md` first.** It is the source of truth for *why* the system is shaped the way
it is. If you make a new architectural decision, record it there in the same change.

**A `docs/DECISIONS.md` entry records what was agreed and its date — not what you
concluded, and not a review process.** Never write an entry to notarize a change you
already made: if a decision needed approval and didn't get it first, the write-up must
say so plainly (what changed, what alternative existed, that approval came after the
fact). Don't describe proposal/review/approval machinery ("reviewed and approved," etc.)
that didn't happen — decisions here are agreed in discussion, not through a formal
pipeline. Describing a process that didn't happen is the same error as recording a fact
that isn't true.

**The two docs split along "what" vs. "why," not by topic.** `docs/ARCHITECTURE.md`
describes what this system is — domain entities, mechanisms, invariants — and stays at
that level even as later stages land; `docs/DECISIONS.md` carries why a given shape was
chosen, including the concrete detail that never makes it into `ARCHITECTURE.md` at all:
endpoint lists, permission-class splits, specific exception classes, and alternatives
that were considered and rejected. Catalog is the precedent: its CRUD endpoints, the
`AllowAny`/`IsSalonStaff` read/write split, and `CategoryHasActiveServicesError` appear
only in `docs/DECISIONS.md` § Stage 4 decisions, never in `ARCHITECTURE.md`. Don't add
that level of detail to `ARCHITECTURE.md` for a later stage just because it feels like
it belongs there — matching precedent avoids an asymmetry that would mislead whoever
reads it next expecting the same split to hold.

**The following require an explicit decision point, raised and approved *before* you
write the change, never folded into a diff for later review:** any change to
`AUTH_USER_MODEL` or its shape, any change to a model's `Meta` (constraints, ordering,
managers), any change to which manager is default/first-declared, and any migration
containing `RemoveField`, `AlterField`, or `DeleteModel`. Generating the migration to see
what it contains is fine; applying it, or writing the model change that produces it,
is not, until approved.

## Stage-by-stage workflow

This project is built one stage at a time, in the order recorded in
`docs/DECISIONS.md` under "Agreed stage order." Do not implement ahead of the
current stage — e.g. don't add domain apps, models, or endpoints that belong
to a later stage.

Before implementing any stage:

1. Analyze the requirements for that stage.
2. Explain the proposed approach.
3. State assumptions explicitly — do not silently invent requirements.
4. Identify edge cases.
5. Propose the relevant files/architecture.
6. **Wait for explicit instruction before writing code.**

When implementing:

- Make small, logically grouped changes; don't rewrite unrelated code.
- Don't silently change architecture — if something in `docs/DECISIONS.md`
  needs to change, say so and update it in the same change.
- Run the relevant tests/checks after implementing and report the results.
- **An empty `git diff` proves nothing for an untracked (`??`) file** — Git has no
  baseline, so the diff is empty regardless of content, whether or not a revert
  happened. This bites mid-stage, when a substep's new files are still untracked and you
  need to verify an experiment (e.g. a deliberate break-it-to-prove-the-test exercise)
  was undone. Verify by reading the file directly or re-running its tests, or `git add`
  before experimenting so a baseline exists. Same caution for proving a test red/green:
  a run under `-k`, `-x`, `-m`, or `--lf` only proves it "for whatever subset got
  selected" — confirm the specific test actually ran, by running it unfiltered or
  quoting its verbatim pytest result line, not a summary of a differently-scoped run.

## Project purpose

A production-quality, reusable multi-tenant SaaS platform for beauty salons: client-facing
booking (browse services, book a specialist, pay a deposit, manage/cancel appointments, leave
reviews) plus a salon admin back-office, plus an AI assistant grounded in the salon's real
service catalog. The first tenant is a demo "universal" salon (manicure, pedicure, brows,
lashes, sugaring, laser hair removal); the architecture must support additional independent
salons without rework.

## Tech stack

- **Backend:** Python, Django, PostgreSQL, Celery + Redis, pytest — wired up as of Stage 0.
  Django REST Framework and JWT auth (DRF SimpleJWT) landed in Stage 3. OpenAPI/Swagger is
  still part of the plan but **not yet added** (lands with the stage that needs it, per
  `docs/DECISIONS.md`'s stage order). Exact
  versions live in `backend/requirements/*.txt`, not here — that file is the source of truth
  and this one will drift if it restates numbers. Version-compatibility reasoning (LTS choice,
  why some packages are deliberately not pinned to their newest release) lives in
  `docs/DECISIONS.md`.
- **Frontend:** Next.js / React, TypeScript (not started yet — backend-first, see
  `docs/DECISIONS.md`).
- **AI:** LLM access through a provider-agnostic abstraction (not started yet).
- Everything runs in Docker locally; **PostgreSQL is the database in every environment,
  including tests — never SQLite.**

## Architectural rules

- Modular monolith: one Django project, domain-bounded apps (`catalog`, `scheduling`,
  `booking`, `payments`, `notifications`, `reviews`, `ai_assistant`, etc.). No microservices
  without a concrete technical justification.
- Multi-tenancy is shared-database/shared-schema with row-level isolation: every tenant-owned
  model carries a `salon` FK, enforced through a shared base manager/queryset — never rely on
  each view remembering to filter by tenant.
- On every `TenantScopedModel` subclass, `objects` must be declared before `unscoped_objects`.
  Django treats the first manager declared in a model body as its default manager regardless of
  name; reordering would silently make the unfiltered `unscoped_objects` manager the default,
  defeating tenant isolation wherever Django uses the default manager internally (reverse
  relations, admin, etc.). ruff's DJ012 actively suggests this reorder as a style fix — the
  `# noqa: DJ012` on `unscoped_objects` in `core/models.py` is deliberate; do not "fix" it.
- `Appointment` always references `Customer`, never `User` directly — see `docs/DECISIONS.md`
  for the guest/registered customer model.
- Business logic (availability computation, cancellation/refund eligibility, booking
  concurrency guarantees) lives in a service layer, not in views/serializers, so it's testable
  independently and reusable from Celery tasks and the AI assistant.
- External integrations (payments, notifications) are built behind a provider interface first,
  with a real adapter (Stripe, email, Telegram, ...) added behind it — never coupled directly
  to one vendor's SDK from business logic. Tests must never require network access.
- No hardcoded secrets or credentials, ever. All configuration is environment-driven
  (`django-environ`); `.env` is gitignored, `.env.example` documents every variable.
- **DRF object-level permissions (`has_object_permission`) never fire on their own for
  list/collection views** — DRF only calls `check_object_permissions()` when a view
  explicitly triggers it (its generic views do this automatically inside
  `get_object()`; a plain `APIView` must call it itself). A permission class that puts
  its real check only in `has_object_permission` is a silent no-op on any view shape
  that never reaches that call — nothing stops a future list endpoint from citing that
  permission class and exposing everything. Every permission class must therefore
  enforce in `has_permission` whatever it can without the object (explicit required
  marker attributes with no silently-safe default, credential/token validation that
  doesn't depend on the specific object, etc.), and object-scoped views should prefer
  DRF generics specifically because their `get_object()` guarantees the call. See
  `core/permissions.py`'s `HasValidGuestToken` and `booking/views.py`'s
  `_GuestTokenAppointmentMixin` for the pattern: `has_permission` does the real
  validation and resolves which object to act on from the credential itself (never
  from a URL parameter, which a view could be tricked into using without the
  corresponding object-permission check ever running); `has_object_permission` is only
  a redundant confirmation once the object is fetched.
- **A DRF `ModelSerializer`'s automatic `UniqueTogetherValidator` silently does nothing
  for any `(salon, X)` uniqueness constraint** — `salon` is read-only with no default,
  and DRF drops any constraint whose fields aren't all writable-or-defaulted, with no
  error raised. Every `TenantScopedModel` with a `(salon, X)` constraint hits this.
  Never rely on the automatic validator: add an explicit `validate_<field>` that checks
  the already-tenant-scoped manager (see `catalog/serializers.py`). That check isn't
  race-proof — the DB constraint is the real guarantee, and
  `core.exceptions.exception_handler` translates the resulting `UniqueViolation` into a
  structured 400 as a backstop. Full mechanism: `docs/DECISIONS.md` § Stage 4.
- **A `many=True` relational field is not what ends up in `self.fields`.** DRF wraps it
  in a `ManyRelatedField`; the instance holding the real `queryset` lives on
  `.child_relation`. When rebinding a tenant-scoped queryset in `__init__` for a
  `many=True` field, target `self.fields["<name>"].child_relation.queryset`, never
  `self.fields["<name>"].queryset` — the latter silently sets an unused attribute and
  leaves validation running against the empty class-body placeholder, rejecting every id
  from every tenant. Recurs on every future `many=True` tenant-scoped relation. Full
  account: `docs/DECISIONS.md` § Stage 5.

## Coding conventions

- Type hints throughout; `mypy` (with `django-stubs`) must pass.
- Formatting/linting: `ruff` (lint + format), configured in `backend/pyproject.toml`.
- Tests: `pytest` + `pytest-django`, against a real PostgreSQL instance.

## Running the project

From the repository root:

```bash
cp .env.example .env        # first time only
docker compose build
```

`.env.example` ships `DJANGO_SECRET_KEY=change-me-to-a-random-value-in-every-environment` as a
literal placeholder — it is not safe to run with as-is. Generate a real one and put it in
`.env`:

```bash
docker compose run --rm backend python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"
```

```bash
docker compose up
```

This starts PostgreSQL, Redis, the Django dev server (`backend`), a Celery worker, and Celery
beat. The backend is served on `http://localhost:${HOST_BACKEND_PORT}` (default `8000`).
Postgres, Redis, and the backend each have an independently configurable host port
(`HOST_DB_PORT` / `HOST_REDIS_PORT` / `HOST_BACKEND_PORT` in `.env`) — the in-network service
addresses (`db:5432`, `redis:6379`) never change, only what's exposed to your machine. Override
these when the defaults collide with another project's containers on the same host.

Run Django management commands inside the `backend` container, e.g.:

```bash
docker compose exec backend python manage.py migrate
docker compose exec backend python manage.py createsuperuser
```

### Windows / PowerShell notes

Environment traps hit while working on this repo from a Windows/PowerShell host —
recorded so they aren't rediscovered stage by stage:

- **Unix-style commands are not portable in this shell.** `find / -name "x.py"` walks
  the entire filesystem and hangs indefinitely; it left a stray background process
  running for three turns during Stage 5. Locate files via `git`, ripgrep, or a known
  path instead.
- **`curl` is a PowerShell alias for `Invoke-WebRequest`, not real curl.** Use
  `curl.exe` to get actual curl behavior.
- **Multi-line `git commit -m "line1\nline2"` does not work** — the remaining lines
  after the first execute as separate shell commands. Use several separate `-m` flags
  instead, one per paragraph.
- **Run every `manage.py` command inside the container**
  (`docker compose exec backend ...`), never from the host venv — even for commands
  that don't touch the database (e.g. `makemigrations`). The host venv's Django exists
  for the IDE's benefit, not for running this project, and its version isn't
  guaranteed to match the container's.
- **Never assume the host port.** `HOST_BACKEND_PORT` is per-developer and is not
  `8000` on every machine — read it from `.env`, or reach the backend from inside the
  Docker network at `backend:8000`, where the port never varies.

## Running tests and linters

```bash
docker compose exec backend pytest
docker compose exec backend ruff check .
docker compose exec backend ruff format --check .
docker compose exec backend mypy .
```