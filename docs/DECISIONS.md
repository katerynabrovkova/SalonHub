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
- **UI chrome i18n — not yet scheduled to a specific stage.** Static
  interface labels (button text, navigation, empty-state messages — e.g.
  "Далі", "Назад", "Забронювати", "Інформація про послугу") have no
  translation mechanism. This is distinct from the existing Stage 11.5
  content-localization system (`?lang=en|uk` for salon-provided data:
  service/category names, `Salon.about`, `Specialist.bio`) — Stage 11.5's
  own "Explicitly out of scope" section already named frontend UI-string
  translation as a separate, future frontend concern, not a backend data
  concern. Until UI i18n lands, all interface chrome is hardcoded
  Ukrainian, with no locale-switching mechanism. `docs/ARCHITECTURE.md`
  §§ on Stage 11.5 notification formatting record the same carve-out for
  admin/staff interface language. Flagged here rather than left to fall
  out of a stage's scope discovery, since no stage in the roadmap above
  currently commits to building it.
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

  **Partially superseded 2026-09-10 by § Stage 12 "Frontend routing:
  subdomain-based".** The `/salons/<slug>/` path segment shown here is
  scheduled to change to a subdomain form
  (`https://<slug>.salonhub.com/appointments/<id>/manage/<token>/`) once that
  follow-up is implemented — tracked there, not done yet. Everything else in
  this bullet stands: token in the path (not query/fragment), persistent
  resource link, the accepted-risk rationale.
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

### Cookie mechanism resolved

Decided 2026-09-10. Closes the "exact cookie name(s)" and "exact `SameSite`
value" open questions below; the CORS allowed-origins list stays open.

- **Django itself sets the session as two separate httpOnly cookies on the
  response** — one for the access token, one for the refresh token. Tokens are
  no longer returned in the JSON body, and there is no Next.js proxy / route
  handler layer mediating the cookies: the browser talks to the Django API
  directly and the `Set-Cookie` comes straight from the DRF view.
- **Two cookies, not one shared cookie**, because their scopes differ:
  - **Access token cookie** — no `Path` restriction (effectively `Path=/`), so
    it rides every request to the API. This is what the authentication class
    reads on normal authenticated calls.
  - **Refresh token cookie** — `Path=/api/v1/salons/<slug>/auth/refresh/`, so
    the browser only attaches it to the refresh endpoint itself. It never
    reaches any other view, shrinking the surface on which a long-lived
    credential is exposed. (One consequence: the path is per-salon, so a
    browser session is scoped to the salon it logged into — consistent with the
    tenant-scoped login.)

  Note explicitly: because the access cookie has no `Path` restriction and
  shares one name per domain, logging into salon B overwrites the salon-A
  session cookie in the same browser. This is accepted as intentional — a user
  is expected to be logged into one salon at a time. Do not treat this as a bug
  to fix later.

  **Superseded 2026-09-10 by "Frontend routing: subdomain-based" below.** That
  note was written when the frontend routing strategy was still undecided and
  path-based (`salonhub.com/<slug>`) was the working assumption — one domain,
  so one shared cookie name, so one session at a time. With subdomain routing
  (`<slug>.salonhub.com`) each salon is its own origin and the auth cookies are
  host-only (no `Domain=` attribute — see below), so salon A's and salon B's
  session cookies no longer collide: a user *can* hold independent, simultaneous
  sessions in different salons in the same browser, and that is now the intended
  behaviour, consistent with `Account` email uniqueness being per-salon rather
  than global.
- **`SameSite=Lax`** on both. `Strict` was rejected because it drops the cookie
  on top-level cross-site navigation into an authenticated view (e.g. following
  a link from an email into a booking-management page), which is a flow this
  product has. `Lax` is the standard compromise and still blocks the
  cross-site subrequests that matter for CSRF.
- **`Secure=True` in production only.** `production.py` already forces
  `SESSION_COOKIE_SECURE` / `CSRF_COOKIE_SECURE`; the JWT cookies follow the
  same rule (env- or settings-module-driven), off in dev so `localhost` over
  plain HTTP works.
- **CSRF for state-changing requests rides a separate non-httpOnly CSRF cookie
  plus a matching request header**, checked on unsafe methods. `SameSite=Lax`
  already blocks the classic cross-site form POST; the cookie+header check is
  the defense-in-depth layer for the cases `Lax` does not cover (same-site
  subdomains, `Lax`'s top-level POST allowance). Exact cookie name, header
  name, and where the check lives (DRF-level vs middleware) are detailed when
  this is implemented.

**What this requires (implementation, not yet done):**

1. **`LoginView` / `RefreshView` set cookies** instead of (or alongside)
   returning JSON tokens — the response writes `Set-Cookie` for the access and
   refresh cookies with the attributes above, and `LogoutView` clears them.
2. **A custom DRF authentication class** that reads the JWT from the access
   cookie rather than the `Authorization: Bearer` header, slotted into
   `DEFAULT_AUTHENTICATION_CLASSES` (or per-view) alongside / replacing the
   stock `JWTAuthentication`.

**This decision reopens `accounts/` — Stage 3 / Stage 9 territory — and that is
intentional.** The cookie transport was explicitly deferred to Stage 12 (see
the transport decision at the top of this section), so touching `LoginView`,
`RefreshView`, `LogoutView` and adding an auth class now is the planned
continuation of that deferral, not a breach of the "don't reopen a closed
stage" rule. The stage-order constraint is about not implementing *ahead*;
finishing a deliberately-deferred piece of an earlier stage is expected.

### CSRF protection resolved

Decided 2026-09-10. Closes the "CSRF cookie/header names and check location"
open question below. Contract agreed before code, per the stage-by-stage
workflow.

- **Reuse Django's built-in CSRF engine (`django.middleware.csrf`), not a
  hand-rolled double-submit check.** Django's token is signed and per-request
  masked (BREACH-resistant) and is compared in constant time; a bespoke
  double-submit would re-implement all of that worse. DRF's own
  `SessionAuthentication.enforce_csrf()` is the reference pattern to follow: it
  instantiates a `CsrfViewMiddleware`, calls its `process_request()` then
  `process_view()` manually, and raises `PermissionDenied` on failure. Our
  check mirrors that shape.
- **The check is wired into
  `accounts.authentication.AccountJWTCookieAuthentication.authenticate()`**,
  not middleware and not a DRF permission class. It runs the
  `enforce_csrf`-style logic only when **both**:
  1. the request method is unsafe (`POST` / `PUT` / `PATCH` / `DELETE`), and
  2. the access token was read from the **cookie**, not the
     `Authorization: Bearer` header.

  A non-browser client presenting a Bearer header is therefore exempt — it is
  not subject to ambient-credential CSRF, the same cookie-vs-header split
  rationale already used for the transport decision. `AccountJWTAuthentication`
  (the header class) gets no CSRF check.
- **Scope — endpoints protected:** every endpoint listed as an
  "Account-authenticated write endpoint" in the Stage 12 recon inventory
  (catalog category/service create + update + delete, specialist create +
  update + delete, appointment review create), **plus** `auth/login/`,
  `auth/refresh/` and `auth/logout/` explicitly. Login-CSRF **is** in scope
  (decided 2026-09-10): the cost is one extra bootstrap `GET` before the login
  form renders, and that is accepted against the real risk of a forged
  cross-site login that logs a victim into an attacker-controlled account.
  `auth/login/` and `auth/refresh/` are `AllowAny` and cookie-driven rather
  than access-cookie-authenticated, so their CSRF check is wired at the view
  level following the same `enforce_csrf` pattern, not via the authentication
  class.
- **New endpoint: `GET /api/v1/salons/<slug>/auth/csrf/`** — `AllowAny`. Calls
  `django.middleware.csrf.get_token(request)` to force the CSRF cookie onto the
  response, returns `204` with no body. The frontend calls this once before
  rendering the login form (and any time it needs to (re)prime the token).
- **`CSRF_COOKIE_HTTPONLY` stays `False`** (Django default). The frontend JS
  must read this cookie's value to echo it in the request header, so it cannot
  be httpOnly. This is **not** in tension with the access/refresh cookies being
  httpOnly: the CSRF cookie is not a credential on its own — possessing it
  grants nothing; it only demonstrates that the request was issued by a
  same-origin script that could read a same-origin cookie. The session tokens
  remain unreadable to JS.
- **`CSRF_COOKIE_SAMESITE = "Lax"`** — matches the auth cookies.
  **`CSRF_COOKIE_SECURE`** follows the existing settings-module split (already
  `True` in `production.py`, unset/`False` in development).
- **Header name: Django's default `X-CSRFToken`** — no reason to diverge. The
  frontend API client reads the `csrftoken` cookie and sets `X-CSRFToken` on
  every unsafe-method request. `CSRF_HEADER_NAME` / `CSRF_COOKIE_NAME` stay at
  their defaults.
- **Explicitly out of scope for this sub-step:**
  - Guest-token-authenticated endpoints (`bookings/`,
    `guest/appointments/<id>/cancel/`, `.../pay/`, and the guest path of
    review-create). `X-Guest-Token` is a custom request header, not an ambient
    cookie — a cross-site page cannot set it without a CORS preflight the API
    will not grant, so these are already immune to CSRF (per the recon
    inventory).
  - The payment webhook (`/api/v1/webhooks/payments/`). HMAC-signature
    verified, not cookie-authenticated, called by an external system.
  - The other `AllowAny` unauthenticated `auth/` endpoints (`register/`,
    `verify-email/`, `password-reset/`, `password-reset/confirm/`,
    `resend-verification/`) — no ambient credential to abuse; a forged POST
    achieves nothing an attacker could not do directly.

**What this requires (implementation, not yet done):**

1. CSRF settings block in `config/settings/base.py` (`CSRF_COOKIE_SAMESITE`,
   `CSRF_COOKIE_HTTPONLY` explicit-`False`; `CSRF_COOKIE_SECURE` in
   `production.py` already present).
2. The `enforce_csrf`-style check in
   `AccountJWTCookieAuthentication.authenticate()`, gated on unsafe method +
   cookie-sourced token, plus the equivalent guard on `LoginView` /
   `RefreshView` / `LogoutView`.
3. `AuthCsrfView` (`GET auth/csrf/`) and its route in `accounts/salon_urls.py`.
4. Frontend API client: prime via `auth/csrf/`, read `csrftoken`, send
   `X-CSRFToken` on unsafe methods (lands with the frontend client sub-step).

### Open questions — resolve in the next sub-step, not decided here

- **CORS allowed-origins list, dev vs prod** — the concrete origin values,
  whether they come from an env var, and how many prod origins (apex +
  per-salon subdomains?) must be allowed.

## Stage 12: API client wrapper

Decided and implemented 2026-09-10.

- `apiRequest<T>(slug, path, options)` — explicit salon slug parameter, no
  global/module-level state. How the frontend determines the active slug per
  page was left open here and is now resolved as subdomain-based routing — see
  "Frontend routing: subdomain-based" below. The client stays agnostic to it
  regardless (it takes `slug` as an argument), so the resolution does not
  change `client.ts`.
- Browser-only: reads the CSRF token from `document.cookie`, so this client
  cannot run in Server Components — only Client Components / event handlers.
  Server-side data fetching is a separate future decision if a use case
  appears.
- Every request sent with `credentials: 'include'`. Non-safe methods
  (POST/PATCH/PUT/DELETE) attach `X-CSRFToken` read from the `csrftoken`
  cookie.
- Errors surfaced as one `ApiError` class (`status`, `code`, `message`,
  `details`), mirroring the backend's single `{error:{code,message,details}}`
  envelope (confirmed by recon in `core/exceptions.py`). Network failures
  (fetch throwing before a response) are not wrapped — propagate as-is.
- `NEXT_PUBLIC_API_URL=http://localhost:8000` (frontend, dev default) and
  `CORS_ALLOWED_ORIGINS=["http://localhost:3000"]` (backend, closing the
  Stage 12 TODO) added — required for any browser fetch to pass CORS at all.
- No automated tests added in this step — frontend has no test runner yet.
  Verified manually via curl/browser. Adding a frontend test framework is a
  separate near-term decision.

### Frontend routing: subdomain-based

Decided 2026-09-10. Supersedes the "separate, unresolved decision" note in
"Stage 12: API client wrapper" above and the "one salon at a time" note in
"Cookie mechanism resolved".

- **Each tenant is served from its own subdomain: `<slug>.salonhub.com` in
  production, `<slug>.localhost:3000` in development.** Browsers resolve any
  `*.localhost` name to `127.0.0.1` automatically, so dev needs no hosts-file
  edit and no local DNS. Path-based routing (`salonhub.com/<slug>/...`) was the
  earlier working assumption and is rejected: distinct origins per tenant give
  cleaner cookie/storage isolation and match how the session cookies already
  behave.
- **The backend tenant-resolution path prefix (`/api/v1/salons/<slug>/...`) is
  UNCHANGED.** This decision is about frontend *page* routing only. The API
  keeps its path-prefix tenant resolution (§ Stage 1); the Next.js app reads
  the active slug from `window.location.hostname` and passes it to
  `apiRequest(slug, ...)`, which still builds `/api/v1/salons/<slug>/...` URLs.
- **Cookie scoping: no `Domain=` attribute is set on the auth cookies**
  (`accounts/cookies.py` — confirmed still true as of this recon; also true for
  the `csrftoken` cookie, which Django sets with no domain). This is now a
  deliberate choice, not an incidental fact. A cookie with no `Domain=` is
  host-only: it is sent back only to the exact host that set it. So
  `<salon-a>.salonhub.com` and `<salon-b>.salonhub.com` each get their own
  isolated `access_token` / `refresh_token` / `csrftoken` cookies, and a user
  can be logged into several salons at once in one browser with no cookie
  collision. This is consistent with `Account` having per-salon (not global)
  email uniqueness — the same person at two salons is two independent
  `Account`s and now genuinely two independent browser sessions.
- **CORS: `CORS_ALLOWED_ORIGIN_REGEXES`, not a hardcoded origin list.** The
  single `CORS_ALLOWED_ORIGINS = ["http://localhost:3000"]` planned in "Stage
  12: API client wrapper" cannot express "any salon subdomain". Two regex
  patterns are needed:
  - prod: `^https://[\w-]+\.salonhub\.com$`
  - dev: `^http://[\w-]+\.localhost:3000$`

  `CORS_ALLOW_CREDENTIALS = True` is unchanged. **Implemented 2026-09-10:**
  `CORS_ALLOWED_ORIGIN_REGEXES` in `config/settings/base.py` replaces the
  hardcoded `CORS_ALLOWED_ORIGINS`, with both dev and prod patterns fully
  anchored (`^...$`) so a suffix-spoofed host cannot slip through. The prod
  pattern is built from the `PLATFORM_DOMAIN` env var via `re.escape`. Covered
  by `tests/test_cors_csrf_settings.py`.
- **CSRF: `CSRF_TRUSTED_ORIGINS` will need wildcard-subdomain entries.**
  **Implemented 2026-09-10:** `CSRF_TRUSTED_ORIGINS = ["https://*.{PLATFORM_DOMAIN}"]`
  in `config/settings/base.py`, built from the new `PLATFORM_DOMAIN` env var
  (default `salonhub.com`, a placeholder — same caveat as `FRONTEND_URL`), so
  Django's CSRF origin check accepts unsafe requests coming from a salon
  subdomain. Covered by `tests/test_cors_csrf_settings.py`.
- **Apex domain (`salonhub.com`, or `localhost:3000` with no subdomain) serves
  a minimal placeholder for now**, not a full marketing landing page. The full
  landing-page design is deferred to the frontend polish stages (18–21).

**Tracked open follow-up (NOT part of this docs change — do not implement
here):** the emailed links still use the path-based frontend shape and must be
migrated to the subdomain shape when this lands:

- `notifications/services.py` — the guest manage-appointment link, built in
  both the `APPOINTMENT_REMINDER` and `BOOKING_CONFIRMED` branches of
  `_build_message`, currently
  `{FRONTEND_URL}/salons/<slug>/appointments/<id>/manage/<token>/`.
- `accounts/tasks.py` — `send_verification_email` and
  `send_password_reset_email`, currently
  `{FRONTEND_URL}/salons/<slug>/verify-email#token=...` and
  `.../reset-password#uid=...&token=...`.

The target shape is subdomain-based (e.g.
`https://<slug>.salonhub.com/appointments/<id>/manage/<token>/`), which also
implies `FRONTEND_URL` can no longer be a single fixed base URL — it needs a
per-salon host. Four tests in `backend/tests/test_notifications_send_service.py`
hard-assert the current path shape and will need updating:
`test_build_message_for_booking_confirmed_builds_subject_body_and_link`,
`test_build_message_for_booking_confirmed_link_carries_the_re_derived_token`,
`test_build_message_for_appointment_reminder_builds_subject_body_and_link`
(which asserts the whole email body verbatim), and
`test_build_message_for_appointment_reminder_link_carries_the_re_derived_token`.

### `/me/` endpoint (Stage 12)

Decided 2026-09-11.

- New endpoint: `GET /api/v1/salons/<slug>/auth/me/`, authenticated
  (Account, cookie-based JWT via AccountJWTCookieAuthentication).
- Returns only `email` and `role`. No `salon` field — the frontend
  already has the slug from the subdomain before login happens.
- No `email_verified_at` — deferred until an actual "verify your email"
  UI exists (YAGNI); verification does not gate login itself, only the
  guest→account Customer merge.
- Purpose: after login (which returns an empty body per Stage 3-R.E),
  the frontend calls `/me/` to learn the Account's role and redirect
  accordingly — `client` → client dashboard, `admin` → salon admin
  panel. There is no third "specialist" role; specialists never
  authenticate (see Stage 3-R, "No specialist logins in this build").

### Dev environment: API hostname breaks the same-site assumption (found 19.09.2026)

The cookie/CSRF design (§ "Cookie mechanism resolved") reasons from a
same-site-but-cross-origin production topology: every `<slug>.PLATFORM_DOMAIN`
frontend and the API origin share one registrable domain, so `SameSite=Lax`
cookies flow and the CSRF token is documented defense-in-depth "for
same-site subdomains."

Local dev violates this: the frontend is `<slug>.localhost:3000`
(registrable domain `<slug>.localhost`, since `localhost` has no
public-suffix entry and each label becomes its own effective TLD), while
`NEXT_PUBLIC_API_URL` points at bare `localhost:8001` (registrable domain
`localhost`) — a genuinely different site, not just a different origin.
`SameSite=Lax` cookies (`csrftoken`, `access_token`, `refresh_token`) are
correctly refused by the browser on cross-site `fetch()`/XHR subrequests,
which blocked every real browser login attempt even after the separate
`CSRF_TRUSTED_ORIGINS` dev-origin fix (both bugs were stacked; fixing the
first only surfaced the second).

Diagnosed via direct reasoning about the site-boundary algorithm, not
observed behavior alone — confirmed the earlier curl-based CSRF
reproduction could not have caught this (curl has no `SameSite` cookie
policy at all).

**First fix attempted (19.09.2026), superseded the same day:** give the dev
API its own `*.localhost` subdomain (`api.localhost:8001`), on the
reasoning that it would share `localhost` as an effective TLD with
`<slug>.localhost` the same way production's subdomains share one
registrable domain. **This does not actually solve the problem**:
`api.localhost` and `<slug>.localhost` are still two *different* labels
under that effective TLD, so their registrable domains
(`api.localhost` vs `<slug>.localhost`) are still two different strings —
still genuinely cross-site, just with a different pair of mismatched
hostnames than before. A fixed API hostname can never be same-site with
every possible `<slug>.localhost` frontend, no matter what that fixed
hostname is.

**Actual fix:** `apiRequest` (`frontend/src/lib/api/client.ts`) no longer
reads a fixed `NEXT_PUBLIC_API_URL` origin at all. It builds its base URL
from `window.location.hostname` — the page's own, literal, current
hostname — at call time, keeping only the port configurable
(`NEXT_PUBLIC_API_PORT`). Since the registrable domain is then always
identical to the page's own (not just nominally "same effective TLD"),
this is unconditionally same-site regardless of which salon subdomain is
active, with no fixed API hostname to keep in sync with every future
tenant subdomain. `ALLOWED_HOSTS` correspondingly needed a genuine
wildcard, not one more fixed name: `DJANGO_ALLOWED_HOSTS` (repo-root
`.env`) and `development.py`'s fallback default both now include
`.localhost` (Django's leading-dot subdomain-wildcard syntax), replacing
the reverted `api.localhost` entry.

`guestClient.ts`/`createGuestBooking.ts` still read the original, unchanged
`NEXT_PUBLIC_API_URL` (a fixed origin) — they send no cookies
(`credentials` is never `"include"` in either), so they carry no ambient
credential and are not subject to `SameSite` at all; only `apiRequest`,
which does send credentialed requests, needed this fix.

**Open question this raises for production**, not resolved here: this
approach implicitly assumes the production API is reachable on the exact
same hostname as each tenant frontend (`<slug>.PLATFORM_DOMAIN`), just a
different port — not a separate host like `api.PLATFORM_DOMAIN`, which was
never ruled out before this change and is not documented anywhere as
decided. Flagged for a real decision before production deployment, not
assumed here.

### Design system (Stage 12) — explicitly deferred

Decided 11.09.2026.

Stage 12's title ("Frontend skeleton — design system, API client, auth")
included design system as a named component, but no decision body ever
scoped it — a gap discovered during Stage 12 closeout, not a silent
skip. As of this entry: session transport, CORS, CSRF, the API client
wrapper, subdomain routing, and the /me/ endpoint are decided and
implemented; a design system (color tokens, typography scale, shared
component library) is not.

Decision: defer design-system work to Stages 18–21 (frontend polish),
alongside the full apex marketing landing page (already deferred there
per the Stage 12 API-client entry). Current pages (login, admin/client
stubs) use raw HTML elements with inline Tailwind utility classes and
the unmodified create-next-app Geist/background-foreground tokens —
functional but not stylistically final. Stage 12 is considered closed
on this basis: the auth and API-client scope is complete; the
design-system third of the title is a known, named, deferred item —
not an oversight.

## Stage 13 (Frontend catalog / service / specialists / reviews)

Decided 11.09.2026 — contract agreed before any code, per the
stage-by-stage workflow.

Scope: browsing/display only — no booking flow, no appointment state.
Selecting a specialist or service here does not carry any state into
a booking process; that begins at Stage 14 (a separate, distinct
stage: "Frontend booking flow + payment + confirmation").

Routing: three separate pages, not one combined page and not a
services+specialists merge —
  - /services — service list
  - /specialists — specialist list, each showing their assigned
    services (already modeled via Specialist.services M2M)
  - /reviews — review list

Rationale: clients approach a salon two ways — "which specialist do I
want" or "which service do I want" — and each needs a different card
layout (specialist: photo/bio-first; service: price/duration-first).
Merging them into one page or one route forces a layout compromise
that serves neither entry path well. All three endpoints are already
AllowAny on GET — public browsing, no login required.

### Stage 13 amendment — service detail page (historical; removed)

Decided 11.09.2026; removed by the "Stage 13 reopened" entry below.

`/services/[id]` existed as a Stage 13 detail page (description/price
display, specialist list read from the existing `SpecialistService`
M2M) from this amendment and the "service detail page scope narrowing"
amendment that followed it, through the rest of Stage 13. It was
removed entirely, replaced by the category-navigation + service-popup
design, per "Stage 13 reopened: category navigation, service popup,
specialist detail page" below. That page's implementation-level
decisions (no description field; inline JSX specialist list rather
than a shared component) applied to code that no longer exists and are
not preserved here.

### Stage 13 amendment — specialists-by-service filter

Decided and implemented 11.09.2026.

Plan: add a `?service=<id>` query param to `SpecialistListCreateView`,
mirroring the existing `?category=` filter on `ServiceListCreateView`
(`backend/catalog/views.py`). Needed at the time for the (now-removed)
`/services/<id>` detail page ("who offers this service"); the filter
itself remains live and is reused by the Stage 14 scope revision's
booking flow ("Step 2 for `entry=service`") — without it, the only
option is fetching the full specialist list (paginated, 20/page via
`core.pagination.DefaultPagination`) and filtering client-side, which
silently breaks past page 1 for any salon with more than 20
specialists.

Rejected alternative: nesting specialists directly into
`ServiceReadSerializer`. That would couple catalog's serializer to the
specialists app and add cost to every service *list* request, not
just the detail page — the query-param filter keeps that cost scoped
to only the request that actually needs it.

`test_filter_by_service_returns_only_matching_specialists`,
`test_filter_by_nonexistent_service_returns_empty`, and
`test_no_filter_returns_all` (`backend/tests/test_specialist_api.py`)
pass.

### Stage 13 amendment — /specialists page: serializer and card scope

Decided 11.09.2026.

1. `SpecialistReadSerializer` gains three additions:
   - `photo` (existing model field, was not exposed until now)
   - `services` changes from a bare list of ids to nested `{id, name}`
     objects (`name` resolved via `?lang=`, matching
     `ServiceCategoryMiniSerializer`'s pattern), to show service names
     on the card without a second fetch per specialist
   - `average_rating` and `review_count`, computed via queryset-level
     `annotate()` (`Avg`/`Count` over the reverse `reviews` relation)
     — never via a per-object `SerializerMethodField`, to avoid N+1
     across a paginated list of specialists

2. `services` must be loaded via `prefetch_related`, kept separate from
   the `annotate()` on `reviews` — combining an annotate and a
   select_related/join on two different reverse relations in one
   queryset risks row multiplication before aggregation, silently
   inflating `average_rating`/`review_count`.

3. A specialist with zero reviews: `average_rating` is null (not 0) at
   the API level; the frontend renders this as 0 stars plus a "Немає
   відгуків" label, never hidden from the list (unlike the `/reviews`
   endpoint, which omits zero-review specialists from its grouping —
   that's a different, deliberate behavior for a different page).

4. Specialist detail page (`/specialists/[id]`) is out of scope for
   this amendment — this amendment only covers the `/specialists` list
   card. It was later built, not as a browsing-only page but as a
   booking entry point — see "Stage 14 scope revision: booking flow
   entry points and specialist assignment" below.

### Stage 13 amendment — /reviews page: flat feed, not grouped-by-specialist

Decided 11.09.2026.

1. Unlike the existing `GET /api/v1/salons/<slug>/reviews/` endpoint
   (which groups reviews by specialist, ordered by review count desc),
   the `/reviews` page itself displays a flat chronological feed —
   every review as an individual item, sorted by `created_at`
   descending across all specialists, not grouped into per-specialist
   sections. This is a frontend-only presentation choice; the backend
   grouping behavior is unchanged and still used by `/specialists`'
   rating aggregation.

2. `ReviewPublicSerializer` gains a `service` field: `{id, name}`
   (`name` resolved via `?lang=`, same pattern as
   `ReviewSpecialistSerializer.get_name`), sourced via
   `review.appointment.service` — a plain FK chain, added to the
   existing `select_related("specialist")` as
   `select_related("specialist", "appointment__service")`. No N+1
   risk: single-valued forward relations only, one query total.

3. Each review card on `/reviews` shows: rating, text, created_at,
   specialist name, and service name — so a reader understands both
   who performed the service and what the review is about.

4. This flat-feed page is unrelated to (and does not replace) any
   review list on `/specialists/[id]` — that page was later built as a
   booking entry point (photo, name, bio, rating, "Book" button) per
   the Stage 14 scope revision entry, not as a browsing page with its
   own review list.

### Stage 13 status: closed

Decided 11.09.2026.

All three catalog pages shipped: `/services` list + `/services/[id]`
detail, `/specialists` list, and `/reviews` flat feed. Each follows the
same thin Server Component + tested pure fetch-helper pattern
(`getServicesPage`/`getServiceDetailPage`/`getSpecialistsPage`/
`getReviews`). Scope held throughout: browsing/display only, no
booking-state carryover — consistent with how Stage 13 was scoped at
the start.

Explicit deferrals coming out of this stage:

- `/services/[id]` has no description field — the `Service` model has
  none; adding one is deferred until a concrete need arises.
- `/specialists` cards are inline JSX, not a reusable `SpecialistCard`
  component — extraction deferred until a second caller with known
  requirements exists.
- `/specialists/[id]` was out of scope — it bridges into booking, and
  Stage 13 was browsing-only. It was later built as a booking entry
  point per the Stage 14 scope revision entry, not left deferred.
- `/reviews` has no server-side pagination — client-side "показати ще"
  only, revealing more of the already-fetched array. Revisit if a
  salon's review volume grows enough to make one full fetch costly.

## Stage 14 (frontend booking flow + payment + confirmation) — scope

Decided 11.09.2026 — contract agreed before any code, per the
stage-by-stage workflow.

- **Stage 14 is scoped guest-only.** It builds on the existing
  guest-token-based backend endpoints as-is: `bookings/`,
  `guest/appointments/<id>/pay/`, `guest/appointments/<id>/cancel/`
  (`backend/booking/urls.py`, `backend/payments/urls.py`). No new
  backend auth path is added for this stage.
- **Logged-in Account booking is explicitly deferred to Stage 15**
  ("Frontend customer account + guest token management") — an
  Account-aware booking path, frontend auth context, and "my bookings"
  in a client dashboard all land there. Stage 15 already owns the
  client dashboard per the existing stage list (`/client/page.tsx` is
  currently only a Stage 12 placeholder proving the post-login
  redirect).
- **Rationale:** no stage-scoping decision existed prior to this entry.
  The backend's guest/Account-agnostic booking path (`Appointment`
  always references `Customer`, never `Account` — see § "Identity:
  Customer vs. Account") is an implementation detail of the service
  layer, not a frontend scope decision — it does not by itself commit
  the Stage 14 frontend to building an Account-aware flow. Splitting
  here avoids duplicating booking-flow logic between Stage 14 and
  Stage 15: Stage 14 ships the guest flow once; Stage 15 adds the
  Account-aware path on top once auth context exists on the frontend.

### Stage 14 planning: booking flow, payment link, confirmation email

Decided 12.09.2026 — contract agreed before any code, per the
stage-by-stage workflow.

- **Three separate notification triggers span the booking lifecycle —
  not one trigger reused across it.** An earlier draft of this entry had
  `BOOKING_CONFIRMED` firing immediately on appointment creation; that
  was wrong. `BOOKING_CONFIRMED` semantically means "payment received"
  (per the `Appointment.status` model: `CONFIRMED` only ever follows
  payment) and stays dispatched only from `payments/views.py`
  (`PaymentWebhookView`) on a successful payment webhook, unchanged from
  its existing behavior.
  - **`BOOKING_CREATED` (new)** — dispatched immediately from
    `GuestBookingCreateView` on appointment creation, unconditional on
    payment status. Message: booking created, pay within the hold
    window, includes the fragment-based payment link (see the payment
    page link bullet below).
  - **`BOOKING_EXPIRED` (new)** — dispatched from
    `booking.services.expire_overdue_appointments` immediately after it
    transitions an appointment's status to `EXPIRED` (called per-salon
    by the `expire_pending_payment_appointments` Celery sweep task, §
    Stage 7.F). Message: the hold window passed, the booking was
    released, invites the customer to book again.
  - **`BOOKING_CONFIRMED` (existing, unchanged)** — dispatched from
    `payments/views.py` on a successful payment webhook. Message:
    payment received, appointment confirmed.

  Both new triggers follow the existing generic pattern in
  `notifications/services.py` (a `_MESSAGES` entry plus a
  `record_and_dispatch_notification` call at the relevant call site) —
  no new Celery task and no new send mechanism, same as every existing
  trigger.
- **The payment page link uses a URL fragment, not a query parameter:**
  `build_salon_frontend_url(salon_slug, "/booking/pay") +
  f"#appointment_id={id}&token={token}"`. Rationale: fragments are
  never sent to the server (no server logs, no Referer leakage),
  unlike query params. This mirrors the existing password-reset and
  email-verification link pattern in `backend/accounts/tasks.py`
  (`#uid={uid}&token={token}` / `#token={token}`). Consequence: the
  frontend `/booking/pay` page must be a Client Component, since the
  fragment is only readable via `window.location.hash`, which is
  unavailable server-side.
- **The booking flow supports two entry orders:** "service-first" (a
  service is already chosen before `/booking` opens; the flow then
  proceeds to choose a specialist, then date/time) and
  "specialist-first" (a specialist is already chosen before `/booking`
  opens; the flow then proceeds to choose a service, then date/time).
  Both orders converge on the same underlying slot-computation logic,
  mirroring the existing dual-entry pattern for
  `compute_candidate_start_times` from Stage 6.
- **Client-side flow state is split by sensitivity.** Selections made
  before contact info — service, specialist, slot, and the current
  step number — are held in URL query params, so the flow survives a
  page refresh and a specific step can be shared or returned to.
  Contact info (name, email, phone — corrected 15.09.2026; this entry
  originally named only "name, phone" and omitted email despite the
  serializer requiring all three, see "Stage 14 implementation
  decisions: step 4 contact-info form" below) is held only in local
  component state (React `useState`), never persisted to the URL,
  storage, or elsewhere — deliberately, since it is personal data and
  the earlier steps' choices are not. The "local component state
  (React `useState`)" mechanism named here is itself superseded by the
  entry below (a React Context in a new client `layout.tsx`), for
  reasons recorded there — this entry's field list is corrected in
  place, but its state-container detail is now stale and should be
  read alongside that entry.
- **Slot conflict handling:** if the backend rejects appointment
  creation because the exclusion constraint caught a concurrent
  booking of the same slot, the frontend returns the user to the
  date/time selection step. Contact info already entered (name, email,
  phone — corrected 15.09.2026, same reason as above) is preserved in
  local component state rather than cleared — a slot conflict is
  unrelated to the privacy rationale for keeping contact info out of
  the URL, so there is no reason to discard it. This behavior itself is
  unchanged by the entry below; only its technical mechanism is
  revised there.
- **`/booking/pay` must branch on appointment status, not just
  guest-token validity.** `HasValidGuestToken` only confirms the token
  itself hasn't expired or been tampered with — that is orthogonal to
  what state the appointment is actually in. The page must check
  `Appointment.status` and render accordingly:
  - `PENDING_PAYMENT` → render the payment form as normal.
  - `CONFIRMED` → render an "already paid" state; do **not** render the
    payment form (prevents double payment).
  - `EXPIRED` or `CANCELLED` → render a "no longer available, please
    book again" state.
- **The guest booking creation response (`POST bookings/`) includes the
  raw guest token in the JSON body**, in addition to the token being
  sent via the `BOOKING_CREATED` email link. This lets the frontend
  redirect the guest directly to `/booking/pay` right after submission,
  without waiting on email delivery. The email-delivered link remains
  the recovery path for a closed or lost session within the hold
  window.

### Stage 13 reopened: category navigation, service popup, specialist detail page

Decided 13.09.2026 — discovered during Stage 14 planning, not agreed in
advance of any code. Stage 13 was previously closed (see "Stage 13
status: closed" above) and is reopened by this entry because Stage
14's booking-flow entry points depend on catalog changes Stage 13 did
not anticipate.

- New field `Service.description`: text, nullable/blank — optional;
  neither existing nor new services are required to have one.
  Supersedes the Stage 13 status entry's explicit deferral
  ("`/services/[id]` has no description field ... deferred until a
  concrete need arises") — a concrete need arose in Stage 14 planning:
  the service info popup below needs it.
- New field `ServiceCategory.photo`: mirrors `Specialist.photo`
  (`backend/specialists/models.py`) — a URL-based field, no new
  file-storage/upload infrastructure.
- `/services` becomes two-level: a category list (each card showing
  `ServiceCategory.photo`) → selecting a category shows the services
  within it, reusing the existing `?category=` filter on
  `GET /services/` (`backend/catalog/views.py`, from Stage 4). This
  replaces the previous flat `/services` list.
- Each service card in the category view has two independent controls:
  - clicking the service itself selects it and proceeds directly into
    the booking flow, at `/booking?entry=service&service=<id>&step=2`;
  - a separate "i" (info) button opens a popup — a new frontend
    component, since no modal/popup component exists anywhere in the
    codebase yet — showing the service's name, duration, price, and
    description. It does not show which specialists offer the
    service; that responsibility moves to the booking flow's own step
    2 (see the Stage 14 scope revision entry below).
- `/services/[id]` (the Stage 13 detail page) is removed entirely.
  Confirmed by recon before this entry: the flat `/services` list page
  was its only inbound link anywhere in the frontend; no other page,
  including `/specialists`, ever linked to it.

Rationale: none of this was foreseeable when Stage 13 closed — the
popup and category-navigation requirements only surfaced once Stage 14
planning worked out how a customer actually arrives at the booking
flow. This is recorded as reopening the closed stage, not as a new
Stage 13 amendment appended after "status: closed," so a future reader
isn't misled into thinking Stage 13's closure held all along.

### Stage 13 reopened: implementation decisions for the popup, category fetch, and detail-page removal

Decided 13.09.2026 — follow-on to the "Stage 13 reopened: category
navigation, service popup, specialist detail page" entry above, made
during read-only recon of the current frontend before writing any
code. Decided only; none of this is implemented yet.

- The service-info popup uses the native HTML `<dialog>` element — no
  modal/dialog library dependency is added, since none currently
  exists in `frontend/package.json`.
- The popup is a separate, small Client Component (e.g.
  `ServiceInfoPopover`), receiving service data as props. The
  `/services` page itself remains a Server Component; `"use client"`
  is not added to the page.
- The `ServiceCategory` list is fetched with `?page_size=100`
  (`core.pagination.DefaultPagination.max_page_size`) as a single
  request — the backend has no unpaginated mode, so the response is
  still the standard `DrfPage` envelope (`{count, next, previous,
  results}`), just requested at its maximum page size instead of
  looped like `Service` pagination (`getServicesPage.ts`'s
  `PAGE_SIZE`/`totalPages` handling). If the envelope's `count`
  exceeds `results.length` (i.e. more categories exist than this one
  page can hold), the helper throws rather than silently returning a
  truncated category list — the same "raise on incomplete data, never
  silently return partial results" principle as
  `TenantScopedManager`.
- Category selection on `/services` uses URL navigation
  (`?category=<id>`), not client-side state — the page stays a Server
  Component, mirroring the existing `?page=` pagination pattern
  (`services/page.tsx`). No new client-side fetch mechanism is
  introduced. Chosen over a client-side toggle because it requires no
  new `"use client"` fetch infrastructure, keeps back-button behavior
  native, and makes category links shareable/bookmarkable.
- Removing `/services/[id]` means deleting:
  `frontend/src/app/services/[id]/page.tsx` and its `not-found.tsx`,
  `frontend/src/lib/catalog/getServiceDetailPage.ts` and its test
  file, and updating the now-stale comment at
  `frontend/src/app/booking/page.test.tsx:6`, which currently cites
  `services/[id]/page.tsx`'s `notFound()` usage as the reason its own
  `notFound` mock throws.
- The service card's click target navigates straight to
  `/booking?entry=service&service=<id>` (confirmed against
  `booking/page.tsx`'s `entry`/`service` param handling) — clicking the
  card books the service directly, rather than opening the popup. A
  separate "i" (info) button opens `ServiceInfoPopover` instead.
- Card HTML structure: the card is a relatively-positioned container;
  the `Link` inside it uses the "stretched link" pattern (`absolute
  inset-0`, covering the full card) for its click target, rather than
  wrapping the whole card in an `<a>`. The "i" button is a sibling of
  the `Link` (not nested inside it), given a higher `z-index` so it
  remains clickable above the stretched link. An interactive `<button>`
  nested inside an `<a>` is invalid HTML and produces unpredictable
  click behavior, which is why the stretched-link pattern is used
  instead of the naive "wrap everything in one link" approach.
- `getServicesPage.ts`'s `Service` interface gets `description: string
  | null` added. The field has been present in the API response
  (`ServiceReadSerializer.Meta.fields`) since the `d1cd709 feat: add
  Service.description and ServiceCategory.photo fields` migration; it
  was simply never added to this frontend type, confirmed by re-
  checking the serializer's field list during recon.
- `ServiceInfoPopover` shows: `name`, `description`, `duration_minutes`,
  `price`, `category.name`. `buffer_minutes`, `ordering`, `is_active`,
  and the timestamps are operational fields, not shown.

### Stage 14 scope revision: booking flow entry points and specialist assignment

Decided 13.09.2026 — supersedes the entry-point assumptions in "Stage
14 planning: booking flow, payment link, confirmation email" above.
The core step order (service/specialist choice → date/time → contact
info → submit) is unchanged; what changes is how the flow is entered
and how "any specialist" is resolved.

- New page `/specialists/[id]`: a specialist detail card (photo, name,
  bio, rating) with a "Book" button leading to
  `/booking?entry=specialist&specialist=<id>&step=2`. This did not
  exist before — the Stage 13 amendment "/specialists page: serializer
  and card scope" (point 4) had explicitly deferred it as "out of
  scope ... bridges into booking"; it is now in scope because Stage 14
  needs it as an entry point.
- `/booking`'s "step 1" — an unqualified starting point with nothing
  pre-selected — is removed for both entry orders described in the
  Stage 14 planning entry above ("service-first" / "specialist-first").
  In practice every real entry into `/booking` already carries one
  identifying parameter from wherever the customer came from (the
  category-navigated services catalog, or `/specialists/[id]`); there
  is no product surface that opens `/booking` with nothing pre-chosen.
  The flow now always starts at what the earlier entry called "step 2."
- Step 2 for `entry=service`: shows specialists who perform the chosen
  service, reusing the existing `?service=` filter on
  `GET /specialists/` (`backend/specialists/views.py`, added in the
  Stage 13 "specialists-by-service filter" amendment), plus an "any
  specialist" option.
- Step 2 for `entry=specialist`: shows the services offered by the
  chosen specialist as a flat list — no category navigation, since
  category navigation is specific to the catalog-browsing entry point
  (Stage 13 reopened entry above), not to this narrower context, which
  is already scoped to one specialist.
- "Any specialist": choosing it does not narrow date/time availability
  to one specialist. The date/time step computes availability as the
  union across all qualifying specialists, reusing the existing
  `compute_multi_specialist_availability`
  (`backend/scheduling/services.py`, Stage 6.H) unchanged. The actual
  specialist assignment happens later, only at the
  booking-confirmation/review step immediately before payment — the
  first point the customer learns which specialist was assigned.
  Assignment rule (new logic — no "least busy" or auto-assignment
  function existed anywhere before this, confirmed by recon): among
  specialists qualified for the service, working that day, and
  available at the chosen date/time, prefer whichever has fewer
  appointments already booked that day.
  - Clarification (15.09.2026), tie-break: when two or more candidates
    have an equal count of same-day appointments, the tie is broken
    randomly among them. This same ordering (sorted by busyness
    ascending, randomized within tie groups) also defines the retry
    sequence used when the first-choice specialist's slot creation
    fails due to a concurrent booking conflict (per the
    `create_appointment` retry-on-`ExclusionViolation` approach already
    discussed for this feature) — not a separate re-roll each time, one
    ordering computed once and walked in sequence. Testability: the
    randomization must be injectable/mockable (e.g. accept an optional
    `random.Random` instance or equivalent seam), so tests asserting
    tie-break behavior are deterministic and not flaky.

Rationale: the original Stage 14 planning entry assumed a bare
`/booking` starting point and left "any specialist" unresolved past
availability computation; both gaps were only surfaced by working
through concrete entry points during Stage 14 planning, not decided or
reviewed in advance of this entry — recorded here as a revision for
that reason, not folded silently into the earlier entry.

### Stage 14 implementation decisions: "any specialist" encoding, specialist-services shape, specialist filter/detail page

Decided 14.09.2026 — agreed before any code, per the stage-by-stage
workflow, during read-only recon ahead of building step 2+ of the
booking flow.

- **"Any specialist" is encoded as the literal URL value
  `specialist=any`**, not an empty/omitted param. Rationale:
  `booking/page.tsx`'s existing step-3 guard already requires
  `specialist` to be truthy for that step to render at all; `any` is
  short and unambiguous as a URL value alongside real numeric ids.
- **Step 2 for `entry=specialist` (the flat list of that specialist's
  services) needs `name` + `duration_minutes` + `price`**, not just
  `id`/`name`. This will **not** be added to `ServiceMiniSerializer`
  (`specialists/serializers.py`) — it is deliberately minimal
  (id/name only), guarded by
  `test_service_mini_serializer_excludes_new_fields` and
  `test_service_category_mini_serializer_excludes_new_fields` from the
  Stage 13 reopened work; extending its field list would weaken/
  contradict those tests. Instead, a new dedicated serializer will be
  added for this endpoint's needs, sourced from the same prefetched
  `Specialist.services` relation `SpecialistReadSerializer` already
  uses (`specialists/views.py`'s `_with_card_annotations`) — no new
  query pattern, just a different output shape over the same data.
- **`GET /specialists/` already supports `?service=<id>`**, confirmed
  by recon: it landed ahead of schedule in the Stage 13
  "specialists-by-service filter" amendment
  (`specialists/views.py`'s `SpecialistListCreateView.get_queryset`),
  before Stage 14 planning even asked for it. `getSpecialistsPage.ts`
  needs the same optional-param extension `getServicesPage.ts` already
  got for `category` — including switching its current manual ternary
  URL-building (`page === 1 ? ... : ...?page=...`) to the
  `URLSearchParams` approach `getServicesPage.ts` uses, for consistency
  between the two fetch helpers.
- **`/specialists/[id]` (detail page, "Book" button →
  `/booking?entry=specialist&specialist=<id>&step=2`) doesn't exist
  yet** and is the next concrete piece to build. Confirmed by recon:
  its data already exists via the existing `SpecialistDetailView`
  (`GET /specialists/<id>/`) — no new backend endpoint is needed for
  the detail page itself.
- **The new services-with-pricing serializer (the open question left
  by the previous bullet) is wired in as a new field on
  `SpecialistReadSerializer`, not a new endpoint.** It's added
  alongside the existing `services` field (still `ServiceMiniSerializer`-
  backed, completely untouched) on the serializer already returned by
  `GET /specialists/<id>/` (`SpecialistDetailView`). Rejected
  alternative: a separate endpoint — `SpecialistDetailView` and its
  frontend fetch helper (`getSpecialistDetailPage.ts`) already exist
  and are tested, and the only consumer of this shape is booking step
  2 for `entry=specialist`, so a dedicated endpoint would duplicate
  that surface for a single caller. The new field's name is not final
  — to be decided in the implementation step — but it must read
  clearly as the fuller shape (e.g. `services_detail` or similar)
  rather than overloading `services` itself.

### Stage 14 implementation decisions: `services_detail` gains a `description` field

Revised 14.09.2026, after review — revising the services-with-pricing
shape scoped in the "Stage 14 implementation decisions" entry above,
which specified `name` + `duration_minutes` + `price` only, with no
`description`.

- **`services_detail` gains a `description` field.** Rationale: the "i"
  info popover (`ServiceInfoPopover`) must show the same description on
  booking step 2 (`entry=specialist`) as it does on `/services` — the
  customer sees identical information regardless of which entry path
  they took into booking.
- `ServiceWithPricingSerializer` (`specialists/serializers.py`) needs a
  `description` field added, sourced the same way
  `ServiceReadSerializer`'s `description` already is
  (`catalog/serializers.py`): a plain passthrough `Meta.fields` entry,
  not a `SerializerMethodField`/`resolve_translation` call —
  `Service.description` is a plain string field, not a translatable
  `{lang: str}` dict like `name`.
- **`ServiceSelectionGrid.tsx`'s generalization is unchanged by this
  decision.** It is still being generalized, not duplicated, for reuse
  between `/services` and booking step 2: its hardcoded
  `router.push("/booking?entry=service&service=...")` becomes an
  `onConfirm(selectedId)` callback prop supplied by the caller. The
  navigation target still legitimately differs between the two callers
  (`/booking?entry=service&service=<id>` vs.
  `/booking?entry=specialist&specialist=<id>&service=<id>&step=3`) even
  though both callers' service shape now includes `description`.

### Service selection: select-then-confirm interaction pattern

Decided 14.09.2026 — agreed before any code, per the stage-by-stage
workflow, during read-only recon ahead of building booking step 2 for
`entry=specialist`.

- Service selection (the "final choice before proceeding to booking"
  step) uses a select-then-confirm pattern: cards are selectable
  (visually indicate selection, e.g. a highlighted border or radio
  input for accessibility) but do **not** navigate on click. A "Далі"
  button, disabled until a selection is made, is the only way to
  proceed.
- Applies to: (a) the service-selection grid on `/services` (within a
  category — **not** the category-selection grid itself, which stays
  instant-navigate/unchanged, since browsing categories is not a final
  choice), and (b) the new booking step 2 for `entry=specialist`
  (choosing which of a specialist's services to book).
- Rationale: gives the customer a chance to reconsider before
  committing to navigate away, rather than an irreversible single
  click. Applies uniformly across the app wherever a "final selection
  before proceeding" pattern occurs.
- **Supersedes** the service-card click-target decision recorded in
  "Stage 13 reopened: implementation decisions for the popup, category
  fetch, and detail-page removal" (13.09.2026): that entry's
  stretched-link-to-`/booking` pattern is retired for the
  service-selection grid. That entry's HTML-structure guidance (the
  "i" info button as a sibling of the click target, not nested inside
  it) still applies conceptually, but the card is no longer a `Link`
  at all under this new pattern.
- `ServiceInfoPopover` (the "i" info popover) is unaffected — it
  remains a separate, non-selection interaction.

### Deferred: frontend price display shows no currency unit

Noted 14.09.2026, during recon after the select-then-confirm retrofit
above. Not fixed now — recorded as a known, deliberately deferred gap
rather than folded in as a quick fix at the end of an unrelated
session.

- Every price shown in the frontend today (`ServiceSelectionGrid`,
  used by `services/page.tsx`; `ServiceInfoPopover.tsx`) renders the
  raw `Service.price` string with no currency unit at all — e.g.
  `500.00`, not `500.00 UAH` or `$500.00`. This is a **pre-existing
  gap, not a regression from today's select-then-confirm work** — the
  stretched-link card it replaced had exactly the same unadorned
  `{service.price}` display.
- **Not a hardcoded-symbol fix.** Confirmed via the live BellaBeauty
  salon record: its `Salon.currency` is set to `USD`, not the
  product-default `UAH` (§ Currency above) — different salons use
  different ISO 4217 currencies, so any fix must read each salon's
  actual `currency` value, never assume one symbol platform-wide.
- Fixing this properly requires three pieces, none of which exist yet:
  1. **A new Salon-info API endpoint.** Confirmed by recon: the
     `tenants` app currently has zero DRF surface — no
     `serializers.py`, `views.py`, or `urls.py`, only `models.py`,
     `middleware.py`, `admin.py`. `Salon.currency` is backend-internal
     only today, read solely by `payments` to freeze onto
     `Payment.currency` at creation (`payments/models.py`,
     `payments/services.py`) — never serialized out to any client.
  2. **A frontend fetch for that endpoint** — nothing in
     `frontend/src` currently fetches salon-level data at all; the
     frontend only ever learns the salon's `slug` (via
     `middleware.ts`'s `SALON_SLUG_HEADER` /
     `resolveSlugFromHost`), never anything else about the salon.
  3. **A currency-aware price-formatting utility** (e.g. wrapping
     `Intl.NumberFormat` with the fetched ISO 4217 code), not a
     hardcoded `₴`/`$` suffix — no such utility exists yet.

### Resolution: frontend price display shows no currency unit

Decided 18.09.2026, resolving the 14.09.2026 deferred gap directly above.
Three parts, agreed before any code, per the stage-by-stage workflow. Scoped
deliberately narrower than the three-piece fix originally sketched above —
see "Out of scope" below.

- **(A) `/booking/pay`: render the payment amount already returned by the
  API.** `PaymentStatus.tsx`'s `PayResponse` type is extended to include
  `payment: { amount: string; currency: string }` alongside the existing
  `provider_data`, and the component renders it. This data is not new —
  `GuestAppointmentPayView` (`booking/views.py`) already returns it via
  `PaymentGuestSerializer` (`payments/serializers.py`), which has exposed
  `amount`/`currency` on `Payment` since Stage 8; it was simply never typed
  or rendered on the frontend. **No backend change** — pure frontend fix.
- **(B) A new read-only Salon-info endpoint**, closing recon item 1 from the
  deferred entry above: `GET /api/v1/salons/<slug>/` returns
  `{"currency": "<ISO 4217>"}`, `AllowAny` (a public read, same read-access
  posture as `catalog`'s list/detail endpoints per docs/DECISIONS.md § Stage
  4 decisions "Catalog read semantics"). New `tenants/serializers.py`,
  `tenants/views.py`, `tenants/urls.py`, following the existing per-salon
  read-only pattern (`catalog` app: `ReadSerializer` +
  DRF generic view + relative sub-path `urls.py` registered via
  `include()` in `config/urls.py` under the shared
  `api/v1/salons/<slug:slug>/` prefix). Registered alongside
  `catalog.urls`/`booking.urls`/etc.; the bare
  `api/v1/salons/<slug:slug>/` path is currently unclaimed by any existing
  app (confirmed by recon — no other app's `urls.py` defines an empty `""`
  route), so `tenants.urls` claims it with no conflict. Feeds `/services`,
  `/specialists`, and booking steps 2–4, where no `Payment` row exists yet
  (a `Payment` is only created inside `initiate_payment` after a successful
  provider call, never at booking time) and `Service` carries no `currency`
  field of its own — this endpoint is the only source of a currency to pair
  with the `Service.price` strings already rendered on those pages.
- **(C) `/services` and booking step 2's `entry=specialist` path fetch
  endpoint (B) and render price + currency together** — recon found
  exactly **two** real JSX call sites for `ServiceSelectionGrid`, not
  three: `services/page.tsx`, and `booking/page.tsx`'s step-2
  `entry=specialist` branch. Step 2 of the `entry=service` path renders
  `SpecialistSelectionGrid` instead — a different component (choosing a
  specialist *after* the service was already picked on `/services`),
  which shows no price and needs no currency prop. Not `/specialists`
  and not booking steps 3–4 either, which render no price at all. Closes
  recon items 2–3 from the deferred entry above: a shared formatting
  utility (e.g. `formatPrice(price, currency)`, wrapping
  `Intl.NumberFormat` with the fetched ISO 4217 code, not a hardcoded
  `₴`/`$` suffix — per the original deferred entry's "not a
  hardcoded-symbol fix" note) replaces the current bare `{service.price}`
  rendering in `ServiceSelectionGrid.tsx` and `ServiceInfoPopover.tsx`
  (`ServiceInfoPopover` is only ever rendered from inside
  `ServiceSelectionGrid`, never directly by any page). **(C) is required
  to actually close the original gap** — (B) alone adds a backend
  endpoint nothing yet calls, so on its own it changes nothing visible to
  users.
  - **Prop threading**: `currency` is passed as a new sibling prop
    (`currency: string`) on `ServiceSelectionGridProps` and
    `ServiceInfoPopoverProps`, **not** embedded into the `Service`
    type/objects those components already take. `currency` is a
    salon-level constant — one per response, not one per service — and
    the backend `Service` model has no `currency` field of its own to
    begin with (docs/DECISIONS.md § Currency: it lives only on `Salon`
    and gets frozen onto `Payment` at creation, never onto `Service`);
    embedding it per-item would be redundant and would imply a per-item
    variability that doesn't exist.
  - **`formatPrice` utility**: `frontend/src/lib/pricing/formatPrice.ts`
    — matching the established per-domain-subfolder convention recon
    confirmed (`lib/routing/`, `lib/scheduling/`, `lib/catalog/`); a flat
    `lib/formatPrice.ts` would have been an oversight, not a deliberate
    deviation. A pure function `(price: string, currencyCode: string) =>
    string`, following this codebase's existing
    pure-function-extraction-for-testability pattern
    (`lib/scheduling/groupAvailabilityByDay.ts`,
    `lib/routing/resolveSlugFromHost.ts`): no React, no fetch, trivially
    unit-testable on its own, called from both `ServiceSelectionGrid.tsx`
    and `ServiceInfoPopover.tsx`.
  - **Locale**: `formatPrice` hardcodes the `'uk-UA'` locale for
    `Intl.NumberFormat`, not the visitor's browser locale — matches the
    current all-Ukrainian UI chrome (every other user-facing string in
    this frontend today, e.g. `PaymentStatus.tsx`'s messages, is
    Ukrainian, not locale-negotiated). Revisit only alongside the Stage
    18–21 UI-i18n work, not before.
  - **Data-fetching approach, confirmed by recon**: there is no shared
    layout to fetch currency once and pass it down — the frontend's
    routing is subdomain-based (`docs/DECISIONS.md` § "Frontend routing:
    subdomain-based"), with no `[slug]` route tier at all; the only two
    `layout.tsx` files in `frontend/src/app` are the root layout (fonts/
    metadata only) and `booking/layout.tsx` (a client-side context
    provider wrapper, explicitly not touching `page.tsx`'s own
    server-side data fetching). Every salon-scoped page today
    independently reads the resolved slug off the `x-salon-slug` request
    header (`middleware.ts`'s `SALON_SLUG_HEADER`) and calls its own
    `lib/*` fetch helper — there is no existing shared-fetch mechanism to
    extend.
  - Each of the two real call sites (`/services` `page.tsx`; booking
    step 2's `entry=specialist` branch — the only sites that actually
    render a price, per recon) independently calls a new
    `getSalonInfoPage(slug)` helper
    (`frontend/src/lib/tenants/getSalonInfoPage.ts`), mirroring
    `getServicesPage.ts`'s conventions exactly (plain async function
    taking `slug`, `fetch` against `${INTERNAL_API_URL}/api/v1/salons/
    ${slug}/`, throw a generic `Error` on a non-ok response).
  - **Run in parallel via `Promise.all`** alongside each call site's
    existing services/specialist-detail fetch, not sequentially — avoids
    adding a second round-trip on top of the existing one.
  - **Deliberately not** middleware header injection (wrong layer — would
    add a network fetch to *every* request matched by `middleware.ts`,
    including every request that never renders a price) **and
    deliberately not** a request-scoped cache/dedup layer for the
    now-duplicated per-call-site fetch (premature — one salon exists in
    the DB today, this is a single-row lookup; revisit if this becomes a
    real, measured cost).

**Out of scope:**

- **Showing a payment amount to the guest *before* they click
  "Оплатити".** Unrelated to (A)/(B)/(C) above: those close the
  *no-currency-unit* gap on already-rendered prices; this is a *no-amount-
  shown-at-all* gap on a screen that today renders no price. No backing
  `Payment` row exists pre-pay — a `Payment` is only created inside
  `initiate_payment` after a successful provider call, never at booking
  time — so this would need either new fields on `AppointmentGuestSerializer`
  or a duplicated deposit-computation on the frontend. A separate future UX
  decision, deliberately not investigated or folded into this fix.

### Fix: TenantContextMissingError on admin save for TenantScopedModel

Decided and implemented 11.09.2026.

_TenantBoundModelForm (previously local to accounts/admin.py, solving
this exact issue for Account only) is promoted to core/admin.py and
wired as SalonScopedAdmin's default form, so every TenantScopedModel
admin (ServiceCategory, Service, Specialist, and any future one)
inherits the fix automatically instead of needing its own copy.
AccountAdmin's local copy is removed in favor of the shared one.

Discovered while manually seeding dev data through /admin/ — a real
gap, not present in existing tests because nothing previously
exercised admin-side creation of these models outside Account.

Specialist admin creation is now fully fixed and tested
(test_specialist_admin_add_post_succeeds passes).

ServiceCategory and Service admin creation are still broken, but for
an unrelated, pre-existing reason: their per-language UniqueConstraint
uses NullIf(KeyTextTransform(...), Value("")) with no output_field,
which Django's Python-side UniqueConstraint.validate() cannot resolve
a type for (Postgres itself handles it fine at the DB level — this
only affects the ORM's pre-check, reached via Model.full_clean(), a
path nothing previously exercised for these two models). Tracked
separately, not fixed in this change per CLAUDE.md's Meta.constraints
decision-point rule.

### Fix: separate server-side API URL for Server Components

Decided and implemented 11.09.2026.

`NEXT_PUBLIC_API_URL` is browser-scoped by design (Next.js inlines
`NEXT_PUBLIC_*` vars into client bundles) and set to the host-published
port (`http://localhost:8001` dev) — correct for browser code
(`api/client.ts`) but unreachable from server-side code running inside
the `frontend` Docker container, where "localhost" means the frontend
container itself. Discovered as `fetch failed, ECONNREFUSED
127.0.0.1:8001` from `getServicesPage.ts`, a Server Component data-fetch
helper.

Fix: added `INTERNAL_API_URL` (no `NEXT_PUBLIC_` prefix — server-only,
never inlined into the client bundle), set to `http://backend:8000` —
Compose's internal service-name DNS, resolvable container-to-container
on the shared `salonhub_default` network regardless of host port
mappings — to `frontend/.env` (documented in `frontend/.env.example`
alongside `NEXT_PUBLIC_API_URL`, with a comment on the
browser/server/Docker-network distinction; no `docker-compose.yml`
change, since the `frontend` service has no Compose-level
`environment:`/`env_file:` block and already relies on Next.js loading
`frontend/.env` from the bind-mounted volume). `getServicesPage.ts` now
reads `process.env.INTERNAL_API_URL` instead of `NEXT_PUBLIC_API_URL`.
`getServicesPage.test.ts` updated to stub `INTERNAL_API_URL`, value
changed to `http://backend:8000` — no assertion behavior changed, only
the env var name/value being stubbed. `api/client.ts` is unaffected —
it stays on `NEXT_PUBLIC_API_URL`, since it only ever runs in the
browser.

Frontend test suite: 21/21 passing (3 files) after the change.

### Stage 14 UI decisions: booking step 2 for `entry=service` (specialist selection)

Decided 14.09.2026 — agreed before any code, per the stage-by-stage
workflow, during read-only recon ahead of building step 2 for
`entry=service`.

- **`SpecialistCard` is extracted as a shared presentational component**
  (photo/initials fallback + rating line), currently duplicated between
  `frontend/src/app/specialists/page.tsx` and
  `frontend/src/app/specialists/[id]/page.tsx`. This booking step is the
  third caller of that visual pattern, so the duplication is extracted
  now per the project's existing "extract on second/third caller"
  approach — extraction of this exact component was previously deferred
  in the Stage 13 entry "`/specialists` cards are inline JSX, not a
  reusable `SpecialistCard` component — extraction deferred until a
  second caller with known requirements exists," and this booking step
  is that caller (in fact the third, counting the detail page).
- **A new component, not an extension of `ServiceSelectionGrid.tsx`,**
  renders the list of real specialists returned by
  `GET /specialists/?service=<id>`, built from the shared `SpecialistCard`
  and reusing the existing select-then-confirm interaction pattern (card
  selection + a "Продовжити" button disabled until something is
  selected). Rationale: specialist cards and service cards have
  materially different data shapes (photo/rating/bio vs.
  duration/price/description), so generalizing `ServiceSelectionGrid`
  itself would mean branching its rendering on shape rather than reusing
  it cleanly.
- **"Any specialist" is not a synthetic card inside this grid.** It is
  rendered as a separate selectable element positioned above the grid,
  labeled "Будь-який спеціаліст".
- **Selecting "any specialist" and selecting a specific specialist card
  are mutually exclusive:** choosing one clears the other's selected
  state. The confirm button is enabled when either is selected.
- **URL encoding is unchanged by this entry.** Choosing a specific
  specialist still resolves to `specialist=<id>`; choosing "any" still
  resolves to `specialist=any` — both already decided in the "Stage 14
  implementation decisions" entry above.

This surfaced a second, pre-existing gap: the container-to-container
request reaches Django but is rejected with `DisallowedHost` — see next
entry.

### Stage 14 UI decisions: booking step 3 (date/time selection)

Decided 14.09.2026 — agreed before any code, per the stage-by-stage
workflow, during read-only recon ahead of building step 3.

- **Step 3's scope is time selection only.** It calls `AvailabilityView`
  exclusively, never `SpecialistsAtTimeView`. Resolving which specific
  specialist serves an "any specialist" booking is explicitly out of
  scope here — that resolution (the "least busy that day" rule) happens
  later, at the confirmation step immediately before payment, per the
  "Stage 14 scope revision" entry above.
- **Both entry paths render through the same UI and the same
  `AvailabilityView` call.** A specific specialist already chosen and
  `specialist=any` differ only in whether the `specialist` query param
  is included in the request — no branching UI for the two cases.
- **Data fetching is one `AvailabilityView` call per 14-day window**
  (`date_from`/`date_to`), not one call per day and not the full
  `max_advance_days` range in a single call. The flat `available_times`
  list returned is grouped into per-day buckets client-side (JS), so
  selecting a different day within the loaded window renders instantly
  with no additional request. A "next window" action, triggered when the
  user scrolls past the loaded 14 days, fetches the next 14-day range.
  Rationale: avoids both an oversized single response across the full
  booking window and a chatty per-day request pattern.
  - Clarification (14.09.2026): the 14-day window boundary is
    represented as a URL search param (e.g. `date_from`) on
    `booking/page.tsx`, consistent with the existing URL-based state
    approach for service/specialist/slot/step. "Load next window" is a
    plain server-navigated `<Link>`, mirroring the existing pagination
    pattern in `/services/page.tsx` (full Server Component re-render,
    not a client-side fetch) — not a client-side incremental fetch.
    Rationale: `getAvailability.ts` is server-only (reads
    `INTERNAL_API_URL`); building a client-safe fetch variant would mean
    a third client auth/fetch pattern in the codebase (alongside
    `api/client.ts`'s cookie model and `guestClient.ts`'s token model)
    for an endpoint that actually needs none (`AllowAny`). This is a
    deliberate simplicity tradeoff for the current pre-design-polish
    stage — a smoother client-fetched "load more" can be revisited
    during Stage 18-21 if the full-navigation reload proves noticeably
    jarring.
- **Days with no available times render as disabled/unselectable in the
  day strip, with no distinction between "outside the booking window,"
  "fully booked," or any other reason.** The API returns absence, not a
  reason code — existing backend behavior, not a gap to fix here.
- **Selecting a time slot appends `slot=<iso datetime>` to the URL** and
  advances the flow to the next step. This value must be built with
  `encodeURIComponent()` (or equivalent): `available_times` are
  salon-local ISO datetimes with a timezone offset (e.g. `+03:00`), and a
  literal `+` is unsafe unescaped in a query string. No appointment row
  and no hold exist yet at this point.
- **This is the first date/time UI in the codebase.** No existing
  calendar/date-picker component is being reused — recon confirmed none
  exists anywhere in the project.

### Stage 14 implementation decisions: step 4 contact-info form

Decided 15.09.2026 — agreed before any code, per the stage-by-stage
workflow, during read-only recon ahead of building step 4 of the
booking flow.

- **Contact-info state (name, email, phone) is held in a React
  Context, defined in a new client `layout.tsx` for the `/booking`
  route** — not in local `useState` inside a step-4 form component, as
  the earlier "Stage 14 planning" entry's now-corrected text implied.
  This is necessary, not merely a style choice: `booking/page.tsx` is a
  Server Component, confirmed by recon to hold no state across step
  navigations — each step's page render is independent, and no
  `layout.tsx` currently exists under `/booking` (recon also confirmed
  this: only the root `layout.tsx` exists, itself a stateless Server
  Component). A plain `useState` local to a step-4 form component
  cannot survive the "return to step 3 on slot conflict, then forward
  to step 4 again" round trip the existing slot-conflict-recovery
  decision (above) requires, because that round trip is two separate
  Server Component page renders with no shared instance between them.
  A client `layout.tsx` is the one part of the App Router tree that
  persists across page-level navigations within the same route,
  making it the correct — and minimal — place for this state: no
  existing step 2/3 component needs to change to accommodate it.
- **This does not change the existing slot-conflict recovery behavior
  itself** (return to step 3, preserve contact info, docs/DECISIONS.md
  § Stage 14 planning, "Slot conflict handling"). It only supplies the
  technical mechanism that makes that behavior actually possible given
  the Server Component structure — the earlier entry's "local
  component state (React `useState`)" mechanism detail is superseded
  by this one; the behavior it describes is not.
- **The guest booking `POST bookings/` call needs a new, minimal
  client-side fetch helper.** Neither existing client fetcher fits:
  `guestClient.ts`'s `guestApiRequest` requires an already-issued guest
  token as a parameter, but this call is the one that *issues* the
  token — there is no token yet to send. `client.ts`'s `apiRequest`
  sends cookie credentials (`credentials: "include"`) and echoes a CSRF
  cookie, built for the cookie/session-authenticated admin/client
  flows; this endpoint is `AllowAny` with no session to speak of. The
  new helper mirrors both existing ones' URL-building convention
  (`NEXT_PUBLIC_API_URL` + `/api/v1/salons/<slug>` + path) without a
  token header or cookie credentials.

### Fix: add `backend` to DJANGO_ALLOWED_HOSTS

Decided and implemented 11.09.2026.

Server-side Next.js code (`getServicesPage.ts`) now reaches Django over
the Docker network as `http://backend:8000` (`INTERNAL_API_URL`), sending
`Host: backend:8000`. Django's `CommonMiddleware` rejected this with
`DisallowedHost` (400) because `ALLOWED_HOSTS` only listed
`localhost,127.0.0.1` — correct for browser-origin requests, but this is
the first container-to-container request the project has made. Added
`backend` to `DJANGO_ALLOWED_HOSTS` in both the dev `.env` and
`.env.example` (`localhost,127.0.0.1,backend`) to allow it.

Verified end-to-end after recreating the `backend` container (a plain
`docker compose restart` does not pick up an `env_file` change — the
container keeps the env it was created with; `docker compose up -d
backend` was needed to recreate it): a direct request to
`http://backend:8000/api/v1/salons/bella-demo/services/` with
`Host: backend:8000` now returns `200` with real catalog data, and the
backend access log shows a matching `200` for the request the `/services`
page itself made (`GET /api/v1/salons/bella-demo/services/ HTTP/1.1" 200
837` at 10:01:05, correlated against the frontend's own `GET /services
200` log line for the same request) — confirmed as a live round trip, not
a cached render (an earlier `/services 200` had no corresponding backend
log entry and was discarded as a stale Next.js dev-cache hit before
concluding the fix worked).

### Fix: NullIf output_field on ServiceCategory/Service unique constraints

Decided and implemented 11.09.2026.

NullIf(KeyTextTransform(lang, "name"), Value("")) in
ServiceCategory/Service's Meta.constraints gets an explicit
output_field=TextField() (matching KeyTextTransform's own native
output type). Without it, Django's Python-side
UniqueConstraint.validate() pre-check (reached via Model.full_clean(),
only exercised through /admin/ saves — never through the DRF
serializer path, which does its own queryset check) raises FieldError
on the ambiguous TextField/CharField mix between KeyTextTransform and
an untyped Value(""). Postgres itself never hits this — it's a
Python-side ORM resolver limitation, not a real SQL type conflict.

Requires a new migration (RemoveConstraint+AddConstraint pair per
affected constraint, 4 total across both models) — output_field
becomes part of the expression's deconstruct() output, so migration
state no longer matches the model without it, even though the
generated SQL is byte-identical to the existing constraints. This
migration is a schema no-op in Postgres.

### Fix: CORS_ALLOW_HEADERS missing X-Guest-Token

Decided and implemented 16.09.2026.

`guestApiRequest` (`frontend/src/lib/api/guestClient.ts`) sends a custom
`X-Guest-Token` header on every guest-token-authenticated request
(detail/cancel/pay), which forces a CORS preflight. No
`CORS_ALLOW_HEADERS` setting existed anywhere in the backend, so
django-cors-headers fell back to its own `default_headers` list
(accept, authorization, content-type, user-agent, x-csrftoken,
x-requested-with) — `x-guest-token` was never advertised as allowed.
The preflight `OPTIONS` request itself still returned 200 (backend
logs showed it), but the browser then silently blocked the real
`GET`/`POST` before sending it — no error surfaced anywhere in the
frontend, since `PaymentStatus.tsx` treats a failed `guestApiRequest`
call identically to an invalid/expired token by design (§ Stage 14
step 5). Fixed by extending, not replacing, the defaults:
`CORS_ALLOW_HEADERS = [*default_headers, "x-guest-token"]`
(`backend/config/settings/base.py`).

**Lesson: mocked-fetch frontend tests cannot catch a CORS
misconfiguration.** All 149 frontend tests were green throughout —
`guestClient.test.ts` and `PaymentStatus.test.tsx` both stub `fetch`
directly, so no real browser, no real preflight, and no
`Access-Control-Allow-Headers` check ever entered the test run. The
break was only visible in an actual browser session hitting the real
backend. Same broader lesson as the "stale Next.js dev-cache hit"
aside in the DJANGO_ALLOWED_HOSTS fix entry above — green tests plus
a working build is not the same claim as a working live app — just at
a different layer (network/CORS here, dev-server caching there).

### Stage 14 step 5: `/booking/pay` architecture

Decided 16.09.2026 — agreed before any code, per the stage-by-stage
workflow, during read-only recon ahead of building step 5 of the
booking flow. None of this is implemented yet.

- **This step builds full status-branching
  (`PENDING_PAYMENT`/`CONFIRMED`/`EXPIRED`/`CANCELLED`) and a working
  mock payment action, but deliberately does not integrate a real
  payment provider.** Real provider integration (LiqPay/Fondy/WayForPay
  — candidates only per § Payments, none chosen) is deferred to a
  separate future decision. Rationale: status branching, routing, and
  token handling are stable regardless of which provider eventually
  lands; only the pay-action UI itself (currently a button that calls
  the mock endpoint directly) will need rework into a redirect-based
  flow once a real provider is integrated — a narrow, contained piece
  to redo later, not the whole page.
- **`/booking/pay` is a Client Component**, per the existing Stage 14
  planning decision ("the frontend `/booking/pay` page must be a
  Client Component"), since it must read `appointment_id` and `token`
  from the URL fragment (`window.location.hash`) — fragment values are
  never sent to the server as part of any HTTP request, which is why
  the token is transported this way (§ Stage 3, "Credential-token
  transport: URL fragment"). This read must happen client-side after
  mount (e.g. inside `useEffect`), not during any server render, since
  the fragment isn't available server-side at all.
- **On mount, the page calls `GET guest/appointments/<id>/`** (via
  `guestApiRequest`, with the token read from the fragment) to get the
  appointment's current status, then branches:
  - `PENDING_PAYMENT` → renders a pay button labeled "Оплатити" that
    calls `POST guest/appointments/<id>/pay/` (currently
    `MockPaymentProvider` — no real charge, no real redirect).
  - `CONFIRMED` → renders an "already paid" message, no button.
  - `EXPIRED` or `CANCELLED` → renders a "no longer available" message.
  - After a successful pay call, the page renders a "pending
    confirmation" state — payment status stays `pending` until a
    webhook (currently nothing real triggers it) confirms it. This is
    an honest reflection of current mock behavior, not a bug to fix
    here.
- **An invalid or expired token, or a missing fragment entirely** (e.g.
  someone navigates to `/booking/pay` directly with nothing after
  `#`), renders a generic "link invalid or expired" message — not a
  crash, not `notFound()`. This isn't a routing-level 404; it's a
  runtime token-validation outcome, so it's handled as page state, not
  as a Next.js not-found route.

### Stage 14 payment step: WayForPayProvider architecture

Decided 16.09.2026 — agreed before any code, per the stage-by-stage
workflow, during read-only recon ahead of adding a real
`WayForPayProvider` alongside `MockPaymentProvider` (test/sandbox mode,
not going live). None of this is implemented yet.

- **`WayForPayProvider.start_payment()` uses WayForPay's
  server-to-server "Create Invoice" API** (`CREATE_INVOICE`,
  HMAC_MD5-signed POST to `api.wayforpay.com/api`), not the
  client-rendered signed-form "Purchase" flow
  (`secure.wayforpay.com/pay`). Rationale: Create Invoice returns a
  plain `invoiceUrl` string in its JSON response, which fits the
  existing `PaymentIntent.provider_data: str | None` contract directly
  — no new frontend form-generation/auto-submit mechanism needed.
- **`WayForPayProvider` generates its own unique `orderReference`
  internally for each actual `start_payment()` call** — not the bare
  `reference` argument passed in (which stays a stable
  appointment-derived identifier at the interface level) — because
  WayForPay rejects a reused `orderReference` with a "(1112) Duplicate
  Order ID" error (confirmed via a real-world SDK workaround, not
  assumed). The unique value generated is returned as
  `PaymentIntent.provider_reference_id`, so `Payment.provider_reference_id`'s
  existing overwrite-in-place behavior on retry (already implemented,
  unchanged) continues to correlate correctly with inbound webhooks.
  This is entirely internal to `WayForPayProvider` — the
  `PaymentProvider` interface, `GuestAppointmentPayView`, and
  `payments/services.py` are not changed by this.
- **`Payment` gains a provider-neutral `provider_data` field** (not
  WayForPay-specific naming), persisted at creation time from
  `PaymentIntent.provider_data`. The existing-PENDING short-circuit
  path in `initiate_payment` (`payments/services.py`) is updated to
  return the stored value instead of hardcoding `None`, so a guest
  reloading `/booking/pay` or clicking "Оплатити" again while a payment
  is still pending sees the same payment link again rather than
  nothing. This fixes a gap in the existing mock-shaped design (a real
  provider can return something worth re-showing; the mock always
  returns `None` so this was never exercised) — it is a general
  interface fix, not a WayForPay-specific workaround.
  `MockPaymentProvider`'s behavior (always `None`) is unaffected.
- **Frontend: `PaymentStatus.tsx` needs a small addition** (not present
  today) — when the pay response includes `provider_data`, redirect the
  guest to it (`window.location.href`) instead of only rendering the
  static "pending confirmation" message. The mock path (`provider_data`
  always null) keeps rendering the existing static message unchanged.
- **`verify_signature`'s HMAC_MD5 field order is endpoint-specific**
  per WayForPay's own docs (Create Invoice's signed string differs from
  Verify's) — this must be implemented and tested against real
  documented signature examples, not a single generic helper assumed to
  work for every WayForPay endpoint.

### Stage 8 refund decision, revisited: `PaymentProvider.refund()` gains an `amount` parameter

Decided 16.09.2026 — agreed before any code, per the stage-by-stage
workflow, during the same WayForPay integration recon as the entry above.
None of this is implemented yet.

The original Stage 8 decision, in `payments/providers/base.py`'s `refund`
docstring, reads:

> Reverses the specific existing transaction identified by
> provider_reference_id. No amount argument: business rules always
> refund the full deposit or nothing, so the amount is implied by the
> original payment.

- **That business rule is unchanged.** Refunds are still always the full
  deposit or nothing — this decision does not reopen partial refunds.
  `initiate_refund` (`payments/services.py`) will always pass
  `payment.amount`, the value already on the row it just fetched under
  `select_for_update`, never a partial or separately-computed value.
- **What changes is mechanical, not business logic: `refund()` gains an
  explicit `amount: Decimal` parameter**, mirroring `start_payment`'s
  existing `amount` param. Cause: WayForPay's real refund API requires an
  explicit amount field even for a full refund — a requirement the
  original Stage 8 design couldn't anticipate, since it was written
  before any real provider was integrated and reasonably assumed the
  amount could stay implied.
- **This is a technical interface widening prompted by a concrete
  external-API constraint discovered during WayForPay integration, not a
  reversal of the "full deposit or nothing" business decision.** The two
  are easy to conflate because they touch the same method signature; they
  are independent.

### Stage 8 refund decision, revisited again: `PaymentProvider.refund()` gains a `currency` parameter

Agreed 17.09.2026, in a separate session from the `amount` widening directly
above (agreed 16.09.2026) — a follow-up decision, not a backdated or merged
part of that earlier one. Same decision pattern, though: WayForPay's Refund
API requires an explicit `currency` field, mirroring how `amount` was found
to be required. Agreed before the change was written, per the stage-by-stage
workflow.

- `refund()` gains an explicit `currency: str` parameter, mirroring
  `start_payment`'s own `currency` param, for the same reason `amount` was
  added above. `initiate_refund` (`payments/services.py`) passes
  `payment.currency`, the value already on the row it fetched.
- Implemented in the same change as this entry: `WayForPayProvider.refund()`
  now calls WayForPay's real Refund API
  (`POST https://api.wayforpay.com/api`, `transactionType: "REFUND"`).
  Its signature is its own, shorter formula —
  `merchantAccount;orderReference;amount;currency`, HMAC_MD5 — distinct
  from both Create Invoice's 9-field signature and the payment-status
  webhook's 8-field signature already in this file; do not conflate the
  three. Success is decided by `reasonCode == 1100` (WayForPay's universal
  "Ok" code), not `transactionStatus`, since WayForPay's own Refund docs
  example shows a lowercase `"refunded"` status unlike the
  `"Approved"`/`"Declined"` casing the payment-status webhook uses. On any
  other `reasonCode`, or a network-level failure, it raises
  `PaymentProviderError` directly (this file's other methods raise
  `RuntimeError`/let `requests` exceptions propagate instead, leaving the
  wrap-into-`PaymentProviderError` step to `payments/services.py`; `refund()`
  raises it directly per this decision instead, since there is no
  provider-neutral "reason" string here worth preserving before the wrap).
  WayForPay's Refund response doesn't mint a separate refund-transaction id
  the way `RefundIntent`'s docstring describes Stripe's Refund object doing
  — `refund()` returns the same `provider_reference_id` it was given, the
  only id WayForPay's response actually carries.

### Refund-eligibility gap (found 19.09.2026, closing Stage 8)

Recon confirmed the ≥24h-full-refund / <24h-deposit-forfeited business rule
(§ Business rules) is documented but never implemented — cancel_appointment
(booking/services.py) never calls initiate_refund; GuestAppointmentCancelView's
own "Stage 8 work" comment near the refund hook-in point is stale (Payment now
exists, was never wired in).

Correction to an earlier same-session note: this is NOT a partial-refund
feature. initiate_refund and the provider layer already pass the full
payment.amount only — Stage 8's own decision ("always the full deposit or
nothing") is unchanged. The fix is a binary eligibility check, not a new
amount/status field.

Fix: add an eligibility check — salon-initiated cancellations always qualify;
customer-initiated cancellations qualify only if now is ≥24h before
start_datetime. On eligible, call the existing initiate_refund with the
appointment's Payment (if one exists and is SUCCEEDED); on ineligible, do
nothing (Payment stays SUCCEEDED, deposit kept). Recommended home: inside
cancel_appointment itself (role-agnostic, already receives
cancelled_by/now/appointment) rather than duplicated per caller — so both the
existing guest-cancel path and Stage 15 item 4's future account-cancel path
get correct behavior from one place.

This is a prerequisite for Stage 15 item 4's cancel action, not new Stage 15
scope — cross-referenced from item 4 above.

Known parallel case, deliberately unaddressed for now: `payments/views.py`'s
`PaymentWebhookView` also calls `initiate_refund` uncaught, but at lower
severity than `cancel_appointment`'s original gap — a resulting 502 only
triggers the payment provider's own webhook retry, which the idempotency
ledger (`ProcessedWebhookEvent`) already makes a safe no-op, not a
human-facing error the way an uncaught failure in `cancel_appointment` would
have been. Not urgent; not fixed here.

### WayForPayProvider: first-time technical conventions (HTTP, mocking, Decimal-to-string, errors, credentials)

Decided 16.09.2026 — agreed before any code, per the stage-by-stage
workflow, during read-only recon ahead of implementing `WayForPayProvider`.
None of this is implemented yet. Recorded here rather than left implicit
because every item below is a genuine first for this codebase — no prior
outbound HTTP call, no prior HTTP-mocking pattern, no prior Decimal-to-
string convention — so each choice sets precedent for any future external
integration, not just this one.

- **HTTP library: `requests`**, added to `requirements/base.txt` as a
  runtime dependency (not dev-only) — `WayForPayProvider` runs in
  production, not only in tests.
- **Test mocking: `responses`**, added to `requirements/development.txt`
  (test-only). Chosen over `unittest.mock.patch`-ing the call site directly
  because it verifies the actual outbound request shape — URL, method,
  headers, signed body — rather than only that some function was called
  with some arguments.
- **Decimal-to-string for signature construction: explicit
  `str(amount.quantize(Decimal("0.01")))`** — quantize first, then convert
  — rather than relying on whatever precision the `Decimal` happens to
  already carry. This guards the HMAC input against a `Decimal` that
  arrived non-normalized (e.g. `Decimal("100")` vs `Decimal("100.00")`
  stringify differently) producing a signature mismatch against WayForPay's
  own computation.
- **Error handling: `WayForPayProvider` raises no custom exceptions of its
  own.** HTTP/network/parsing failures propagate naturally as ordinary
  exceptions. No change to `payments/services.py`, which already wraps any
  `Exception` from a provider call into `PaymentProviderError` at its two
  existing call sites (`initiate_payment`, `initiate_refund`) — that
  translation is the service layer's job and doesn't need duplicating
  inside the provider.
- **Credentials: `WAYFORPAY_MERCHANT_ACCOUNT` / `WAYFORPAY_SECRET_KEY` /
  `WAYFORPAY_DOMAIN_NAME` are optional env vars with empty-string
  defaults**, mirroring `EMAIL_HOST_USER`'s existing pattern
  (`config/settings/base.py`) rather than `DJANGO_SECRET_KEY`'s
  hard-required one — so `docker compose up` boots without real sandbox
  keys. **`WayForPayProvider` is not wired as any view's default
  `provider_class` in this step** — `MockPaymentProvider` remains the
  default everywhere; going live is separate future work, already scoped
  out as such in the earlier "Stage 14 payment step: WayForPayProvider
  architecture" entry above.

### Stage 14 payment step: WayForPay webhook → PaymentWebhookView mapping

Decided 17.09.2026.

- **`provider_reference_id`** maps directly to WayForPay's `orderReference`
  — already stored on `Payment` by `start_payment()`, no new field needed.
- **`event_type`** is derived from WayForPay's `transactionStatus`:
  `Approved` → `payment_succeeded`, `Declined` → `payment_failed`,
  `Refunded`/`Voided` → `refund_succeeded`. Any other `transactionStatus`
  is an unrecognized `event_type` — the existing no-op path in
  `PaymentWebhookView.post` is unchanged.
- **`event_id`** (the idempotency key) is the composite
  `f"{orderReference};{transactionStatus};{reasonCode}"`, not
  `orderReference` alone — a bare `orderReference` would collide across
  distinct lifecycle events on the same order (e.g. `Declined` then later
  `Approved`, or `Approved` then later `Refunded`), while the composite
  still dedupes true delivery retries of the identical status (WayForPay
  resends the same notification unchanged on retry).
- **`Payment.provider_reference_id` gets a DB-level `unique=True`** (it was
  `db_index`-only). The webhook lookup in `PaymentWebhookView.post`
  (`Payment.unscoped_objects.get(provider_reference_id=...)`) already
  assumes exactly one matching row per value; nothing currently guarantees
  that at the DB layer, only that each `start_payment()` call happens to
  produce a fresh value.

This decision is recorded now, ahead of writing the migration — the
`unique=True` change touches `Payment`'s field definition, which
`CLAUDE.md` requires be raised and approved before the model change (or
the migration containing the resulting `AlterField`) is written.

### Known issue: `test_create_guest_appointment_raw_token_round_trips_through_validate_guest_token` is a time bomb

Noted 18.09.2026, during the tenants Salon-info endpoint work above — found
failing in a full-suite run unrelated to that change.

- `tests/test_booking_create_guest_appointment.py` hardcodes a fixed past
  `SAFE_NOW` (2026-08-16) as the `now` passed into token issuance. The
  issued token's `expires_at` is frozen relative to that fixed clock
  (`GUEST_TOKEN_VALIDITY` = 30 days, so ~2026-09-15), but
  `validate_guest_token` (`booking/guest_tokens.py`) checks expiry against
  the real wall-clock `timezone.now()`, not the test's injected `now` — the
  two clocks were never the same one to begin with.
- **Currently failing**: today's real date has passed the token's fixed
  expiry, so the "round trips" assertion now fails with
  `InvalidOrExpiredTokenError`. Confirmed via `git status` that this is
  unrelated to any change made in this session (only `tenants/` and
  `config/urls.py` were touched) and reproduces identically when run in
  isolation.
- **A genuine test design flaw ("time bomb"), not a regression** — the
  test was always going to fail once enough real time passed since
  `SAFE_NOW` was chosen, regardless of any code change. Needs its own fix
  (freeze both issuance and validation against the same test clock, e.g.
  `freezegun` or an injectable clock in `validate_guest_token`) as
  separate future work — not fixed here.

### Known issue: pre-existing `ruff` findings

Noted 18.09.2026, while running `ruff check`/`ruff format --check` after
the currency/tenants session's frontend work above.

- `ruff check` flags `catalog/serializers.py` (two lines over 100 chars,
  `E501`) and `catalog/migrations/0004_remove_service_service_salon_name_en_uniq_and_more.py`
  (an unsorted import block, `I001`). `ruff format --check` additionally
  flags `catalog/migrations/0005_service_description_servicecategory_photo.py`
  (quote style / line-wrapping that Django's migration autogeneration
  doesn't run through `ruff format`).
- **Confirmed via `git log` to predate this session entirely** — all
  three files were last touched by `d1cd709` ("feat: add
  Service.description and ServiceCategory.photo fields") and `bbe09c9`
  ("fix: add output_field to NullIf in catalog unique constraints"),
  Stage 11.5 work, unrelated to the currency/tenants Salon-info endpoint
  or frontend price-display work.
- **Not fixed here, deliberately out of scope** — a trivial cleanup pass
  (`ruff check --fix` / `ruff format`) whenever convenient, not tied to
  any current change.

### Stage 15 planning: account appointments, auth context, registration, client dashboard

Decided 18.09.2026 — contract agreed before any code, per the
stage-by-stage workflow.

Scope, in build order:

1. **Backend: a `GET` endpoint listing appointments for the currently
   authenticated `Account`**, scoped through `TenantScopedManager` to
   that Account's linked `Customer` in the current salon. Confirmed by
   recon: no such endpoint exists today —
   `backend/booking/urls.py` currently exposes only the guest-token-based
   `bookings/` and `guest/appointments/<id>/...` routes, all scoped by
   `appointment_id` + token, none by `Account`. **Implemented
   18.09.2026:** `GET appointments/mine/` (`AccountAppointmentListView`,
   `backend/booking/views.py`), filtered through `Appointment.objects`
   (`TenantScopedManager`). Cross-tenant isolation with a same-email
   Account in another salon is explicitly test-covered
   (`tests/test_booking_account_appointment_list.py`).
2. **Frontend: a shared `AuthContext`/`useAuth` hook**, replacing the ad
   hoc local `useState` currently in `app/login/page.tsx`. Confirmed by
   recon: no `AuthContext` or `useAuth` exists anywhere in
   `frontend/src` today. `MeView` (`backend/accounts/views.py`) has no
   `throttle_scope` (confirmed by recon) — acceptable since putting
   `AuthContext` in the root layout means `auth/me/` is called on every
   page load, and an unthrottled, cheap, cookie-checked GET is the right
   posture for that; flag this for a `throttle_scope` if/when
   infra-level rate limiting is added later. `logout()` is now in scope
   (decided 19.09.2026) — POST to the existing `auth/logout/` endpoint
   (`LogoutView`, recon-confirmed already built and cookie-clearing),
   then reset `AuthContext`'s `me` state to `null`.
3. **Frontend: a registration page**, calling the existing
   `POST auth/register/` backend endpoint (`RegisterView`,
   `backend/accounts/views.py`). The backend side is already built and
   unchanged by this stage; only the frontend page is missing —
   confirmed by recon: no `app/register` route exists today. The
   registration page must be reachable from `app/login/page.tsx` via a
   "Немає акаунту? Зареєструватися" link, added to the login page —
   recon-confirmed gap: the login page currently has no such link, so a
   first-time visitor at `/login` has no way to reach `/register`. The
   registration page itself already links back to `/login` ("Вже маєте
   акаунт? Увійти").
4. **Frontend: a real `/client/page.tsx` dashboard**, replacing the
   current placeholder (`"Client dashboard placeholder"`), showing "my
   bookings" via the endpoint from item 1. Refinements agreed
   19.09.2026:
   - Shows price/deposit amount per appointment. `AppointmentAccountSerializer`
     (item 1) does not currently include these fields — widen it to add
     them (they already exist on the `Appointment` model as the
     price/deposit snapshot fields, `service_price_at_booking` /
     `deposit_percentage_at_booking`, noted elsewhere in this doc).
   - Appointments split into two sections: "Найближчі" (upcoming) and
     "Минулі" (past). A cancelled appointment always shows under
     "Минулі" regardless of its `start_datetime`, even if the date is
     still in the future — cancelled appointments require no further
     action from the client.
   - Adds a cancel action from the dashboard. This needs a new
     Account-scoped cancel endpoint, symmetric to item 1's list endpoint
     (authorization: only the requesting Account's own linked
     Customer's appointment). Before implementing, recon whether the
     existing cancellation/refund business logic (≥24h full refund,
     <24h deposit forfeited) already lives in a shared service function
     reusable from `GuestAppointmentCancelView`, or is inline there and
     needs extracting first — do not duplicate that logic. Depends on
     the refund-eligibility fix, § "Refund-eligibility gap (found
     19.09.2026, closing Stage 8)" — do not duplicate its logic.
   - Split into two waves: list + sections + cancel action first (backend
     ready); the "Оплатити" button is deferred to item 14 (new endpoint
     needed).
5. **Account-aware booking path.** Two distinct sub-cases, both real:
   - If the Account already has a linked Customer (e.g. via the existing
     guest-booking→verification email-match merge), skip the
     contact-info step in the booking flow — the frontend already has
     the Customer's name/email/phone via item 7's expanded profile data.
   - If the Account has NO linked Customer yet (recon-confirmed:
     `_verify_and_link` only links an EXISTING guest Customer matching
     by email — it does not create one; an Account that registered
     without ever booking as a guest first has no path to a Customer
     until this exact moment), the contact-info step still shows
     normally, but the resulting booking must create a new Customer AND
     link it to the Account at creation time — not leave it as an
     unlinked, guest-style Customer. This is likely the primary way most
     users ever get a linked Customer, not just a fallback: most people
     register when they're already about to book, not guest-book then
     register later with the same email.

   Before implementing, recon whether a shared `create_appointment`
   function can be reused with an added Account-linking step, or whether
   a new function/branch is needed — do not duplicate booking-creation
   logic between the guest and account-aware paths.

   Builds on the guest-only Stage 14 flow per the split already recorded
   in § "Stage 14 (frontend booking flow + payment + confirmation) —
   scope" above.

   Refined 19.09.2026 — decided before implementation, per the
   stage-by-stage workflow:

   - Account-aware booking applies only to Accounts with
     `email_verified_at` set (`backend/accounts/models.py`). An
     unverified Account keeps using the existing guest flow unchanged —
     no new error, no blocking. Reason: `appointments/mine/` filters by
     `customer_id` (item 1), so linking an unproven email to an existing
     Customer would expose that Customer's booking history; creating a
     new Customer is also impossible whenever a guest Customer with the
     same `(salon, email)` already exists (`customer_salon_email_uniq`),
     and any distinct error surfaced for that case would itself reveal
     whether that email belongs to an existing Customer in the salon.
   - The backend enforces this, not just the frontend: the account
     booking endpoint rejects an unverified Account regardless of
     whether a Customer exists for its email. The frontend separately
     chooses which flow to render (skip vs. show contact form) based on
     `email_verified_at`.
   - The Customer's `email` is always taken from `request.user.email`
     (the authenticated Account's own, verified email), never from the
     request payload — the contact form, when shown, supplies
     name/phone only.
   - Customer resolution runs inside one `transaction.atomic()`, with
     `select_for_update()` on the `Account` row: if `account.customer_id`
     is already set, use that Customer; otherwise `get_or_create` a
     Customer by `(salon, account.email)`, link it to the Account, and
     apply the name/phone supplied on the request (the contact form is
     shown only in this second case, per the two sub-cases above).
   - `create_appointment`/`create_appointment_for_any_specialist` are
     reused unchanged (`backend/booking/services.py`) — only the
     customer-resolution step differs from `create_guest_appointment`'s.
   - No `guest_token` is issued for an account booking. On success the
     frontend redirects to `/client` instead of `/booking/pay`. Payment
     for an account booking is Stage 15 item 14's scope
     (`AccountAppointmentPayView`) and must be built immediately after
     this item.
   - Known issue, not fixed by this item: `get_or_create_guest_customer`
     (`backend/accounts/services.py`) unconditionally overwrites an
     existing Customer's `name`/`phone` from every new guest booking
     ("Option A", same file's docstring). Now that a Customer can be
     linked to an Account, a guest booking made under a registered
     user's email silently overwrites that user's own profile
     name/phone. Left as a known issue, not addressed here.

   Recon checks done before writing this refinement:
   - `MeSerializer` does not expose `email_verified_at`
     (`backend/accounts/serializers.py:69-96`; its own docstring, line
     74, says so explicitly — deferred until a "verify your email" UI
     exists).
   - The `BOOKING_CREATED` notification link is built as
     `build_salon_frontend_url(salon.slug, "/booking/pay") +
     f"#appointment_id={appointment.id}&token={token}"`, where `token =
     derive_guest_token(appointment.id)` (`backend/notifications/services.py:222-226`)
     — it depends on a guest token. Open question, not resolved here:
     what should an account booking's confirmation email link to
     instead?

   Extended 19.09.2026 — further decisions on the two open points above:

   - `MeSerializer` gains a new read-only boolean field `email_verified`
     (`backend/accounts/serializers.py`), derived as
     `account.email_verified_at is not None` — the timestamp itself is
     not exposed. Reason: the frontend only needs yes/no to choose
     between the guest flow and the account-aware flow (this item), and
     the same field serves item 13's verification-reminder banner. The
     `MeSerializer` docstring's "no `email_verified_at` (deferred...)"
     note must be updated when this lands. This is a contract change to
     `GET auth/me/` and needs its own test.
   - Resolves the open question above: the `BOOKING_CREATED` email for
     an account booking links to the salon's `/client` page, with no
     guest token. The choice between the guest link
     (`/booking/pay#appointment_id=...&token=...`) and the `/client`
     link is made by an explicit parameter passed down from the
     booking-creation call site (guest path vs. account path) — never
     by inspecting whether the appointment's Customer happens to have a
     linked Account. Reason: the guest path must stay exactly as built
     in Stage 14 regardless of whether the guest's email happens to
     match a registered Account, and a guest booking made under a
     registered user's email has no session at send time, so a
     `/client` link would be dead for that recipient.

   Recon check done before writing this extension: of the notification
   triggers built in `backend/notifications/services.py`, only three
   build a link at all, and all three currently derive it from a guest
   token (`derive_guest_token`) — `APPOINTMENT_REMINDER` (lines
   141-169, links to `/appointments/<id>/manage/<token>/`),
   `BOOKING_CONFIRMED` (lines 171-199, same manage-link shape), and
   `BOOKING_CREATED` (lines 201-231, links to
   `/booking/pay#appointment_id=...&token=...`). `BOOKING_CANCELLED`,
   `BOOKING_EXPIRED`, `PAYMENT_SUCCEEDED`, `PAYMENT_FAILED`, and
   `REVIEW_REQUEST` are static text with no link at all
   (`_MESSAGES`, lines 65-98). `EMAIL_VERIFICATION` is not handled by
   this builder/trigger system at all — it's sent from
   `accounts/tasks.py` with its own signed verification token
   (`/verify-email#token=...`), unrelated to guest tokens.
   `APPOINTMENT_REMINDER` and `BOOKING_CONFIRMED` are outside this
   item's scope but will hit the same guest-token-vs-account question
   `BOOKING_CREATED` just resolved, once an account booking can reach
   REMINDER/CONFIRMED — flagged here, not resolved.

   Line-reference check: the `BOOKING_CREATED` link citation above
   (`backend/notifications/services.py:222-226`) was verified against
   the file and is correct as written.
6. **Frontend: a `/verify-email` page.** Recon-confirmed gap: the
   verification email links to a frontend URL
   (`/verify-email#token=<token>`, built by `build_salon_frontend_url`
   in `accounts/tasks.py`) but no such frontend route exists today —
   without this page, the registration flow (item 3) is a dead end; a
   registered user has no way to actually verify their email.
   `VerifyEmailView` (`backend/accounts/views.py`) is POST-only, reads
   the token from the request body (not URL/query — the frontend must
   read it out of the URL fragment client-side and POST it), returns
   204 on success / 400 (neutral, no-enumeration) on failure, and does
   NOT set auth cookies — verification does not log the user in,
   matching `LoginView` being the sole cookie-setting view (Stage 12).
   On success, show a confirmation state with a link to `/login` (the
   user logs in separately with their password — no auto-login).
   Follows the same fragment-reading client-component-inside-a-Server-
   Component pattern as `/booking/pay`'s `PaymentStatus.tsx`
   (Stage 14).

   Note: password-reset (`/reset-password#uid=...&token=...`) uses the
   identical link-fragment pattern but has its own separate, currently
   unbuilt frontend page — explicitly out of scope here, flagged only
   for awareness.
7. **Backend + frontend: Account/Customer name and phone in the profile
   panel.** Currently nothing exposes this — `MeSerializer` is
   deliberately `email`+`role` only (recon-confirmed), and no other
   authenticated-Account endpoint includes it either. Must handle the
   case where the Account has no linked `Customer` yet (verification
   does not gate login — an Account can be logged in before its
   guest→Customer merge happens, per the `/me/` endpoint's own decision
   note): in that case, name/phone fields are simply absent/hidden in
   the profile panel, not shown as empty or defaulted.
8. **Backend + frontend: change email.** Must (a) require verification
   of the new email address before it becomes active — reuse the
   existing token-signing pattern (`accounts/tokens.py`) rather than
   inventing a new one, if it fits (recon first to confirm); and (b)
   keep the old email valid for login until the new one is verified —
   an in-flight, unverified email change must never lock the account
   holder out. Surfaced in the profile panel (item 7).
9. **Backend + frontend: change password (authenticated).** Requires
   the current password — distinct from the existing unauthenticated
   `PasswordResetConfirmView` flow (which stays as-is for "forgot
   password"). Uses the same `validate_password` rules as registration
   (item 3). Surfaced in the profile panel (item 7).
10. **Backend + frontend: edit Customer name/phone from the profile
    panel.** Item 7 only exposes these fields for reading; editing them
    requires a new endpoint (updating the Account's linked Customer).
    Same null-Customer edge case as item 7 applies — if no Customer is
    linked yet, these fields (and their edit entry points) are simply
    absent, not editable-but-empty.
11. **Frontend: "forgot password" flow.** Two pages: a request page
    (enter email, calls the existing `PasswordResetRequestView`) and a
    confirm page (reads uid+token from the URL fragment per the
    `#uid=...&token=...` pattern noted when item 6 was scoped, calls the
    existing `PasswordResetConfirmView`). Surfaced as a link from the
    change-password screen (item 9) for users who don't remember their
    current password. Both backend views already exist and are
    unauthenticated by design; only the frontend is missing.
12. **Frontend: route protection for `/client` and its subtree.**
    Nothing today stops an unauthenticated visitor from navigating
    directly to `/client`, `/client/profile`, or any of its sub-pages.
    Add `app/client/layout.tsx` (mirroring the existing
    `app/booking/layout.tsx` precedent — a client-component layout
    wrapping a route subtree) that checks `useAuth()` and redirects to
    `/login` when resolved to logged-out.
13. **Frontend: "verify your email" reminder banner.** An Account can be
    logged in without being verified (verification does not gate login,
    per the `/me/` endpoint's own decision note) — and per item 5's
    finding, this can be a real, possibly extended state for a user who
    registered but hasn't booked/verified yet. Currently the only way to
    trigger a fresh verification email is the registration-confirmation
    screen, unreachable once that tab is closed. Add a persistent banner
    on the dashboard/profile for an unverified logged-in Account, with a
    resend action calling the existing `ResendVerificationView`.
14. **Backend + frontend: "Оплатити" (pay) action from the account
    dashboard.** Recon-confirmed gap: `/booking/pay` and
    `GuestAppointmentPayView` are guest-token-only; an authenticated
    Account's own booking may have no guest token at all, so neither can
    be reused. Needs a new `AccountAppointmentPayView`
    (`POST appointments/<id>/pay/`), mirroring
    `AccountAppointmentCancelView`'s ownership check (own linked
    Customer's appointment, 404 on mismatch) and calling the existing
    `payments.services.initiate_payment`. Split from the rest of item 4
    (list/sections/cancel, which are already backend-ready) — the
    dashboard ships first without a working pay button; this lands as a
    follow-up.

Explicitly out of scope for Stage 15:

- **Any "claim a specific guest booking via its guest token while logged
  in" mechanic.** Not needed: the existing email-verification merge
  (Stage 3-R, `accounts/views.py`'s `_verify_and_link`) already links a
  guest `Customer` to an `Account` by matching email at verification
  time, covering the guest→Account linking need without a separate claim
  flow.
