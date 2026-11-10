# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

**Before changing anything that isn't a local implementation detail (tenancy, data model
shape, payment, notification or assistant abstractions, auth, money), read the section of
`docs/DECISIONS.md` for that area.** It is the source of truth for *why* the system is shaped
the way it is, and its "Open items register" lists known gaps by when they are due. If you make
a new architectural decision, record it there in the same change, in the topic section where it
belongs: rewrite a changed rule in place, don't append a "superseded" note.

**A `docs/DECISIONS.md` entry records what was agreed and its date, not what you concluded,
and not a review process.** Never write an entry to notarize a change you already made: if a
decision needed approval and didn't get it first, say so plainly (what changed, what
alternative existed, that approval came after the fact). Decisions here are agreed in
discussion, not through a formal pipeline; describing a process that didn't happen is the same
error as recording a fact that isn't true. Don't record implementation history (test counts,
mutation runs, sub-step logs) there: code and tests are that record.

**The two docs split along "what" vs. "why".** `docs/ARCHITECTURE.md` describes what the system
is (entities, mechanisms, invariants) and stays at that level; `docs/DECISIONS.md` carries why a
shape was chosen, including the concrete detail that never goes into `ARCHITECTURE.md`:
endpoint lists, permission-class splits, specific exception classes, rejected alternatives.
Catalog is the precedent: its endpoints, the `AllowAny`/`IsSalonStaff` split and
`CategoryHasActiveServicesError` appear only in `docs/DECISIONS.md`.

**The following require an explicit decision point, raised and approved *before* you write the
change, never folded into a diff for later review:** any change to `AUTH_USER_MODEL` or its
shape, any change to a model's `Meta` (constraints, ordering, managers), any change to which
manager is default/first-declared, and any migration containing `RemoveField`, `AlterField`,
or `DeleteModel`. Generating the migration to see what it contains is fine; applying it, or
writing the model change that produces it, is not, until approved.

## Stage-by-stage workflow

This project is built one stage at a time, in the order recorded in `docs/DECISIONS.md` under
"Stage order". Do not implement ahead of the current stage (no domain apps, models or endpoints
that belong to a later stage).

Before implementing any stage:

1. Analyze the requirements for that stage.
2. Explain the proposed approach.
3. State assumptions explicitly; do not silently invent requirements.
4. Identify edge cases.
5. Propose the relevant files/architecture.
6. **Wait for explicit instruction before writing code.**

When implementing:

- Make small, logically grouped changes; don't rewrite unrelated code.
- Don't silently change architecture: if something in `docs/DECISIONS.md` needs to change, say
  so and update it in the same change.
- Run the relevant tests/checks after implementing and report the results.
- **An empty `git diff` proves nothing for an untracked (`??`) file:** Git has no baseline, so
  the diff is empty whether or not a revert happened. To verify an experiment (e.g. a
  deliberate break-it-to-prove-the-test mutation) was undone, read the file or re-run its
  tests, or `git add` before experimenting. Same for proving red/green: a run under `-k`, `-x`,
  `-m` or `--lf` only proves it for the selected subset; confirm the specific test ran by
  running it unfiltered or quoting its verbatim pytest result line.

## Project purpose

A production-quality, reusable multi-tenant SaaS platform for beauty salons: client-facing
booking (browse services, book a specialist, pay a deposit, manage/cancel appointments, leave
reviews), a salon admin back-office, and an AI assistant grounded in the salon's real data. The
demo tenant is `bella-demo` ("BellaBeauty"); the architecture must support additional
independent salons without rework.

## Tech stack

- **Backend:** Python, Django (5.2 LTS), Django REST Framework, SimpleJWT (cookie transport),
  PostgreSQL, Celery + Redis, pytest. OpenAPI/Swagger is not added yet. Exact versions live in
  `backend/requirements/*.txt`; why some pins are deliberately not the newest is in
  `docs/DECISIONS.md` § "Dependency pins".
- **Frontend:** Next.js (App Router), React, TypeScript, Tailwind, Vitest + Testing Library,
  in `frontend/`.
- **AI:** the assistant reaches the model through a provider interface: a mock provider by
  default and in all tests, an OpenAI adapter chosen by `ASSISTANT_PROVIDER`.
- Everything runs in Docker locally; **PostgreSQL is the database in every environment,
  including tests, never SQLite.**

## Architectural rules

- Modular monolith: one Django project, domain-bounded apps (`core`, `tenants`, `accounts`,
  `catalog`, `specialists`, `scheduling`, `booking`, `payments`, `notifications`, `reviews`,
  `ai_assistant`). No microservices without a concrete technical justification.
- Multi-tenancy is shared-schema with row-level isolation: every tenant-owned model carries a
  `salon` FK and is filtered by the tenant-scoped default manager, which raises without a bound
  tenant context. Never rely on a view remembering to filter by tenant. Code that runs outside a
  request (Celery tasks, streamed responses) binds `tenant_context` itself.
- On every `TenantScopedModel` subclass, `objects` must be declared before `unscoped_objects`.
  Django treats the first declared manager as the default; reordering would silently make the
  unfiltered manager the default wherever Django uses it internally (reverse relations, admin).
  ruff's DJ012 suggests exactly this reorder, so the `# noqa: DJ012` on `unscoped_objects` in
  `core/models.py` is deliberate; do not "fix" it.
- `Appointment` always references `Customer`, never `Account` or `User`.
- Business logic (availability, cancellation/refund eligibility, booking concurrency, money)
  lives in a service layer, not in views/serializers, so it is reusable from Celery tasks and
  the assistant. The frontend never computes money.
- External integrations (payments, notifications, the LLM) sit behind a provider interface;
  business logic never imports a vendor SDK. Tests never require network access.
- No hardcoded secrets or credentials. All configuration is environment-driven
  (`django-environ`); `.env` is gitignored and must never be read, printed or copied;
  `.env.example` documents every variable.

## Known traps

- **DRF object-level permissions (`has_object_permission`) never fire on their own for list
  views:** DRF only calls `check_object_permissions()` from generic views' `get_object()` (a
  plain `APIView` must call it itself). A permission class whose real check is only in
  `has_object_permission` is a silent no-op on any view that never reaches that call. Enforce
  in `has_permission` everything that doesn't need the object (explicit required marker
  attributes with no safe default, credential validation), resolve the object from the
  credential, never from a URL parameter, and prefer generics for object-scoped views. Pattern:
  `core/permissions.py`'s `HasValidGuestToken` and `booking/views.py`'s
  `_GuestTokenAppointmentMixin`.
- **A `ModelSerializer`'s automatic `UniqueTogetherValidator` silently does nothing for any
  `(salon, X)` constraint** (`salon` is read-only with no default, so DRF drops it with no
  error). Add an explicit `validate_<field>` against the tenant-scoped manager (see
  `catalog/serializers.py`); the DB constraint is the real guarantee and
  `core.exceptions.exception_handler` turns `UniqueViolation` into a structured 400.
- **A `many=True` relational field is wrapped in a `ManyRelatedField`:** when rebinding a
  tenant-scoped queryset in `__init__`, set `self.fields["<name>"].child_relation.queryset`,
  never `self.fields["<name>"].queryset` (that silently sets an unused attribute and every id
  from every tenant is rejected).
- **A distinct API error code needs its own `DomainError`:** the exception handler gives every
  DRF `ValidationError` the generic code `invalid`.
- **`Intl.NumberFormat` inserts U+00A0 (non-breaking space),** so a literal string passed to
  `getByText`/`queryByText` won't match; use a regex with `\s+` for whitespace runs, or a custom
  normalizer.
- **Under `vi.useFakeTimers()`, `userEvent` clicks hang**, even with `userEvent.setup({
  advanceTimers: vi.advanceTimersByTime })` (Testing Library advances fake timers only when a
  global `jest` exists). Use `fireEvent.click` and wrap `vi.advanceTimersByTime` in `act`. Don't
  click with real timers and switch to fake ones afterwards: a timer scheduled at click time
  escapes the fake clock, so the test can't catch it. See
  `app/PasswordField.test.tsx`.
- **A rejected promise returned from a `vi.fn()` mock can never be unhandled** (the spy
  attaches its own `.then`), so a test for a missing `.catch` passes vacuously. Return a promise
  from another V8 context (`runInNewContext("Promise.reject(...)")` from `node:vm`); see
  `app/login/page.test.tsx`.
- **In a Vitest `it.each` title, `$name_suffix` is read as the property `name_suffix`.** Put the
  variable at the end in parentheses, e.g. `"test_error_shows_fixed_message_($name)"`.
- **Mocked-fetch frontend tests cannot catch CORS mistakes.** A new custom request or response
  header needs a check in a real browser.

## Coding conventions

- Type hints throughout; `mypy` (with `django-stubs`) must pass.
- Formatting/linting: `ruff` (lint + format), configured in `backend/pyproject.toml`.
- Tests: `pytest` + `pytest-django` against real PostgreSQL. The assistant tests always run on
  the mock provider and a separate Redis DB, whatever `.env` says.
- **A test must never compare a hardcoded absolute date with the real `timezone.now()`**,
  directly or through production code (token expiry, lead time, max advance days...): it passes
  when written and fails once the calendar moves on (this has broken the baseline three times).
  Freeze now with `monkeypatch.setattr(timezone, "now", ...)`, or build dates relative to a
  frozen now.
- Isolation is two-level: across salons (two-salon tests) and across objects in the same salon.
  When code looks up a related row (payment, customer, appointment), add a test with two such
  rows in the same salon and act on the one with the higher pk.
- Frontend: never import `useRouter` from `next/navigation` directly (lint-enforced); use
  `lib/navigation/useNavigatingRouter.ts` so the navigation progress bar sees every navigation.

## Running the project

From the repository root:

```bash
cp .env.example .env        # first time only
docker compose build
docker compose up
```

`.env.example` ships a placeholder `DJANGO_SECRET_KEY` that is not safe to run with; generate a
real one:

```bash
docker compose run --rm backend python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"
```

`docker compose up` starts PostgreSQL, Redis, the Django dev server (`backend`), the Next.js
dev server (`frontend`), a Celery worker and Celery beat. Host ports are per developer
(`HOST_BACKEND_PORT` etc. in `.env`); the in-network addresses (`db:5432`, `redis:6379`,
`backend:8000`) never change. Salons are opened on their subdomain, e.g.
`http://bella-demo.localhost:<HOST_FRONTEND_PORT>`. Keep the frontend on port 3000 in dev:
the dev CORS regex and `CSRF_TRUSTED_ORIGINS` hardcode `:3000`, so another port breaks
credentialed calls.

Run Django management commands inside the `backend` container:

```bash
docker compose exec backend python manage.py migrate
docker compose exec backend python manage.py reset_bella_demo --yes   # dev only
docker compose exec backend python manage.py seed_bella_demo          # dev only
```

### Windows / PowerShell notes

- **Unix-style commands are not portable in this shell.** `find / -name "x.py"` walks the whole
  filesystem and hangs. Locate files via `git`, ripgrep or a known path.
- **`curl` is a PowerShell alias for `Invoke-WebRequest`.** Use `curl.exe`.
- **Multi-line `git commit -m "line1\nline2"` does not work:** use several separate `-m` flags.
- **Run every `manage.py`, npm and node command inside its container** (`docker compose exec
  backend ...`, `docker compose exec frontend ...`), never from the host, even commands that
  don't touch the database (e.g. `makemigrations`). The host venv exists for the IDE only and
  its versions may not match the container's. Host-side `frontend/node_modules` is
  intentionally shadowed by a named Docker volume, so an empty one is not an error and a
  host-side `npm install` has no effect.
- **Never assume the host port.** Read it from `.env`, or reach the backend from inside the
  Docker network at `backend:8000`.

## Running tests and linters

```bash
docker compose exec backend pytest
docker compose exec backend ruff check .
docker compose exec backend ruff format --check .
docker compose exec backend mypy .
docker compose exec frontend npm test
docker compose exec frontend npm run lint
docker compose exec frontend npm run typecheck
```

- **The green gate before any commit is the full project-wide run** of the backend checks, plus
  the frontend checks when frontend files changed, never a run limited to the changed files.
  Pre-existing failures in untouched files are reported to Katya, not silently fixed.
- **Errors inside `frontend/.next/` mean the generated types are corrupt** (`next dev` rewrites
  them non-atomically), not that the source is broken: `docker compose restart frontend` and
  re-run. `typecheck` passes `--incremental false` because `tsc` cannot write
  `tsconfig.tsbuildinfo` into the root-owned `/app` mount.
- **After `makemigrations`, run `ruff check --fix` and `ruff format` on the new migration
  files** (Django's own style fails `ruff format --check`).
- **After changing code that Celery runs (notifications, sweeps, tasks), restart the worker
  and beat** (`docker compose restart celery_worker celery_beat`): they do not reload code,
  so a manual check would exercise the old code.
- **Never run `npx <tool>` for a tool the project already has; use its npm script.** Outside
  `/app`, `npx tsc` downloaded an unrelated registry package instead of TypeScript.
- **Git:** never run commands that change the working tree, index, stash or history, and never
  create worktrees or branches; Katya commits. Some of these commands, `npx` inside the
  containers and reading `.env` are also denied in `.claude/settings.json`, but that list is not
  complete: the rules apply whether or not a command is denied.