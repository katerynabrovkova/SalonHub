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
10. Second notification channel — DEFERRED (see § Notifications)
11. Reviews — read + submission (completed-appointment gating) ✓
11.5. Content localization — per-salon language, translatable catalog /
    salon profile / notification templates. Numbered 11.5 so it doesn't
    renumber every existing `Stage N` reference. ✓
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
- **Salon profile API.** No endpoint currently exposes `Salon.name` /
  `Salon.about` / other salon-identity fields over HTTP (the `tenants/` app
  has no `serializers.py`, `views.py`, or `urls.py` — see Stage 11.5 closure
  recon, 2026-09-10). Stage 11.5 made these fields translatable at the data
  layer only; no stage in the roadmap explicitly commits to building a read
  endpoint for them. Must be resolved when Stage 12/13 scope is written: does
  the frontend catalog/landing page need to render salon name/about, and if
  so, in which stage does the endpoint get built.
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
  Stage 9 § guest-token delivery), not an account login. **Both guests and
  Account-linked customers may leave reviews** — eligibility depends on a
  `COMPLETED` appointment, not on having an account (see Stage 11).
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
  Stage 9. A second adapter is deferred to its own future stage; the
  channel type is not fixed (both Telegram and WhatsApp were considered).
- **Why deferred (decided 07.09.2026):** a second channel is not just
  "another adapter behind the same interface." WhatsApp requires the
  Business Cloud API and pre-approved message templates — business-initiated
  messages cannot be sent as free text outside a customer-initiated window,
  which breaks the dumb-pipe `send(recipient, subject, body)` contract.
  Both options also expand the booking contract (channel choice + phone
  number). We do not build channel-selection logic while only one channel
  actually exists. Stage 9 infrastructure (the NotificationChannel ABC, the
  `_CHANNELS` registry, the channel enum) is left as the extension point;
  the second adapter slots in without rewriting the mechanism.

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
  appointment.** Immutable once posted; no public salon reply in v1.
- **Appointment completion is an automatic scheduled transition**
  (`CONFIRMED → COMPLETED` once `end_datetime` passes), staff override in
  admin. Automatic because review eligibility depends on `COMPLETED`.
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
  Recorded early so the catalog model wasn't bolted onto later.
- **Superseded by § Stage 11.5 (Content localization) below**, which is now
  the load-bearing contract. The early-placeholder wording here — "guest
  language is not stored anywhere yet; all emails are English for now" — no
  longer holds: guest language lives on `Customer.preferred_language` and
  emails render in `en` or `uk`. Kept only as the pointer to the full
  section.

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

  **Correction, recorded 09.09.2026:** this bullet was never implemented.
  accounts/tasks.py's send_verification_email / send_password_reset_email
  still call django.core.mail.send_mail directly, bypass the Notification
  journal entirely, and have no dedup_key. No commit after Stage 3-R
  touched the file. The design above remains the intended target but is
  unbuilt; reconciling it is unscheduled work, not covered by any
  currently-numbered stage.

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

## Stage 10 (second notification channel) — DEFERRED 07.09.2026

Deliberately skipped, not removed from the roadmap. Email remains the only
channel. Rationale is recorded in § Notifications above. Stage 9 left the
channel abstraction as the extension point, so the second adapter can be
added later without reworking the send path. Next work is Stage 11 (Reviews).

## Stage 11. Reviews

- **Review is anchored to the appointment (one-to-one), not to the
  customer-salon relationship.** The unique appointment anchor is the
  anti-spam barrier: N reviews require N real `COMPLETED` visits. No
  moderation layer is needed to keep volume honest.
- **Both guests and Account-linked customers may submit a review**, gated
  only on the appointment being `COMPLETED`. A guest who actually attended a
  visit has as legitimate a basis to review as an account holder; the
  account requirement from Stage 2 is dropped. At the model level both are a
  `Customer`, so the write gate — not the model — enforces this.
- **Public reviews are grouped by specialist**, because the salon is treated
  as a marketplace of specialists a client returns to, rather than a catalog
  of interchangeable services. Rejected alternative: grouping by service.
  Grouping by service would survive a specialist leaving (their review block
  would stay attached to the service); grouping by specialist means a
  departed specialist's review block goes dead. Accepted deliberately as a
  consequence of the marketplace-of-specialists model.
- **A denormalized `specialist` FK is stored on `Review`** to serve the
  grouped-by-specialist read path directly, without joining through the
  appointment on every query. It is a snapshot of fact at review time, not a
  cache: an appointment may later change its specialist, but the review must
  remain about whoever actually performed the visit. Same snapshot principle
  as `service_price` / `blocked_until`.
- **`text` is optional (empty string, not null) and length-capped
  (~2000 chars).** The cap is an input-size anti-spam measure living on the
  field itself — it is not moderation. `rating` is 1–5 (enforced by the
  existing check constraint).
- **No moderation.** The Stage 2 `hidden_at` hide mechanism is removed
  (RemoveField migration). A salon must not be able to hide unfavourable
  reviews; a review exists precisely to show the truth. No placeholder field
  is left behind — it can be added by migration if a real need appears.
  Reviews remain immutable after posting, with no public salon reply in v1
  and `PROTECT` on delete (Stage 2 decisions, unchanged).
- **`COMPLETED` is produced by a time-based sweep** mirroring the Stage 7.F
  expiry sweep and Stage 8.G stuck-refund sweep: an unlocked candidate query
  (`CONFIRMED` with `end_datetime` in the past), then per-row
  `atomic` + `select_for_update` + recheck `status == CONFIRMED` under the
  lock before saving `status` only. Compared against `end_datetime` (not
  `blocked_until`; precedent `booking/guest_tokens.py`). This is the
  `CONFIRMED → COMPLETED` transition that Stage 9 deferred, and the point the
  `REVIEW_REQUEST` trigger now attaches to.

### Part 2: Review CRUD API

**Write endpoint: `POST /api/v1/salons/<slug>/appointments/<appointment_id>/review/`**

- **Auth is a new dedicated permission class**, not `HasValidGuestToken`. It
  accepts either the guest token (`X-Guest-Token` header, the same
  appointment-scoped signed-token mechanism as the existing booking endpoints —
  `booking/guest_tokens.py`) or an authenticated `Account` (JWT). It is not
  `HasValidGuestToken` because that class is scoped to the retrieve-family
  actions (`view` / `cancel` / `pay`) on an already-existing object, whereas
  review-create splits its responsibilities differently: the **identity /
  ownership check** is the permission class's job, and the **eligibility check**
  (appointment `status == COMPLETED`) is the serializer's `validate()`, not the
  permission class. These two checks are kept deliberately separate.
- **Ownership failure → 404, not 403.** If the appointment does not belong to
  the caller — wrong guest token, or an `Account` that does not own it — the
  response is `404`, identical to a nonexistent appointment id. This is an
  identity/ownership boundary, not a business-rule boundary: a `403` here would
  let a caller enumerate which appointment ids belong to other customers.
- **Eligibility failure → 403.** If the appointment belongs to the caller but
  its `status != COMPLETED`, the serializer's `validate()` raises
  `ReviewRequiresCompletedAppointmentError` (`core/exceptions.py`, `403`, code
  `review_requires_completed_appointment`) with a message stating that a review
  requires a completed visit.
- **Duplicate → 409, from an explicit check.** The serializer's `validate()`
  checks for an existing `Review` on the appointment and raises
  `DuplicateReviewError` (`core/exceptions.py`, `409`, code `duplicate_review`)
  before any write is attempted — this is the primary path. The
  `Review.appointment` one-to-one DB constraint stays purely as a
  race-condition backstop for the narrow window between that check and the
  insert: if a concurrent request wins and the constraint fires instead, the
  `UniqueViolation` surfaces as `400 unique_violation` through the Stage 4
  `UniqueViolation → 400` translation in `core.exceptions.exception_handler`,
  not as `409`.
- **Success → 201 with the full serialized `Review`** (`id`, `rating`, `text`,
  `specialist`, `created_at`, and the rest of the row), not a bare confirmation
  message. Consistent with the other create endpoints, lets a frontend render
  the posted review without a refetch, and lets tests assert on response
  content directly.
- **New `DomainError` subclasses (`core/exceptions.py`)** carrying this
  endpoint's non-400 outcomes, each surfaced by `exception_handler` at its own
  `status_code`: `ReviewRequiresCompletedAppointmentError` (`403`, code
  `review_requires_completed_appointment`) and `DuplicateReviewError` (`409`,
  code `duplicate_review`).
- **`rating` / `text` bounds are enforced at the serializer field level**
  (`IntegerField(min_value=1, max_value=5)`, `CharField(max_length=2000)`) so an
  out-of-range value is a `400`, not a `500`: the model `CheckConstraint`s
  (`review_rating_between_1_and_5`, `review_text_max_length_2000`) surface as a
  `CheckViolation`, which `exception_handler` does not translate. The DB
  constraints remain the backstop.

**Read endpoint: `GET /api/v1/salons/<slug>/reviews/`**

- **Public, no auth.**
- **Response is grouped by specialist**: a list of `{specialist, reviews}`
  blocks, matching the Stage 11 decisions above (denormalized `specialist` FK
  on `Review`, grouped display by specialist rather than by service). A
  specialist with no reviews does not appear.
- **Group ordering** is by review count descending — the most-reviewed
  specialist first. Ties (equal review counts) break by lower `specialist` id
  first, so ordering is deterministic rather than incidental DB order.
- **Within a group**, reviews are ordered by `created_at` descending (newest
  first).
- **Per-review shape** is `id`, `rating`, `text`, `created_at` (the GREEN-phase
  serializer may add further non-identifying fields; these four are the
  guaranteed minimum).
- **No customer-identifying data is exposed.** This is a public, unauthenticated
  endpoint: the `customer` FK/id and the customer's `name` / `email` / `phone`
  never appear anywhere in the response.
- **No pagination in v1.** A deliberate scope decision, not an oversight — to
  be revisited if a salon accumulates a large volume of reviews per specialist.

### Stage 11 status: closed

Both parts are complete: (1) the `CONFIRMED → COMPLETED` appointment sweep,
(2) the review write endpoint and the public grouped read endpoint. Review
update and delete are out of scope — reviews are immutable after posting
(Stage 2 decision, reaffirmed in the Stage 11 reconciliation). Final gate
clean: full suite 568/568, `ruff check`, `ruff format --check`, `mypy`, and
`makemigrations --check` all pass.

## Stage 11.5 (Content localization)

Decided 08.09.2026 — the contract was agreed before any code, per the
stage-by-stage workflow. This section is the spec the implementation
sub-steps build to; it supersedes the early-placeholder § Content
localization note above.

### Languages and fallback

- **English (`en`) and Ukrainian (`uk`) only, for now.** The set is expected
  to grow; nothing in the storage or API shape may assume exactly these two.
- **English is the single global fallback language.** A missing or empty
  translation for the requested language falls back to `en`. There is **no
  per-salon default-language field** — the demo "universal" salon and every
  future tenant fall back the same way, through `en`. Rejected: a
  `Salon.default_language` column — it adds a second fallback axis (salon
  default vs. global default) with no concrete requirement for it yet, and
  every salon in scope authors `en` content anyway.

### Storage format: JSONField dict of language code → string

- **Each translatable text field becomes a `JSONField` holding a
  `{lang_code: string}` dict**, e.g. `{"en": "Haircut", "uk": "Стрижка"}`.
- Rejected: **one column per language** (`name_en`, `name_uk`, …) — every new
  language is then a schema migration across every translatable model.
- Rejected: **a separate per-model translation table** (the
  `django-modeltranslation` / `django-parler` shape) — more join complexity
  and more moving parts than the language list's volatility justifies here.
- The language list is not fixed yet; `JSONField` absorbs a new language with
  no migration. Trade-off accepted: no DB-level typing of the dict shape, so
  validation (known language keys, string values, per-language length) lives
  in the serializer / model `clean`, with DB `CheckConstraint`s only where
  one is naturally expressible (see `Salon.about`).

### Fields becoming translatable (`JSONField`)

- `catalog.ServiceCategory.name`
- `catalog.Service.name`
- `tenants.Salon.name`
- `tenants.Salon.about` — **new field.** Salon-profile free text; there is no
  description/bio field on `Salon` today. Blank/optional. Each per-language
  value is capped at 2000 characters by a DB-level `CheckConstraint`, the
  same mechanism as `reviews.Review.text`'s `review_text_max_length_2000`
  (`Length` lookup registered on the field; constraint expressed against the
  value's `__length__lte`). The cap must hold for every populated language
  key, not just `en`.
- `specialists.Specialist.bio`
- **Notification message templates** — the `(subject, body)` strings
  currently inline in `notifications._MESSAGES` and built in
  `notifications._build_message`. The module already anticipates this ("when
  Stage 11.5 localization lands, only `_build_message`'s internals change").
  The dynamic builders (`BOOKING_CONFIRMED`, `APPOINTMENT_REMINDER`) keep
  their structure — only the literal English fragments become
  per-language-keyed; the interpolated values (`salon.name`, formatted time,
  manage link) are resolved in the recipient's language.

### Fields explicitly NOT translated

Customer-authored or internal-only text, never salon-authored content:

- `reviews.Review.text` — written by one customer in one language.
- `specialists.TimeOff.reason` — internal scheduling note.
- `booking.Appointment.cancellation_reason` — internal audit note.

Recorded so a later reader does not mistake the omission for an oversight.

### Uniqueness on `(salon, name)` translatable fields

- `ServiceCategory.name` and `Service.name` carry `(salon, name)` uniqueness
  (`servicecategory_salon_name_uniq`, `service_salon_name_uniq`).
- **Uniqueness is checked per individual populated language key**, not
  against one canonical language. Two `Service` rows in the same salon that
  both have `uk: "Стрижка"` collide — even if their `en` values differ or one
  `en` is empty. Any shared non-empty value under the same language key
  within a salon is a conflict.
- This is deliberately stricter than "the `en` values must differ." The
  concrete enforcement mechanism — serializer-level validation against the
  tenant-scoped manager (the § Stage 4 `validate_<field>` pattern, which
  `(salon, X)` constraints already require because DRF drops the automatic
  `UniqueTogetherValidator`), a DB-level expression constraint, or both — is
  a design decision for the implementation sub-step, not fixed here. The
  Stage 4 precedent (explicit serializer check plus the `UniqueViolation →
  400` backstop in `core.exceptions.exception_handler`) is the starting
  point.
- **Landed (09.09.2026):** both. The DB-level mechanism is Sub-step 1's
  "Per-language uniqueness" bullet (two functional unique constraints per
  model, one per supported language); the serializer-level per-language
  `validate_<field>` checks are in § "Read/write serializer split for
  catalog and specialists" ("Uniqueness error message stays generic").

### Email language: `Customer.preferred_language`

Decided and implemented 09.09.2026 — the field landed with Sub-step 1
(migration `accounts.0007`); the send-path wiring landed with § "Notification
message builder: language resolution" below.

- **New field `accounts.Customer.preferred_language`**, set at booking time.
  It selects the language a notification email is rendered in.
- Falls back to `en` when unset (a pre-existing guest row, or a booking flow
  that did not capture it) — consistent with the global `en` fallback above.
- This is where guest language now lives — exactly one home, on `Customer` —
  replacing the deferred "guest language is not stored anywhere yet"
  placeholder.

### Date formatting in localized emails (`core/formatting.py`)

Decided and implemented 09.09.2026 — the `lang` argument and the Ukrainian
tables landed in `core/formatting.py` (Stage 11.5 step 9a); the send path
passes the resolved language code per § "Notification message builder:
language resolution" below.

- `format_datetime_for_salon` currently renders e.g.
  `"Sat, 26 Sep 2026, 14:00"` from **deliberately hardcoded English weekday /
  month tables** — never `%a`/`%b`, which follow the server's OS locale
  (§ Stage 9; the `test_english_names_regardless_of_locale` guard).
- **Ukrainian weekday / month abbreviation tables are added alongside the
  English ones**, same hardcoded-table approach — still never OS-locale
  dependent. The function takes a language argument and selects the table.
- **Component order is identical across languages** — only the day and month
  names are translated, not the layout. `"Sat, 26 Sep 2026, 14:00"` →
  `"Сб, 26 вер. 2026, 14:00"` (24-hour, no timezone label, unchanged).

### Notification message builder: language resolution

Decided and implemented 09.09.2026 — wires the "Notification message
templates" bullet (§ "Fields becoming translatable") and the "Email language:
`Customer.preferred_language`" bullet into `notifications._build_message`.
Neither specified the exact data shape or the resolution call chain; both
are fixed here.

- **New `core.i18n.resolve_language_code(requested: str | None) -> str`:**
  `requested` if `requested in SUPPORTED_LANGUAGES` else `"en"`. Distinct
  from `resolve_translation` (which resolves a `{lang: str}` dict to a
  string) — this resolves a plain language *preference* to a language
  *code*, needed to select which `(subject, body)` tuple to use from
  `_MESSAGES`. Message templates use plain → `en` fallback only (no
  "first non-empty among remaining languages" fallthrough), since every
  template is fully populated in both supported languages.
- **`_MESSAGES` becomes `dict[str, dict[str, tuple[str, str]]]`** —
  `{trigger: {lang_code: (subject, body)}}`. All four static entries
  (`BOOKING_CANCELLED`, `PAYMENT_SUCCEEDED`, `PAYMENT_FAILED`,
  `REVIEW_REQUEST`) plus the two dynamic branches' subject/body literals
  get an `"en"` and a `"uk"` entry.
- **Resolution source:** `notification.customer.preferred_language` if
  `notification.customer` is set, else `None` — resolved via
  `resolve_language_code`, reusing `core.i18n` rather than a new
  mechanism. `notification.customer` is the canonical FK (already used by
  `_resolve_recipient`), not `notification.appointment.customer`, though
  the two coincide for appointment-scoped triggers today.
- **Customer-less (salon-directed) notifications render in English** — the
  natural extension of "falls back to `en` when unset," now covering "no
  `Customer` at all" as well as "`Customer` with an empty
  `preferred_language`."
- **The two dynamic branches (`BOOKING_CONFIRMED`, `APPOINTMENT_REMINDER`)
  keep their current structure:** `salon.name` resolves via
  `resolve_translation` against the same recipient language;
  `format_datetime_for_salon` receives the same language code;
  link / token / slug pass through unchanged. No shared appointment-email
  helper — that refactor stays deferred (§ Step (e) decisions).
- **`send_notification`'s locked fetch adds `select_related("customer")`**
  — a safe, local addition avoiding an extra query per send inside the
  transaction, consistent with the appointment access the same branches
  already trigger.
- **`Salon` lands in this step** (closes the "`Salon` deferred to the
  notification-builder step" bullet under § "Admin display of translatable
  fields"): `Salon.__str__` → `resolve_display_name(self.name,
  f"Salon #{self.pk}")`, matching `ServiceCategory` / `Service` /
  `Specialist`; `SalonAdmin.list_display` gets the same
  `@admin.display`-decorated `resolve_translation(obj.name, None)` pattern
  (no `admin_order_field`) as the catalog / specialists admin. `Salon`
  gets the same `__str__`-fallback and changelist-render test coverage as
  the other three models — the earlier "`Salon` gets no new test here
  (deferred)" note is superseded.
- **All `Salon.objects.create(name=...)` scalar-string test sites** (not
  only `tests/conftest.py`) migrate to dict-shaped names in this step, for
  consistency: leaving any scalar `Salon` fixture in place would silently
  reintroduce the same `AttributeError` risk `resolve_translation`'s
  fail-loud design is meant to surface immediately, the next time
  something calls `str(salon)` against it.

### API language contract for translatable fields

Decided 09.09.2026 — part of the same Stage 11.5 contract, agreed before any
code exists.

- **Read endpoints** (listing services, salon profile, and any other endpoint
  exposing a translatable field) **accept an optional `?lang=en|uk` query
  parameter.** The response returns the **resolved plain string** for that
  field, not the full JSON dict — e.g. `{"name": "Стрижка"}`, never
  `{"name": {"en": "...", "uk": "..."}}`. Rationale: the backend already
  resolves language + `en` fallback, so it returns ready-to-display text
  rather than pushing fallback logic into every frontend component.
- **A missing `?lang=`, a language with no value for that field, or an
  unsupported language code all silently fall back to English.** No 400 for a
  missing or unsupported `lang` value. Rationale: the frontend controls this
  parameter through its language switcher — users do not hand-edit the URL —
  so a bad value only ever signals a frontend bug, not a real user-facing
  edge case worth hard-erroring on, and it keeps fallback uniform with the
  "field not translated into the requested language" case.
- **Full fallback chain (refines the rule above — decided 09.09.2026).** When
  resolving a translatable field for a read response: try the requested
  language; then English. If English is also empty/absent for that field,
  resolution does **not** stop — it falls through to the first non-empty value
  found among the remaining language keys, checked in a fixed deterministic
  order by iterating the supported-language list (no special-casing of exactly
  two languages, so adding a third later needs no change here). Only when
  **every** language key is empty or absent does the field resolve to `""`.
  Rationale: showing text in an unrequested language is a smaller UX cost than
  showing a blank field when the data actually exists — a salon that has
  translated a field into only one language should still have that text
  visible to every visitor, not a gap, and salons naturally fill in more
  languages over time.
- **`?lang=` is a per-request choice, independent of
  `Customer.preferred_language`.** `preferred_language` controls only the
  language a notification email is rendered in (see § Email language above)
  and is not read or written by this parameter.
- **Write endpoints** (creating or editing a translatable field — e.g. a
  salon editing a service name) **use a separate serializer that
  accepts and returns the full language dict**, e.g.
  `{"en": "Haircut", "uk": "Стрижка"}`, so the editor sees and edits all
  languages at once. This mirrors the read/write serializer split
  established in § Stage 11 (Reviews).

### Write-side language key validation

Decided and implemented 09.09.2026 — the write-path counterpart to the read-side
fallback chain above.

- **Write serializers for translatable fields** (`ServiceCategory.name`,
  `Service.name`, `Salon.name`, `Salon.about`, `Specialist.name`,
  `Specialist.bio`) **validate the keys of the incoming dict against the same
  fixed supported-language list the read-side fallback chain iterates**
  (currently `en`, `uk` — read from that one list, not hardcoded to exactly two
  here).
- **Any key outside that list is rejected with a 400** (`ValidationError`). It
  is **not** silently dropped and **not** silently accepted.
- **Rationale.** This is a write path — data the client is trying to persist.
  Silently dropping an unsupported key (e.g. `"fr"`) would present as a
  successful save while quietly discarding what the client entered — the worst
  kind of bug, because nothing signals the loss. This is deliberately *unlike*
  the `?lang=` read-side contract (decided 09.09.2026), where an
  unsupported or missing `lang` silently falls back to English: that is a
  per-request display parameter driven by the frontend's own language switcher,
  not a durable write, so a bad value there signals a frontend bug rather than
  user-entered data being lost.
- **One source of truth for "what languages exist."** The allowlist check lives
  at the serializer level, in the same place as the per-language
  `validate_name` / `validate_bio` uniqueness checks, and reuses the same
  supported-language list as the `resolve_translation` fallback chain — not a
  second copy that could drift.

### Read/write serializer split for catalog and specialists

Decided and implemented 09.09.2026 — how the read/write contract above is wired
into the catalog and specialists endpoints, which are shaped differently from
Stage 11 (Reviews).

- **No separate read/write view classes.** Reviews split the two directions
  into distinct view classes on distinct URLs. Catalog (`ServiceCategory`,
  `Service`) and specialists (`Specialist`) can't: those endpoints are
  `ListCreateAPIView` / `RetrieveUpdateDestroyAPIView`, and a single class
  serves GET and POST/PATCH on the *same* URL through DRF's built-in
  method dispatch. The split is therefore an override of
  `get_serializer_class()` on each existing view class —
  `request.method in SAFE_METHODS` → the read serializer (each translatable
  field resolved to a plain string via `core.i18n.resolve_translation`
  against `?lang=`), otherwise → the write serializer (the full
  `{"en": ..., "uk": ...}` dict, keys validated against
  `core.i18n.SUPPORTED_LANGUAGES` per § "Write-side language key
  validation"). `get_queryset()`, the permission split, and the
  `_apply_visibility` mixins are untouched and stay shared across both
  methods on the one class.
- **Nested serializers resolve too.** `ServiceCategoryMiniSerializer`,
  embedded as the `category` block inside `ServiceSerializer`'s read
  representation, resolves its `name` exactly as the top-level read
  serializer does. Being nested is not an exemption from the read contract.
- **`scheduling.SpecialistsAtTimeView` also resolves.** `GET
  .../availability/specialists/` calls `SpecialistAtTimeSerializer`
  directly — a plain `APIView`, no `get_serializer_class()` — and still
  applies `?lang=` resolution to `name` / `bio`. A field read directly
  rather than through a generic view is still a read and still bound by the
  read contract.
- **`?search=` fixed for the JSONField shape.**
  `_CatalogViewMixin._apply_visibility` did
  `queryset.filter(name__icontains=search)`, which only worked while `name`
  was a plain `CharField`. Against a `JSONField` that same lookup matches
  the JSON's literal serialized text — keys, quotes, commas and all —
  instead of the translated values, i.e. nonsensical search. Replaced by an
  OR'd `Q` filter over `name__<lang>__icontains=search` for every `lang` in
  `core.i18n.SUPPORTED_LANGUAGES` (Django's JSONField key-transform
  lookup), not hardcoded to two. Rationale: a caller searches in whichever
  language they are thinking in; restricting the match to one language
  (English, or the requested `?lang=`) would be an arbitrary limit with no
  upside.
- **Uniqueness error message stays generic (09.09.2026).**
  `ServiceCategoryWriteSerializer.validate_name`'s per-language check raises
  the same `"A category with this name already exists."` whichever language
  key collided — it does not name the language (no "already exists in
  Ukrainian"). This matches the pre-existing Stage 4 message for the old
  whole-dict check. Rationale: the frontend has its own localized copy for
  displaying errors, and a language-specific backend string would need its
  own translation while adding nothing the frontend can't already infer from
  which field (`name`) the error is attached to.

Concretely, for `Service` (09.09.2026): `ServiceReadSerializer`'s nested
`category` block — a `ServiceCategoryMiniSerializer` — returns `category.name`
as a plain string already resolved for the request's `?lang=`, exactly like the
top-level `name` field, never the raw `{"en": ..., "uk": ...}` dict. This works
because `ServiceCategoryMiniSerializer.name` is its own `SerializerMethodField`
calling `core.i18n.resolve_translation`, and the nested serializer inherits
`self.context` (hence the `request`) from the parent `ServiceReadSerializer`
when instantiated the ordinary declarative way. Confirmed empirically by test:
a `Service` response's `category.name` follows the `?lang=` on *that* request,
resolving per-request rather than from any cached or default value — it can
differ from what a direct `ServiceCategory` endpoint call for the same category
under a different `?lang=` would return.

### Wiring `?lang=` into the Reviews read endpoint

Decided 09.09.2026 — closes the gap left when the catalog/specialists
implementation section above did not enumerate reviews. Reviews already has
its own separate read view (a plain `APIView`, `reviews.ReviewListView`),
unlike catalog/specialists, so the `get_serializer_class()` override
described in § "Read/write serializer split for catalog and specialists"
never touched it.

- **`ReviewSpecialistSerializer` (`reviews/serializers.py`), embedded as the
  `specialist` block of each `{specialist, reviews}` group in the public
  list response, resolves `name` against `?lang=` exactly per the general
  "API language contract" above.** No new resolution rule — this is the
  existing contract applied to a previously-unwired endpoint. Being reached
  through a non-generic `APIView` is not an exemption, the same precedent
  `scheduling.SpecialistsAtTimeView` / `SpecialistAtTimeSerializer` already
  established.
- **Two-part wiring, both required.** (1) `reviews/serializers.py` gains a
  `name = serializers.SerializerMethodField()` + `get_name` on
  `ReviewSpecialistSerializer`, mirroring
  `specialists.SpecialistReadSerializer._resolved` / `get_name`
  (`core.i18n.resolve_translation` against
  `context["request"].query_params.get("lang")`). (2) `reviews/views.py`
  must pass `context={"request": request}` into the
  `ReviewSpecialistSerializer(...)` call in `ReviewListView.get` — unlike a
  nested serializer instantiated declaratively (which inherits the parent's
  context), this one is constructed by hand and has no context otherwise,
  so `get_name` would have no `?lang=` to read.
- **Nothing else in the § Stage 11 Part 2 read-endpoint contract changes.**
  Grouping, group ordering (review count desc, lower `specialist` id as
  tiebreak), within-group ordering (`created_at` desc), and the per-review
  minimum shape (`id`, `rating`, `text`, `created_at`) are all unaffected.
- **Test adaptation (mechanical, no contract change).**
  `tests/test_review_list_endpoint.py`'s two file-local specialist fixtures
  (`specialist2`, `o_specialist`) move to dict-shaped names
  (`{"en": "Zoe"}` / `{"en": "Otto"}`) — the shared `conftest.specialist`
  fixture is already dict-shaped from earlier Stage 11.5 work and needs no
  change — and the `groups[...]["specialist"]["name"]` assertion compares
  against the resolved plain string rather than the raw `Specialist.name`
  dict.

### Sub-step 1 (model changes) — decided 09.09.2026

The concrete model-layer shape, agreed and then implemented in the same
change. Migrations: `catalog.0003`, `tenants.0006`, `specialists.0004`,
`accounts.0007`. All five affected tables were empty in every environment, so
the migration is a plain `AlterField`/`AddField`/`RemoveConstraint`/
`AddConstraint` set with no data step.

- **Storage.** `ServiceCategory.name`, `Service.name`, `Salon.name`,
  `Specialist.name`, `Specialist.bio` become `JSONField(default=dict)`
  (`bio` keeps `blank=True`). `default=dict` — empty dict is the
  "untranslated" state, mirroring `blank=""` elsewhere; not `null=True`.
  A bare string is still valid JSON, so pre-existing rows / string-shaped
  input round-trip until the serializer layer (sub-step 2) enforces the dict.
- **`Salon.about`** — new `JSONField(default=dict, blank=True)`. Per-language
  length cap enforced by `CheckConstraint`
  `salon_about_max_length_2000_per_language`:
  `LENGTH(about ->> 'en') <= 2000 AND LENGTH(about ->> 'uk') <= 2000`
  (built from `Length(KeyTextTransform(lang, "about"))` per language). A
  missing key makes `about ->> lang` SQL NULL, so that language's check is
  NULL, not false — an untranslated language passes. Adding a language = add
  its clause here in a migration. Same intent as
  `reviews.Review.text`'s `review_text_max_length_2000`, adapted from a
  scalar field to per-key.
- **`Customer.preferred_language`** — new
  `CharField(max_length=8, blank=True, default="")`. **Deliberately no
  `choices=`**: § "Languages and fallback" requires that nothing in the
  storage or API shape assume exactly `{en, uk}`, and a `choices` list bakes
  that assumption into a migration + a validator. `""` = unset → the send
  path falls back to `en`.
- **`Meta.ordering`** (each a required consequence — the old key `name` is
  now a dict and cannot be a meaningful `ORDER BY`):
  `ServiceCategory` and `Service` `["ordering", "name"]` → `["ordering", "id"]`
  (`id` chosen over dropping the tiebreak entirely: the list endpoints
  paginate and need a deterministic order); `Salon` `["name"]` →
  `["created_at"]`; `Specialist` `["name", "id"]` → `["created_at", "id"]`
  (`id` keeps its prior tiebreak role).
- **Per-language uniqueness.** The single
  `UniqueConstraint(fields=["salon", "name"])` on each of `ServiceCategory`
  and `Service` (`servicecategory_salon_name_uniq`, `service_salon_name_uniq`)
  is replaced by **two functional unique constraints per model, one per
  supported language**: `servicecategory_salon_name_en_uniq` /
  `servicecategory_salon_name_uk_uniq` and the `service_…` pair. Each is
  `UniqueConstraint("salon", NullIf(KeyTextTransform(lang, "name"), Value("")))`
  →
  `CREATE UNIQUE INDEX … ON … ("salon_id", (NULLIF(("name" ->> 'lang'), '')))`.
  `NULLIF(…, '')` folds an absent *or* empty value to SQL NULL, and Postgres
  treats NULLs as distinct, so any number of rows still lacking a translation
  for that language never collide — only a shared non-empty value under the
  same language key within a salon does. This is the mechanism behind
  § "Uniqueness on `(salon, name)` translatable fields". `NullIf` is a
  two-arg `Func` subclass in `catalog/models.py`.
- **`core/exceptions.py` needs no change.** Its handler already maps *every*
  `psycopg.errors.UniqueViolation` to a generic `unique_violation` 400 and
  deliberately does not parse constraint names (the boundary note in that
  file) — so the four new names are covered with no edit, and the two old
  names appeared nowhere outside `catalog/` anyway.
- **Not done here** (later sub-steps): serializer resolution / `?lang=` /
  the read-write serializer split, the `catalog` `validate_<field>` checks
  (which now compare a dict), admin `list_display`, and the notification
  message builder. Existing tests that treat these fields as scalars fail
  after this sub-step by design.
  - **All five closed 09.09.2026.** Read-side `?lang=` resolution:
    § "API language contract for translatable fields". Read/write
    serializer split: § "Read/write serializer split for catalog and
    specialists" and § "Wiring `?lang=` into the Reviews read endpoint".
    Dict-aware `validate_<field>` / per-language uniqueness:
    § "Write-side language key validation" and the "Per-language
    uniqueness" bullet above. Admin `list_display`: § "Admin display of
    translatable fields". Notification message builder: § "Notification
    message builder: language resolution".

### Admin display of translatable fields

Decided 09.09.2026 — Django admin has no per-request language equivalent
(staff / admin-panel interface language was already marked out of scope for
Stage 11.5, § "Explicitly out of scope"). Every translatable field shown in
admin resolves through the **no-`?lang=` fallback chain** —
`resolve_translation(value, None)`: requested language (none here) → English
→ first non-empty supported language → `""` — with no language selector.

**Scope corrected 09.09.2026, before implementation:** this step covers
**three** models — `ServiceCategory`, `Service`, `Specialist`. The fourth,
`Salon`, was deferred to the notification-builder step and **closed there
09.09.2026** (see the "`Salon` deferred" bullet below).

**`__str__` gets an identifying fallback; `list_display` columns do not.**

- **`__str__`** on `ServiceCategory`, `Service`, `Specialist` (the models
  whose `__str__` touches a translatable field, minus `Salon`) now returns
  `core.i18n.resolve_display_name(self.<field>, f"<Model> #{self.pk}")`.
  `resolve_display_name(value, fallback)` is a thin wrapper —
  `resolve_translation(value, None) or fallback` — living beside
  `resolve_translation` in `core/i18n.py` so the fallback rule has one home
  and isn't copied into the model files.
  - **Why a fallback here:** `__str__` renders the admin changelist **link**
    column and every FK / M2M column, `list_filter` dropdown, and
    autocomplete entry that points at these models. A row whose every
    language key is empty would otherwise render as an empty clickable cell
    and become effectively unfindable / unselectable — a functional
    regression, not a cosmetic one. `"<Model> #<pk>"` keeps the row
    identifiable. This is a deliberate, admin-only divergence from the plain
    API `?lang=` behaviour, where a fully-empty field correctly resolves to
    `""`.
  - This single change per model fixes every **indirect** exposure across the
    app at once — no other `admin.py` file is touched for the `__str__` path.
    `Customer.__str__` / `Account.__str__` / `Appointment.__str__` etc. that
    interpolate one of these objects inherit the fix for free.

- **`list_display`** columns that name a translatable field directly
  (`ServiceCategoryAdmin` `name`, `ServiceAdmin` `name`, `SpecialistAdmin`
  `name`) are resolved via a small per-admin `@admin.display`-decorated
  method returning `resolve_translation(obj.<field>, None)` — **no fallback
  string**. Django formats a direct field-name column through the field's own
  formatter, not through `__str__`, so these need their own method; an empty
  cell for a fully-untranslated row is acceptable in a data column (it
  already rendered as `{}` before this change) and matches the API contract
  exactly.
  - No `admin_order_field` on these methods: a `{lang_code: string}` dict is
    not a meaningful `ORDER BY` key, consistent with the `Meta.ordering →
    id` decision in the storage sub-step.

- **`Salon` deferred to the notification-builder step.** `Salon.name` is a
  `JSONField` at the model level, but the shared `tests/conftest.py`
  `salon` / `other_salon` fixtures still seed it as a **scalar string** —
  deliberately, because `notifications/services.py` still interpolates a raw
  `{salon.name}` f-string into email bodies (DECISIONS.md § storage
  sub-step's "Not done here" list — the notification message builder is a
  later step). `resolve_translation` raises `AttributeError` on a non-dict
  input by design (its fail-loud contract, confirmed in the step-6 recon —
  the docstring's tolerance covers malformed *dicts* only, not non-dict
  values), so routing `Salon.__str__` or a `SalonAdmin` column through it
  now would crash every `str(salon)` path against the scalar fixture —
  directly and via `Customer` / `Account` / `Appointment` `__str__`. Rather
  than weaken `resolve_translation` or migrate the fixture ahead of the
  builder, `Salon`'s admin display (`Salon.__str__` + `SalonAdmin`'s `name`
  column) lands **together with** the `conftest` fixture migration and the
  `notifications/services.py` `{salon.name}` fix, in the step that does that
  work. Until then `Salon.__str__` stays `str(self.name)` and
  `SalonAdmin.list_display` keeps the bare `"name"` string.
  - **Closed 09.09.2026** by § "Notification message builder: language
    resolution" above: `Salon.__str__`, `SalonAdmin`, the `conftest`
    fixture migration (extended to all scalar `Salon.objects.create`
    sites), and the `{salon.name}` fix all land in that step, with `Salon`
    test coverage matching the other three models.

- **`Salon.about` and `Specialist.bio`** are referenced in no `list_display`
  and no `__str__` today — no admin change is needed for them. Recorded here
  so their absence from this change isn't later read as an oversight.

The one existing admin-changelist render test
(`test_admin_tenant_scoping.py::test_admin_changelist_reaches_across_tenants`)
was updated for the resolved `name` column; changelist-render coverage for
`Service` and `Specialist` and `__str__`-fallback coverage for all three
in-scope models was added in the same step. `Salon` got no new test in the
admin-display step — its `__str__`-fallback and changelist-render coverage
was added with the notification-builder step (§ "Notification message
builder: language resolution"), which pulled `Salon` into scope. The
DECISIONS.md correction landed first, on its own; the implementation and its
test changes followed.

### Bare-string test fixtures on translatable fields — swept

Decided and implemented 09.09.2026 — 39 test call sites (36 direct + 3
helper functions) across 15 files (booking / availability / scheduling /
payments / reviews-model tests) passed a bare string to
ServiceCategory.name / Service.name / Specialist.name instead of the
{lang: text} dict shape. None of these tests routed the field through
resolve_translation, resolve_display_name, or any serializer/admin path,
so they passed regardless. Swept to dict shape for consistency across the
codebase, rather than left as a latent inconsistency relying on
resolve_display_name's fail-loud design to catch it later.

### Explicitly out of scope for Stage 11.5

- **Frontend UI-string translation** — labels, buttons, static interface
  copy. That is a separate frontend i18n concern (e.g. `next-intl`), not a
  backend data concern, and lands with the frontend stages.
- **Staff / admin-panel interface language** — also a future frontend
  concern, unrelated to the backend content fields above.

Stage 11.5 is strictly the backend content-data layer: which salon-authored
text is translatable, how it is stored and queried, and which language an
email goes out in.

### Stage 11.5 status: closed

The backend content-data layer for translatable salon-authored text is
complete: `JSONField` storage for `Service` / `ServiceCategory` /
`Specialist` / `Salon.name` plus the new `Salon.about`;
`resolve_translation()` / `resolve_display_name()` / `resolve_language_code()`
in `core/i18n.py`; `?lang=` read resolution wired into the catalog,
specialists, and reviews endpoints; dict-aware `validate_<field>` checks with
per-language uniqueness; admin `list_display` via `resolve_display_name()`;
and per-language notification templates (Ukrainian genitive date forms
included). No Salon profile read/write API endpoint was built — that is out
of scope here (see the § Open questions bullet "Salon profile API": deferred,
to be resolved when Stage 12/13 scope is written). Stage 11.5 was the
data-layer only, not an endpoint surface. Final gate clean: full suite
620/620, `ruff check`, `ruff format --check`, `mypy`, and
`makemigrations --check` all pass (2026-09-10).

## Stage 12 (Frontend skeleton — design system, API client, auth)

Decided 10.09.2026 — contract agreed before any code, per the stage-by-stage
workflow. This section is the spec the implementation sub-steps build to.

### Authenticated session transport: httpOnly cookie, not localStorage

- **The authenticated `Account` JWT session is held client-side in an httpOnly
  cookie set by the backend, never in `localStorage` or `sessionStorage` and
  never in JS-readable state.** An httpOnly cookie is unreadable from page
  JavaScript, so an XSS foothold in the Next.js app cannot exfiltrate the
  access or refresh token. `localStorage` tokens are the standard XSS
  token-theft target and are rejected for that reason.
- This diverges from the one-time credential tokens (email verification,
  password reset) and the guest access token, whose transports were fixed in
  Stage 3 / Stage 9 § Step (d) (URL fragment and URL path respectively). Those
  are single-purpose links handled by page JS on arrival; the authenticated
  session is a long-lived ambient credential and takes the cookie path
  instead. The guest-token `X-Guest-Token` header mechanism is unchanged.

### Consequences (agreed in principle, parameters deferred below)

- **CORS must be configured on the backend.** The Next.js origin differs from
  the API origin in every environment (`localhost:3000` vs the backend host in
  dev; separate hosts in prod), so `django-cors-headers` (or equivalent) is
  added, with credentialed requests enabled
  (`CORS_ALLOW_CREDENTIALS = True`) so the browser sends the session cookie on
  XHR/fetch to the API. `django-cors-headers` is not currently a dependency
  (confirmed 2026-09-10) — it lands in this stage.
- **Cookie attributes carry the CSRF mitigation.** Because the session now
  rides a cookie, the backend sets `SameSite` on it (value chosen in the open
  questions below) and `Secure` in production only — `production.py` already
  sets `SESSION_COOKIE_SECURE = True` / `CSRF_COOKIE_SECURE = True`; dev over
  plain HTTP keeps `Secure` off so the cookie works on `localhost`.
  `base.py` currently sets no cookie attributes at all.

### Frontend stack and tooling

Decided 10.09.2026.

- **Framework: Next.js 16.3.4, App Router.** React 19.3.0, React DOM 19.3.0.
- **TypeScript: pinned to 6.0.3 — not the unpinned `latest` tag.** As of
  this date `latest` resolves to TypeScript 7.0.2, and Next.js 16.3 rejects
  TypeScript `>=7.0` with a hard build error: TS7's native (Go) compiler does
  not yet expose the programmatic compiler API that Next.js — and the
  `typescript-eslint` dependency inside `eslint-config-next` — require. That
  API is expected in TypeScript 7.1, which is not yet released. `package.json`
  must pin an exact `6.0.3` (a caret range would drift into 7.x); `6.0.3` is
  the newest stable release in the 6.x line.
- **Styling: Tailwind CSS 4.3.3.** v4's CSS-first setup: configuration lives
  in the stylesheet via `@theme`, with no `tailwind.config.js` generated or
  required by default (a JS config is opt-in via `@config`). The PostCSS
  integration is the separate `@tailwindcss/postcss` package — the old
  in-`tailwindcss` PostCSS plugin and `autoprefixer`/`postcss-import`
  companions are not used.
- **Linting: `eslint-config-next` 16.3.4, flat config only** (`eslint.config.mjs`).
  The legacy `.eslintrc.*` format is not used.
- **API client: none yet — no Axios, no tRPC, no OpenAPI codegen.** A small
  hand-written typed wrapper over `fetch` covers this stage. There is no
  generated client from the DRF OpenAPI schema because that schema surface
  does not exist yet (OpenAPI/Swagger is still unlanded on the backend per the
  stage order). Revisit if and when a generated client earns its weight.
- **This is an initial pin set, not a long-term lock.** These versions will be
  revisited when TypeScript 7.1 stabilizes the toolchain, or if project
  requirements change before then.

### Open questions — resolve in the next sub-step, not decided here

- **Exact cookie name(s)** — and whether access and refresh tokens share one
  cookie or use two with different paths/lifetimes.
- **Exact `SameSite` value** — `Lax` vs `Strict`. `Strict` is stronger against
  CSRF but breaks top-level cross-site navigation into an authenticated view;
  `Lax` is the usual compromise. Depends on whether any authenticated deep
  link is reached by cross-site navigation.
- **CORS allowed-origins list, dev vs prod** — the concrete origin values,
  whether they come from an env var, and how many prod origins (apex +
  per-salon subdomains?) must be allowed.
