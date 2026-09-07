# Architectural Decisions

Source of truth for architectural decisions on this project. Read it
before changing anything structural (tenancy, data-model shape,
payment/notification abstractions). If a decision here changes, update
this file in the same change — do not let code and this document drift.

This is the working copy: condensed for day-to-day agent use. The full
historical record (dated entries, rejected-alternative arguments,
per-commit process notes, the Stage 3-R reversal log) is kept separately
outside the repo. Only decisions that are still *load-bearing* — the ones
that stop live behaviour being broken — are kept here, with their guard
reasoning intact. Where a reason survives, it is because forgetting it
would let someone re-break the thing.

---

## Agreed stage order

Built one stage at a time, in this order. Do not implement ahead of the
current stage. `Stage N` references elsewhere point at this list.

0. Scaffolding ✓
1. Backend architecture → `docs/ARCHITECTURE.md`, no code ✓
2. Domain models + migrations ✓
3. Auth, roles, tenant isolation, guest identity ✓ (revised by 3-R ✓)
4. Catalog (categories, services) ✓
5. Specialists — full CRUD ✓
6. Availability engine — pure slot computation, read-only ✓
7. Booking core — creation, statuses, cancellation, concurrency ✓
8. Payments — provider abstraction, deposit, webhooks, refunds ✓
9. Celery + notifications (email + channel abstraction) ✓
10. Telegram adapter
11. Reviews — read + submission (completed-appointment gating)
11.5. Content localization — per-salon language, translatable catalog /
    salon profile / notification templates. Numbered 11.5 so it doesn't
    renumber every existing `Stage N` reference.
12. Frontend skeleton — design system, API client, auth
13. Frontend catalog / service / specialists / reviews
14. Frontend booking flow + payment + confirmation
15. Frontend customer account + guest token management
16. AI assistant backend
17. AI assistant frontend widget
18. Admin shell + dashboard + appointments list
19. Admin calendar + appointment create/edit
20. Admin services, specialists, working hours, days off
21. Admin clients, reviews, notifications, statistics
22. Productionization — Docker, CI/CD, README
23. Production-readiness audit (fresh session)

## Open questions

Not yet decided — recorded so they surface before the stages that depend
on them.

- **Business model: one-off site sale vs. recurring SaaS subscription.**
  Affects salon onboarding / plans / billing stages.
- **Tenant resolution: path prefix only, or also custom domain per
  salon.** Path-prefix only today (Stage 3 middleware). Domain-based
  would be additive to the middleware, not a rewrite. Decide before the
  public frontend stage.
- **Salon-level closure/holiday model.** None today — only per-specialist
  `TimeOff`. Needed before the admin calendar stage (19/20).
- **Overlap-prevention validation on `WorkingHours`.** Nothing stops two
  overlapping rows for the same specialist/day (no constraint, no
  `clean()`, admin-writable). The availability engine defends itself by
  merging overlapping rows at ingestion; the write side has no guard. Add
  validation when `WorkingHours` gets a real write API (Stage 20).
- **Buffer-at-shift-end rule revisit.** Stage 6 requires the buffer to
  fit inside the specialist's own window, because no salon-level closing
  time exists. Once salon working hours (or the closure model) exist, the
  rule should become "buffer must fit before the earlier of specialist
  shift end and salon close." Revisit with the closure model.
- **`Salon.timezone` write-time validation.** Nothing validates that it's
  a real IANA zone at write time. A bad value surfaces only as an
  unhandled `ZoneInfoNotFoundError` from `compute_open_windows`. Decide
  where validation belongs (model/serializer/admin).
- **Lower-bound validation on `Salon.slot_granularity_minutes` and
  `Service.duration_minutes` at the model level.** `duration_minutes >= 1`
  is enforced only in `catalog/serializers.py`; `slot_granularity_minutes`
  has no lower-bound guard anywhere (no `Salon` serializer, no admin
  validator). `scheduling._step_windows` guards `<= 0` at read time
  precisely because the write side doesn't. Real fix: close it at the
  source. Needed by Stage 19/20.
- **`compute_candidate_start_times`'s `salon` arg is not checked against
  `specialist.salon`.** A mismatched `salon` silently resolves granularity
  / lead time / max-advance / timezone from the wrong tenant — wrong
  output, not a crash, and current tests wouldn't catch it. No guard yet
  (deliberate, per 6.E). A guard (`assert specialist.salon_id == salon.id`)
  costs nothing — `salon_id` is a plain column, not a lazy FK. Open
  question is likelihood, not cost.
- **Blocker provenance through the availability engine.**
  `compute_open_windows` flattens every blocking source (`TimeOff`,
  `Appointment`, future closures) into indistinguishable `Window`s before
  subtraction. A consumer that needs to explain *why* a slot is
  unavailable (Stage 19 calendar, AI assistant) loses that by design —
  read the underlying rows directly instead.
- **Local-time presentation across a DST transition.** On spring-forward
  the candidate list jumps (…01:40, 02:00, 03:00, 03:20…) because 02:xx
  doesn't exist locally. Show as-is / label / suppress — a presentation
  decision for the local-time formatting substage.
- **Max-advance boundary at local midnight can land on a non-existent
  local time.** In zones whose DST transition is exactly at midnight
  (`America/Santiago`, `America/Havana`), `zoneinfo` shifts rather than
  raises, so the boundary silently moves an hour that day. Not defended
  (default `Europe/Kyiv` transitions at 03:00), but any salon could be
  configured with such a zone. Distinct from the presentation question
  above — this is where the boundary *moves to*.
- **Per-specialist service duration vs. the "any specialist" union rule.**
  Today `occupied_minutes` is identical for every specialist in the union
  (duration/buffer are single `Service` fields, no per-specialist
  override). If overrides are ever added, "at least one specialist free"
  must mean free for *that specialist's own* duration — revisit the union
  rule then.
- **Specialist experience/seniority as a structured field.** Not modelled
  (free-text `bio` only). Wanted ~Stage 13. Key sub-decision: store a raw
  number ("8 years", goes stale) vs. a career-start date (recomputes,
  never lies) — lean toward the date.
- **"Resume payment"** — if the client loses frontend state, the one-shot
  `provider_data` is gone. Re-issuing needs a new provider method to fetch
  instructions for an existing intent. Deferred to Stage 14+ (shape
  depends on how the frontend holds state).
- **Production email send timeout (`EMAIL_TIMEOUT`).**
  `send_notification` calls `channel.send()` *inside* the `Notification`
  row lock (deliberate — serializes duplicate delivery). A hung SMTP
  server would hold the lock indefinitely. Prod must set a finite
  `EMAIL_TIMEOUT` so a stuck send fails fast and releases the lock.
  Settings/ops task for Stage 22 — the in-app design is already correct
  given a bounded timeout.
- **Notification retry loop not integration-tested end to end.** The unit
  test pins only our reaction to `MaxRetriesExceededError` (→ `FAILED`).
  The loop itself (initial attempt + N retries firing) can't be exercised
  under `CELERY_TASK_ALWAYS_EAGER` without depending on Celery internals.
  Real countdown/redelivery needs a running broker — Stage 22 integration
  suite.

## Overall style

- **Modular monolith**, not microservices. One Django project,
  domain-bounded apps (`catalog`, `scheduling`, `booking`, `payments`…).
  Revisit only on a concrete scaling or team-boundary problem.

## Multi-tenancy

- **Shared database, shared schema, row-level isolation.** Every
  tenant-owned model carries a `salon` FK. Scoping is enforced through a
  shared base manager/queryset, not per-view. Chosen over schema-per-tenant
  (migration/ops overhead unjustified; shared-schema is easier to write
  isolation tests against).
- **Tenant resolution: path prefix**, `/api/v1/salons/<slug>/...`. Chosen
  over subdomain (works locally without DNS). Resolution logic lives in a
  single isolated middleware so it can be swapped for subdomain-based
  later without touching every view. Middleware is placed **last** in
  `MIDDLEWARE` so the `tenant_context` binding window wraps only the view.
- **Unknown/inactive salon slug → 404, not 403.** The slug is a routing
  element, not a credential. Both cases resolve through the same query
  (`filter(slug=slug, is_active=True)`) so nothing can distinguish them.
- The `TenantScopedManager` **raises** without bound tenant context —
  never silently returns all rows. Queryset is bound in `__init__`, not at
  class-body level (avoids import-time `TenantContextMissingError`).

## Identity: Customer vs. Account

(Final shape after the Stage 3-R revision, which is fully landed.)

- **Two models. `Customer` = who visited. `Account` = who has a login.**
  - `Customer`: per-salon record (`salon`, `name`, `email`, `phone`),
    `unique(salon, email)`. A guest is a bare `Customer` with no account.
  - `Account`: per-salon login credentials (password, verification state,
    `role`), its own `unique(salon, email)`. Created only on registration;
    a guest never gets one. Links to the person's `Customer` at that salon.
- **`Appointment` always references `Customer`, never `Account`.** One path
  through booking code for guests and registered customers alike.
- **One site = one salon, full isolation.** Every login (client, admin) is
  scoped to exactly one salon. The same person at two salons holds two
  independent `Account`s that cannot observe each other. No endpoint may
  reveal existence across salons.
- **Roles on `Account`: `client` and `admin`** (single staff level for
  v1; `role` field keeps its value-space open for more later, no shape
  migration needed). New accounts are `client` unconditionally; elevation
  to `admin` can only be done by an existing `admin` of the same salon —
  a hard authorization boundary, not just a default. "Which roles the
  account holds" (security) and "which mode the session acts in"
  (presentation) are two separate checks.
- **Dual role within one salon = one `Account` with both roles**, not two
  logins. Doesn't require relaxing either `unique(salon, email)`.
- **`accounts.User` still exists** as `AUTH_USER_MODEL`, serving only
  Django `/admin/` for platform operators (`is_staff`/`is_superuser`). It
  is no longer a product-login identity. `User.username` is dropped;
  `email` is `USERNAME_FIELD` (custom `UserManager`).
- **Guest→`Account` link happens only after email verification**, and only
  within the same salon. Never link by phone (reassigned, unverified,
  shared).
- **Guests manage their appointment through a signed token link** (see
  Stage 9 § guest-token delivery), not an account login. **Guests cannot
  review** — review submission requires an authenticated `Account`-linked
  `Customer`.
- **`AccountJWTAuthentication.get_user` checks the `identity_model:
  "account"` claim *before* the pk lookup.** `User` and `Account` have
  separate auto-increment sequences both starting at 1; the claim is the
  gatekeeper against a cross-model id collision.

## Timezone

- **Single timezone per salon.** Store all timestamps in UTC
  (`USE_TZ=True`, `TIME_ZONE="UTC"`). Local-time rendering is
  application-layer, driven by a per-`Salon` timezone field — not a global
  Django setting. DST handling belongs to the window-building step (via
  `zoneinfo`); convert to timezone **before** `.date()`. Interval bounds
  are `[)` everywhere, matching the DB's `TSTZRANGE`.

## Currency

- **ISO 4217 3-letter code on `Salon`** (default UAH per product owner),
  **frozen onto `Payment` at creation.** Affects minor-unit rounding for
  the 20% deposit and display formatting.

## Payments

- **Mock `PaymentProvider` first**, provider-agnostic interface; real
  adapter added later behind it. **Tests never require network access** —
  they exercise the mock. Stripe is unavailable for Ukraine/Georgia — a
  local acquirer (LiqPay/Fondy/WayForPay) will be used.

## Notifications

- **Channel abstraction from day one**, only an email adapter built in
  Stage 9. Telegram is a second adapter in its own stage (10) behind the
  same interface.

## Frontend cadence

- **Backend-first.** No frontend work until booking + payments domains are
  complete on the backend.

## Dependency versions (Stage 0)

Pins in `backend/requirements/*.txt` are verified against PyPI directly
and checked for mutual compatibility via `requires_dist`, not just "does
it install." Three deliberate non-newest pins:

- **`redis` capped at `6.4.0`** — `kombu` declares `<6.5`; redis-py 7/8
  aren't supported by Celery's transport. `base.txt` uses `celery[redis]`
  so pip enforces this automatically on a future bump.
- **`mypy` capped at `1.19.1`** — `django-stubs`'s `compatible-mypy`
  declares `mypy<1.20`. `development.txt` uses
  `django-stubs[compatible-mypy]` so pip enforces it.
- **`django-stubs` pinned to `5.2.x`** — `6.0.x` targets Django 6.0 with
  only partial 5.2 support.

**Django pinned to `5.2.17`** — latest patch of the current LTS line, not
`6.1` (latest overall, not LTS).

When bumping any pin, re-check `requires_dist` for the above — a clean
install doesn't guarantee the runtime integration (Celery↔Redis) was
tested against that combination.

## Business rules

Product decisions (numbers), source of truth. `docs/ARCHITECTURE.md`
describes the mechanisms; the numbers live here, stated once.

- **Deposit: 20% of service price, paid online at booking.** Single
  salon-wide setting (`Salon.deposit_percentage`, default 20%), not
  per-service in v1.
- **Remaining 80% paid in person.** Not tracked/collected by the platform.
- **Cancellation refund depends on WHO cancelled, not only WHEN.**
  Customer-initiated: ≥24h before start → deposit refunded; <24h →
  forfeited (single cutoff, no tiers). **Salon-initiated** (specialist
  illness, `TimeOff` over a booked slot, working-hours change): deposit
  **always fully refunded regardless of timing.** Computed from
  `Appointment.cancelled_by`, not a bare time comparison.
- **Staff changes to `TimeOff`/`WorkingHours` conflicting with an existing
  appointment must be detected and explicitly resolved — never silently
  orphaned.** Customer is offered: rebook with another specialist, rebook
  later with the same one, or cancel with full refund. Resolution flow is
  Stage 19; the data model (Stage 2) and cancellation path (Stage 7/8)
  must support a salon-initiated, always-refunded cancellation.
- **Reviews require a `COMPLETED` appointment; exactly one per
  appointment.** Guests cannot review. Immutable once posted; no public
  salon reply in v1; staff can hide (not delete).
- **Appointment completion is an automatic scheduled transition**
  (`CONFIRMED → COMPLETED` once `end_datetime` passes), staff override in
  admin. Automatic because review eligibility depends on `COMPLETED`.
  (Note: this transition is **not yet built** — see Stage 9 § Step (e),
  which is why `REVIEW_REQUEST` has no trigger yet.)
- **`NO_SHOW` is a staff-marked status with no automatic effects in v1** —
  deposit already forfeited by then. Record-keeping/statistics only.
- **Booking window: min 3h lead time, max 60 days ahead.**
  Salon-configurable; these are defaults (`min_lead_time_hours`,
  `max_advance_days`).
- **A `PENDING_PAYMENT` appointment holds its slot 15 min** before the
  expiry sweep releases it. Fixed platform constant, not
  salon-configurable — long enough for a card payment incl. 3-D-Secure,
  short enough that an abandoned checkout only blocks a slot briefly.
- **Service buffer blocks the calendar but is never offered as a bookable
  start.** A 90-min service + 15-min buffer occupies 105 min; the next
  appointment may start exactly when the buffer ends. Buffer is per
  service, not salon-wide.
- **No specialist logins in this build.** Specialists are managed by salon
  staff/admin.
- **The AI assistant only proposes a service + slot; it never creates a
  binding appointment.** The user must confirm through the normal booking
  flow — a hallucinated parameter must never produce a real paid booking.
- **AI assistant memory is session-only for everyone (incl. logged-in),
  Redis with a TTL, never persisted to a DB.** Salon chat surfaces
  health-adjacent personal info; the deliberate choice is not to retain it
  by default for anyone.
- **`Salon.timezone` defaults to `Europe/Kyiv`; `slot_granularity_minutes`
  defaults to 15.** Both per-salon, overridable.

## Formatting/linting tooling

- **Consolidated on `ruff format`, dropped `black`** (redundant — ruff
  format is ~99.9% black-compatible). Revisit only if byte-for-byte black
  output is ever externally required.
- **`# noqa: DJ012` on `core/models.py` is intentional** — ruff's DJ012
  would reorder managers and silently make `unscoped_objects` the default
  manager, defeating tenant isolation. Do not remove.

---

## Stage 3 (auth, roles, tenant isolation, guest identity)

- **Guest token: signed value, only its SHA-256 hash stored**
  (`GuestAccessToken.token_hash`, never the raw token — a DB dump must not
  hand out working links). Validation re-hashes and looks up by hash.
- **`cancelled_via_token_at`, not a blanket `consumed_at`.** Viewing and
  cancelling are separate capabilities of the same token; one "consumed"
  flag would kill view access the moment the guest cancels.
- **Guest token expiry: 30 days after `Appointment.end_datetime`** (bounds
  blast radius of a compromised/forwarded inbox while covering the
  realistic view/cancel window).
- **Credential-token transport: URL fragment** (`#token=...`), never a
  query string — a fragment reaches no access log. Frontend JS reads
  `window.location.hash` and sends it as an `X-Guest-Token` header. (The
  *guest manage* token later diverges to a path URL — see Stage 9 § Step
  (d). Verify/reset keep the fragment.)
- **Throttle rates** (DRF `ScopedRateThrottle`, keyed by IP): `login`
  5/min; `password_reset` and `resend_verification` 3/hour each (both send
  email to a third party — abuse spams someone else's inbox);
  `guest_token` 20/min (token is a signed value, not brute-forceable —
  guards endpoint hammering, not guessing).
- **Registration and password-reset-request never reveal whether an email
  exists.** Identical response either way, and a Celery task is enqueued
  either way so timing doesn't diverge. Duplicate registration → an "you
  already have an account" email instead of a second row.
- **DRF throttling needs a shared cache.** `LocMemCache` is per-process, so
  behind N workers the limit silently becomes `rate × N`. Uses
  `RedisCache` on Redis **DB 2** (broker DB 0, result backend DB 1) so
  throttle keys never collide with Celery's.
- **Email verification token: stateless, not single-use, payload includes
  the target email.** `TimestampSigner`-signed `{user_id, email}`, 48h
  expiry. Verifying is idempotent (unlike guest *cancel*), which is why it
  needs no stored single-use record. Checking the signed email against the
  current one gives a future "change email" feature free invalidation.
- **Password reset uses Django's `PasswordResetTokenGenerator`** (already
  self-invalidating on password change — the single-use property this
  token needs). `PASSWORD_RESET_TIMEOUT` = 1h, tighter than verification's
  48h (highest-value credential, acted on within minutes).
- **`GuestAccessToken` lives in `booking`, not `accounts`**, as a full
  `TenantScopedModel`. `booking` already depends on `accounts`, never the
  reverse. A token issued for one salon, presented against another's URL,
  is simply invisible to the tenant-scoped hash lookup — cross-salon
  misuse collapses into the same "not found" path.
- **Every guest-token failure raises the same `InvalidOrExpiredTokenError`
  (400)** — bad signature, unknown hash, expired, cancel-already-spent all
  produce an identical response, so nothing leaks which case fired.
- **`HasValidGuestToken` validates in `has_permission`, not
  `has_object_permission`, and every view using it must be a DRF generic.**
  DRF only calls `check_object_permissions()` when a view triggers it;
  generics do so in `get_object()`, a plain `APIView` doesn't. Defence:
  (1) `has_permission` requires `guest_token_action` to be exactly
  `"view"`/`"cancel"`, no default (a forgetful view is denied); (2) both
  guest views are generics; (3) `get_object()` resolves the appointment
  from the token's `appointment_id`, not the URL id (URL id is compared
  and must match, purely to fail loud); (4) a test walks every URL using
  the permission and asserts the view is a generic. The combination is the
  mitigation — no single layer is airtight. General rule is in `CLAUDE.md`.
- **The guest→registered merge on verification is a per-salon loop**
  (`for salon_id in Salon.objects.values_list(...): with
  tenant_context(salon_id): ...`), not `unscoped_objects` — keeps the one
  deliberately cross-tenant op narrow and explicit.

### Django admin (Stage 3 sub-step 4)

- **`core.admin.SalonScopedAdmin` is a blanket cross-tenant tool for
  platform operators**, not a per-salon back office (that's Stages 18–21,
  on the JWT + `IsSalonStaff` API). Reads through `unscoped_objects` (admin
  requests never bind tenant context). `formfield_for_foreignkey` binds a
  throwaway sentinel (`tenant_context(-1)`) around Django's eager
  `_default_manager` evaluation, because `ForeignKey.formfield()` evaluates
  it *before* applying a `queryset` override — otherwise the add/change
  form for a model with an FK to another `TenantScopedModel` 500s.
- **`GuestAccessToken` registered read-only, `token_hash` excluded** from
  the change form (Django renders every field by default regardless of
  `list_display`; `has_change_permission=False` disables editing, not
  visibility). Regression-tested by loading the actual page.
- **Read-only models:** `Appointment`, `Payment`, `Notification`,
  `ProcessedWebhookEvent`, `GuestAccessToken`, `Review` — each has a
  service-layer state machine or an immutability rule that a hand-edit
  would violate. Editable: reference/config data with no state machine
  (`Customer`, `ServiceCategory`, `Service`, `Specialist`,
  `SpecialistService`, `WorkingHours`, `TimeOff`, `Salon`).
- **Cross-tenant FK mismatch via admin is closed at the DB layer** (the
  composite tenant FK, `core/db.py`'s `composite_tenant_fk`) — a mismatch
  fails as an `IntegrityError`. Ugly 500 for an internal tool, not a
  correctness gap; no admin-form `clean()` added.

## Stage 4 (catalog API)

- Standard CRUD over Stage 2 `ServiceCategory`/`Service`, id-keyed (no
  slugs), under `/api/v1/salons/<slug>/{categories,services}/`, paginated
  (page 20 / cap 100).
- **Read/write split:** GET is public (`AllowAny`) — any visitor browses a
  salon's catalog. Writes require `IsSalonStaff` for the resolved tenant.
- **Soft-delete:** `DELETE` flips `is_active=False` (204), never removes.
  A category refuses to deactivate while it has active services
  (`CategoryHasActiveServicesError`, 409). Deactivating a `Service` with
  appointments is always allowed (`Appointment` snapshots price/deposit,
  so it can't retroactively change what's owed) — it only stops new
  bookings.
- **`include_inactive=true`** is a staff-only *read* privilege, scoped to
  the staff member's own salon (against another salon it silently returns
  the public active-only result). It has no effect on writes — the write
  path skips the visibility filter entirely, since `get_permissions()` has
  already restricted it to same-salon staff.
- **Catalog slugs dropped from Stage 4** — a future frontend concern; both
  tables are empty, adding later is no more expensive.
- **DRF 3.18 silently skips a `UniqueTogetherValidator` from
  `Meta.constraints` when a constrained field is read-only with no
  default.** Every `TenantScopedModel`'s `salon` is read-only + no default,
  so `(salon, X)` validators are dropped with no error — a duplicate name
  passes `is_valid()` and 500s at `.save()`. Fixed two ways: an explicit
  `validate_name` on the serializer (friendly case) **plus** a
  `core.exceptions.exception_handler` branch translating
  `psycopg.errors.UniqueViolation` into a structured 400 (backstops the
  concurrent-POST race). Recurs on every future `(salon, X)` constraint —
  standing instruction in `CLAUDE.md`.

## Content localization (deferred to Stage 11.5)

- **Deferred to its own stage** (11.5), after the backend model/API stages.
  Recorded now so the catalog model isn't bolted onto later. Guest language
  is not stored anywhere yet; all emails are English for now.

## Stage 5 (specialists API)

- **`Specialist.is_active` means employment status only** (`True` =
  currently employed), **not** availability/visibility. Needed by the CRUD
  API's soft-delete (never hard-delete a row an `Appointment` might
  reference).
- **Temporary absence is `TimeOff` rows, not a status on `Specialist`.** No
  duration threshold needed anywhere — the 60-day booking window means an
  absence longer than that simply shows no bookable slots.
- **Stage 6 must hard-exclude `is_active=False` specialists from slot
  computation.** A terminated employee has no `TimeOff`, so an engine that
  only subtracts `WorkingHours − TimeOff − appointments` would keep
  offering their slots. `is_active` is *not* an availability flag, but the
  engine must still consult it — as a hard exclusion, not part of
  open-windows computation.
- **No `unique(salon, name)` on `Specialist`** (two people sharing a name
  is legitimate), so no `validate_name` — the DRF 3.18 trap doesn't arise
  where there's no constraint to skip.
- **Deactivating a specialist with future non-cancelled appointments is
  refused (409)**, not cleaned up — Stage 5 has no payment/notification/
  resolution flow to honor the "detect and explicitly resolve" rule.
  Revisit at Stage 19. "Still expected" reuses `booking`'s
  `ACTIVE_APPOINTMENT_STATUSES` directly (same question as "does this hold
  a slot"). "Future" means `end_datetime > now()` (an in-progress
  appointment still blocks; buffer time does not — that's room turnaround,
  not the specialist's obligation). The 409 returns
  `{future_appointment_count, future_appointment_ids}` — ids are keys the
  caller can already resolve, no customer-facing data.
- **`core/exceptions.py` catches only `UniqueViolation`** — a
  `ForeignKeyViolation` from a composite tenant FK surfaces as a 500, not a
  400 (verified empirically). The clean field-specific 400 always comes
  from the serializer's tenant-scoped queryset check, never from the DB
  constraint underneath. Applies to every `many=True` tenant-scoped
  relation. Only catch `UniqueViolation` / `ExclusionViolation` (by
  `isinstance(__cause__)`); other `IntegrityError`s must propagate as loud
  500s.

## Stage 6 (availability engine)

Pure slot computation, read-only, heavily unit-tested. Layers live in
`scheduling/services.py`. The `imperative shell / pure core` split: only
`_fetch_*` functions know about the ORM and need tenant context.

- **`_step_windows(windows, granularity_minutes, occupied_minutes) ->
  list[datetime]`** is pure. **`compute_candidate_start_times(specialist,
  service, salon, date_from, date_to)`** calls `compute_open_windows`
  itself (rather than receiving pre-computed windows — avoids callers
  keeping two call sites in sync), resolves `occupied = duration + buffer`
  and `granularity = salon.slot_granularity_minutes`, then steps.
- **`salon` is an explicit parameter, not derived from `specialist.salon`**
  — deriving would fire a fourth lazy-FK query on top of the three
  `compute_open_windows` issues (pinned by `django_assert_num_queries(3)`),
  and silently, inside a pure-resolution function.
- **Strict grid only, no off-grid last slot.** Each window is walked from
  `window.start` in `granularity` steps, keeping a candidate while
  `candidate + occupied <= window.end`; a sub-step tail is never offered.
- **`granularity <= 0` / `occupied <= 0` raise**
  `InvalidSteppingParametersError` (`DomainError` subclass, 500 — server
  misconfiguration, not caller error, unlike `InvalidDateRangeError`'s
  400). The guard exists because a `granularity = 0` row is creatable via
  `/admin/` today and the naive loop would hang the request indefinitely —
  worse than any other failure mode.
- **`occupied` larger than every open window returns an empty list**, not
  an exception — "nothing available" isn't an error.
- **Multi-specialist availability: "at least one specialist free."** Query
  count is `~1 + 3*N` (accepted N+1); tests assert it as a formula of N.
- **Buffer at shift end must fit inside the specialist's own window** (no
  salon closing time exists yet — see Open questions).
- **DST belongs to the window-building step** (`zoneinfo`); convert to
  timezone before `.date()`.

## Stage 7 (booking core)

- **Snapshot fields frozen at creation, never mutated:**
  `service_price_at_booking`, `deposit_percentage_at_booking`, `currency`.
  A later catalog/salon change can't retroactively alter an existing
  booking.
- **`now` is always an explicit parameter with no default** (the system
  clock is I/O — called once, in the view, passed inward). Tests use a
  fixed `now` literal wherever lead/max-advance bounds matter, never
  `timezone.now()`.
- **View vs service rule: extract to `services.py` when a *second* caller
  appears, not speculatively.** A single HTTP-bound caller keeps logic in
  the view.
- **Guest booking uses one `Customer` table with a nullable `Account`
  link.** Guest→`Account` link only after email verification, same salon.
- **Cancellation only handles the transitions the state machine
  documents** (`PENDING_PAYMENT`/`CONFIRMED → CANCELLED`); any other start
  status → `InvalidStateTransitionError` (409).
- **Appointment-expiry sweep (7.F):** releases a `PENDING_PAYMENT` slot
  after the 15-min hold. Model: scan existing `hold_expires_at`, per-row
  `atomic` + `select_for_update` + status recheck. (This is the pattern
  the Stage 8.G and Stage 9(e) sweeps mirror.)
- **Race/lock tests** use `@pytest.mark.django_db(transaction=True)` with
  `monkeypatch` injecting a competing update between the unlocked query and
  the lock. `monkeypatch` targets where the name is **looked up**, not
  where defined — recorded in the test docstring.

## Stage 8 (payments)

- **Refund risk asymmetry drives the ordering.** Write `REFUND_PENDING`
  **before** calling the provider (a double refund is irreversible). But
  payment *initiation* calls the provider **outside** the transaction —
  don't hold a row lock during network latency.
- **Stuck refunds are flagged, not auto-retried** (asymmetric risk).
  `refund_initiated_at` is its own field, **not** `updated_at` — `auto_now`
  moves on every save, which would be a lying clock.
- **Webhook orchestration lives in the view** (single HTTP-bound caller);
  domain actions delegate to existing services.
- **`PAYMENT_SUCCEEDED` is suppressed on the EXPIRED-appointment branch**
  of the webhook — a payment that lands after the hold expired must not
  confirm a released slot. `BOOKING_CONFIRMED` is co-gated with it.
- **Stuck-refund flagging sweep (8.G):** `now` once before the loop,
  `Salon.objects.all()`, per-salon `tenant_context` + `try/except` with
  `logger.exception`. Runs at `1800.0` (coarser than the 60s expiry sweep —
  a flag doesn't need fine granularity).

## Stage 9 (Celery + notifications)

Email + channel abstraction. **Fully landed and pushed.** Historical dated
sub-step process notes are in the external full copy; what follows is the
load-bearing shape.

### Schema

- **`Notification.user` FK removed** (vestigial pre-3-R recipient slot with
  no caller). **`notification_exactly_one_recipient` CheckConstraint
  dropped** — with `user` gone the predicate is a tautology; the real guard
  ("`customer`, when populated, belongs to `salon`") is the composite
  tenant FK. Recipient model now: `customer` populated → email to that
  client; `customer` null → alert to `Salon.contact_email`.
- **`Salon.contact_email` added — `NOT NULL`, no model default.** An alert
  with nowhere to go is the failure this prevents; a default would let that
  state be created silently. **Non-emptiness is enforced by a
  `CheckConstraint`** (`Q(contact_email__gt="")`), **not** a field
  validator — a validator is bypassed by `.create()`/`bulk_create()`/raw
  SQL, the `CheckConstraint` holds on every path. (Same category as: a
  `CharField NOT NULL` without a default silently persists `""` via
  `.create()` because it skips `full_clean()` — always supply the value
  explicitly in fixtures/create calls.)

### Send mechanism + dedup

- **`send_notification` calls `_build_message` under `select_for_update`
  inside `atomic`** — deliberate, the *opposite* of the 8.C/8.F refund
  ordering. Here the lock must span the send so a duplicate can't produce
  two emails; a duplicate email is trivial, an irreversible duplicate
  refund is not.
- **`channel.send` raising is not caught** — it propagates, the row stays
  `PENDING`, retry policy handles it.
- **No-enumeration at two levels:** the tenant-scoped manager hides other
  salons; an identical empty-body 202 hides existence within this salon.

### Credential emails

- **Verification/reset emails route through the `Notification` journal**
  (Stage 3-R put product auth under the salon prefix, so every credential
  email now has a salon in scope). `dedup_key` is **per-request** (derived
  from the per-request token — a hash/digest, never the raw token), never
  `account:{id}` — a deliberate "resend" is a new event, not a technical
  duplicate.

### Step (d) — guest-token delivery

- **The `"token"` key is removed from the 201 booking response.** Contract
  is now `{"appointment": {id, status, start_datetime, end_datetime}}`. The
  outer envelope is kept (room for Stage 8 snapshot fields later).
- **The raw token is delivered in the `BOOKING_CONFIRMED` email as a
  manage link, in the URL PATH:**
  `{FRONTEND_URL}/salons/<slug>/appointments/<id>/manage/<token>/`. This
  token diverges from the fragment transport of verify/reset because it is
  a **persistent resource link, not a magic login** — the guest opens it
  repeatedly to view/cancel one booking over the 30-day window. Query
  string is still ruled out (lands in access logs); fragment needs a
  client-landing page that doesn't exist yet. Accepted conscious risk:
  blast radius is a single booking, no account, no funds.
- **`BOOKING_CONFIRMED` fires from exactly one production call site** —
  `payments/views.py` `PaymentWebhookView.post`, on the succeeded →
  `PENDING → CONFIRMED` branch, inside the `select_for_update` lock. So
  every time it fires the appointment is guaranteed `CONFIRMED` and the
  token is meaningful.
- **`derive_guest_token(appointment_id) -> str`** (`booking/guest_tokens.py`)
  re-derives the raw token at send time (the webhook only ever has the
  stored SHA-256 hash). A shared `_sign_token()` is used by both
  `issue_guest_token` and `derive` → determinism by construction; `derive`
  does not write to the DB. `payments` calls it rather than reconstructing
  the signed value (salt/payload stay private to `booking.guest_tokens`).
- **CONSTRAINT: the guest-token signature must stay deterministic.** It
  signs a plain `{"appointment_id": id}` with `signing.Signer` — no
  timestamp, no nonce. **Never add a timestamp or any non-deterministic
  element** — re-derivation would produce a different token, silently
  invalidating every stored hash and breaking guest verification for every
  booking made before the change.
- **`core/formatting.py` → `format_datetime_for_salon(when,
  salon_timezone) -> str`** = e.g. `"Sat, 26 Sep 2026, 14:00"` (start time
  only, 24-hour, no timezone label). Locale-independent via fixed English
  tables — **never `%a`/`%b`** (locale-dependent on the OS → a non-English
  server would render the date in another language mid-English-email).
  Deliberately placed in `core` because it has a second caller (reminders).
- **`_build_message` builds `BOOKING_CONFIRMED` dynamically** from the
  appointment FK (salon name, formatted local time, manage link), not from
  a static `_MESSAGES` entry (that entry was removed). A notification with
  `appointment=None` → **`ValueError`** (loud failure: a background task
  has no client present, so a missing appointment is a bug, not a valid
  blank send — traceback to logs, no broken email sent).
  Body: `"Your booking at {salon.name} on {when} is confirmed.\n\nView or
  cancel your booking: {link}"` (the `\n\n` blank line is required).
  Subject: `"Your booking is confirmed"`.

### Step (e) — appointment-reminder sweep

- **One day-before `APPOINTMENT_REMINDER` per appointment** (the original
  "24h/2h" placeholder is narrowed to a single ~24h reminder). Model A:
  scan the existing `start_datetime`, no new send-state field on
  `Appointment` (mirrors the 7.F expiry and 8.G refund sweeps).
- **Window: `start_datetime` in `(now+24h, now+25h]`** — lower bound
  **strict** (`>`), upper bound **inclusive** (`<=`). Inverting the window
  upward means short-notice bookings never get a reminder by construction.
  **Only `CONFIRMED`** (a bare filter, not `ACTIVE_APPOINTMENT_STATUSES` —
  that set is load-bearing for the double-booking constraint and must not
  be widened here).
- **Beat cadence: hourly (`3600.0`).** Window width (1h) = beat period, so
  each qualifying appointment is normally seen by exactly one run.
  **Residual edge fragility accepted:** beat drift could let an appointment
  cross the whole band inside a gap and be missed — a soft failure (one
  missed reminder, no money, no state change).
- **Two-level dedup.** `dedup_key = f"appointment_reminder:appointment:
  {pk}"` (no trailing segment → one reminder per appointment ever).
  (1) An explicit `Notification.objects.filter(...).exists()` check on the
  key, **under the per-row `select_for_update` lock**, keeps the returned
  count honest across sequential beat runs (`record_and_dispatch` returns
  `None` whether it inserted or swallowed a `UniqueViolation`, so the sweep
  couldn't otherwise tell a fresh reminder from a recorded one).
  (2) The `notification_trigger_channel_dedup_uniq` constraint remains the
  real guard against a *concurrent* run double-sending — `.exists()` isn't
  race-proof alone. `record_and_dispatch_notification` was **not** changed
  (the `.exists()` check lives in the service instead).
- **Service `send_due_appointment_reminders(*, salon, now) -> int`**
  (`notifications/services.py`) mirrors 8.G: unlocked candidate-id query,
  then per row `atomic` + `select_for_update` + recheck `CONFIRMED` +
  dispatch. **Task `send_due_appointment_reminders_task`**
  (`notifications/tasks.py`, `_task` suffix per that file's own convention)
  mirrors 8.G line-for-line: `now` once before the loop,
  `Salon.objects.all()`, per-salon `tenant_context` + `try/except`. Beat
  entry `"send-due-appointment-reminders"` at `3600.0` in `config/celery.py`.
- **`APPOINTMENT_REMINDER` email is dynamic too** (moved out of static
  `_MESSAGES` into a `_build_message` branch, parallel to
  `BOOKING_CONFIRMED`, deliberately without a shared helper — the
  "extract shared appointment-email builder" refactor is deferred).
  `appointment=None` → `ValueError` before the FK dereference.
  Subject: `"Reminder: your appointment tomorrow"` ("tomorrow" is reliable
  precisely because of the narrow `(now+24h, now+25h]` window). Body:
  `"This is a reminder of your appointment at {salon.name} on {when}.\n\n
  View or cancel your booking: {link}"` ("View or cancel your booking:"
  wording is identical to `BOOKING_CONFIRMED` — same page, same purpose).
- **`REVIEW_REQUEST` trigger is deferred** — no `CONFIRMED → COMPLETED`
  transition exists in the codebase yet to fire it (the automatic
  completion transition in § Business rules is not built).