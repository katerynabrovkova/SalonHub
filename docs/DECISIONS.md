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
12. Frontend skeleton — design system, API client, auth ✓
13. Frontend catalog / service / specialists / reviews ✓
14. Frontend booking flow + payment + confirmation ✓
15. Frontend customer account + guest token management ✓ (20.10.2026)
16. AI assistant backend
17. AI assistant frontend widget
18. Admin shell + dashboard + appointments list
19. Admin calendar + appointment create/edit
20. Admin services, specialists, working hours, days off
21. Admin clients, reviews, notifications, statistics
22. Productionization — Docker, CI/CD, README
23. Production-readiness audit (fresh session)

**Clarification 20.09.2026 (no stage added or renumbered):** Stage 18
begins with the design system (color tokens, typography scale, shared
component library), which the Stage 12 closure decision deferred to
Stages 18-21; the admin UI of Stages 18-21 is built on top of it. The
frontend design pass over the client-facing pages (`/services`,
`/specialists`, `/booking`, `/client`), the full apex marketing landing
page, and UI-chrome i18n also belong to Stages 18-21. The exact scope of
that pass is decided when Stage 18 starts, using the salon-site visual
references. Known issues waiting for this pass: the unbounded "Далі →"
on booking step 3 (see its known-issue entry).

- Recorded 29.09.2026: on `/register` the "Паролі не збігаються." message
  is easy to miss: plain text, not styled as an error and not linked to
  the confirm-password field. Deferred by Katya to the design pass. A
  possible approach is an `error` prop on `PasswordField` linked through
  `aria-describedby`; when the message appears (on submit or on leaving
  the field) is still to decide.
- Recorded 30.09.2026: user-visible English strings remain in the
  frontend (for example the generic "Something went wrong. Please try
  again." in `AccountBookingForm`, `ContactInfoForm` and the login page).
  Decided by Katya: replace them with Ukrainian in a separate step after
  Stage 15 closes. New UI is written in Ukrainian from now on. Files, under
  `frontend/src/app/`: `admin/page.tsx`, `booking/AccountBookingForm.tsx`,
  `booking/ContactInfoForm.tsx`, `booking/page.tsx`, `booking/pay/page.tsx`,
  `booking/pay/PaymentStatus.tsx`, `layout.tsx` (metadata description,
  `lang="en"`), `login/page.tsx`, `page.tsx`, `register/page.tsx` (shows
  backend messages as-is, including `apiRequest`'s "Request failed."
  fallback), `reviews/page.tsx`, `services/page.tsx`, `specialists/page.tsx`,
  `specialists/[id]/page.tsx`, `verify-email/page.tsx`. Done 20.10.2026:
  replaced in these files and in two more found during the pass,
  `confirm-email-change/page.tsx` and `reset-password/page.tsx` (the "still
  in development" fallback); `lang="uk"`; the "Request failed." fallback is
  now "Не вдалося виконати запит." in `lib/api/client.ts`,
  `lib/api/guestClient.ts` and `lib/booking/createGuestBooking.ts`. Decided
  by Katya: the home page's salon line became "Сторінка салону в розробці."
  without the slug (the page is replaced by the landing page in the design
  pass); "Email" labels and "SalonHub" stay as they are; developer-facing
  errors and Next.js's built-in 404/error pages are out of scope.
- Recorded 02.10.2026: guest booking does not normalize `Customer.email`.
  `customer_email` is a plain `EmailField` (`backend/booking/serializers.py:260`)
  that only trims whitespace, and `get_or_create_guest_customer` stores it
  as typed (`backend/accounts/services.py:40-41`). Customers that differ
  only by letter case can therefore exist in one salon, since
  `customer_salon_email_uniq` is case-sensitive. A fix needs normalization
  at write time plus a data migration that checks for case collisions
  first. Not part of item 8, which works around it with a case-insensitive
  "taken" check (see "Item 8 decisions (change email)").
- Recorded 02.10.2026: the registration verification token
  (`backend/accounts/tokens.py`) carries no `salon_id` and relies only on
  the tenant-scoped `Account` lookup, like the JWT, which has no salon
  claim either. The email-change token got a second guard, a `salon_id`
  compared with the current salon (see "Item 8 decisions (change email)",
  cycle 2 additions); consider the same guard for these later.
- Recorded 16.10.2026: the verification banner (see "Item 13 decisions
  (verification reminder)") currently renders above each `/client` page's
  own header (title, avatar, back links) at full width, because there is
  no shared header yet. In the design pass it goes below the shared
  header, with the width and type size from the design system, as on the
  Item 13 mockups. Found in Katya's browser check of 16.10.2026.
- Recorded 17.10.2026: `no_show` cards have no badge and no money line; to
  decide later.
- Recorded 19.10.2026, for the stage that wires a real payment provider:
  in production, refuse to start with `MockPaymentProvider`. Its webhook
  accepts any signature, so anyone could confirm a booking without paying.
  Add a startup check.
- Recorded 20.10.2026: where to send the client after logout (login page,
  salon home or `/services`) is decided in the design pass, together with
  the salon home page. Today: both "Вийти" buttons (the avatar menu,
  `client/ClientAvatarMenu.tsx`, and the profile page,
  `client/profile/page.tsx`) ask for confirmation, call `logout()` and then
  `router.replace("/login")`: plain `/login`, no notice, no saved return
  path (`logout()` clears it). The password-change and password-reset
  forms also end the session, but go to `/login?notice=password_changed`.
- Recorded 20.10.2026: `/login` renders the form for an already
  authenticated user, which looks like a logout; consider redirecting
  authenticated users (option E, 20.10.2026). Found in manual testing via
  the forgot-password back link, see "Item 11 decisions (forgot
  password)", point 8.
- Recorded 20.10.2026: login notice ('Пароль змінено...') is read on the
  client and pops in after hydration, shifting the form down. The
  useSyncExternalStore precedent cited in Item 9 point 3 applies to URL
  hashes, not query strings; consider reading notice server-side together
  with option E in the design pass (20.10.2026).
- Recorded 20.10.2026, for Stage 21: if someone changes both the password
  and the account email (item 8), reset links go to their mailbox; the
  item 8 notice tells the owner to contact the salon, but salon staff have
  no tool to restore the account (20.10.2026).

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
- **UI chrome i18n — belongs to Stages 18-21** (clarification of
  20.09.2026 under "Agreed stage order"; the exact scope is decided when
  Stage 18 starts). Static
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
  admin/staff interface language.
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
- **The AI assistant only proposes a service, optionally a specialist and
  a slot; it never creates a binding appointment.** The user must confirm
  through the normal booking flow — a hallucinated parameter must never
  produce a real paid booking.
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

  **Correction, recorded 15.10.2026:** for password-reset-request this is
  not what the code does. `PasswordResetRequestView` queues the reset task
  only when the account exists, so that step is not timing-identical. See
  § "Known issue: password-reset request is not timing-identical".
- **DRF throttling needs a shared cache.** `LocMemCache` is per-process, so
  behind N workers the limit silently becomes `rate × N`. Uses
  `RedisCache` on Redis **DB 2** (broker DB 0, result backend DB 1) so
  throttle keys never collide with Celery's. DB 3 holds the AI
  assistant's chat sessions (`ASSISTANT_REDIS_URL`, Stage 16; see
  "Sub-step 5 details").
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
- `tenants.Salon.assistant_starters` — added in Stage 16 (§ "Stage 16
  planning", "Sub-step 1 details"). A list of translation dicts, not a
  single dict: each item is `{lang_code: string}`.
- `specialists.Specialist.name`
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
- The service card's click target navigated straight to
  `/booking?entry=service&service=<id>` (confirmed against
  `booking/page.tsx`'s `entry`/`service` param handling) — clicking the
  card booked the service directly, rather than opening the popup. A
  separate "i" (info) button opens `ServiceInfoPopover` instead.
  Superseded 14.09.2026: the service grid is select-then-confirm now
  (a radio per card, then "Продовжити" navigates to
  `/booking?entry=service&service=<id>`); see "Service selection:
  select-then-confirm interaction pattern" below.
- Card HTML structure: the card is a relatively-positioned container;
  the `Link` inside it uses the "stretched link" pattern (`absolute
  inset-0`, covering the full card) for its click target, rather than
  wrapping the whole card in an `<a>`. The "i" button is a sibling of
  the `Link` (not nested inside it), given a higher `z-index` so it
  remains clickable above the stretched link. An interactive `<button>`
  nested inside an `<a>` is invalid HTML and produces unpredictable
  click behavior, which is why the stretched-link pattern is used
  instead of the naive "wrap everything in one link" approach.
  Superseded 14.09.2026: the service card has no `Link` now; see
  "Service selection: select-then-confirm interaction pattern" below.
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
- **Known issue, recorded 20.09.2026: "Далі →" has no upper bound.** The
  booking limit is `Salon.max_advance_days` (`backend/tenants/models.py`,
  default 60, enforced in `scheduling/services.py`), but the frontend
  never learns it — `AvailabilityView` returns only `available_times`,
  and the salon info endpoint (`getSalonInfoPage.ts`) returns only
  `currency`. Past the limit, every day looks identical to a fully
  booked one (the ambiguity noted in the bullet above), and "Далі →"
  keeps linking forward indefinitely. Not a dead end: the in-page "←
  Назад" link (`DateTimeSelectionGrid.tsx`'s previous-window control,
  added 20.09.2026) and the browser Back button both return the user to
  a real window. Not fixed now. The fix has two parts: (a) the salon
  info endpoint — deliberately narrow today — gains `max_advance_days`,
  which widens that contract and needs its own decision and test; (b)
  the frontend stops rendering "Далі →" once the next window would
  start after today + `max_advance_days`.
  Owner: the frontend design pass deferred to Stages 18-21 by the Stage
  12 closure decision (11.09.2026); the date selector may be reworked
  there. Trigger to do it earlier: any report of a user lost past the
  booking window.
- **Known issue, recorded 20.09.2026: `todayIsoDate()` uses the UTC
  date, not the salon's local date.** Same convention this step already
  used before today's `minDateFrom`/"← Назад" addition — not introduced
  by it, not fixed by it. Near midnight (salon-local), the initial
  window start and the "← Назад" floor can be one day off from the
  salon's actual "today."
- **Read-only check, recorded 20.09.2026: a `date_from` in the past is a
  valid date, not an error.** `AvailabilityQuerySerializer.date_from` is
  a bare `DateField` with no past-date/min-value validation, and
  `compute_candidate_start_times`'s only range check
  (`scheduling/services.py:206-209`) is `date_from > date_to`, which a
  past `date_from` doesn't trigger. Confirmed against
  `test_filter_candidates_by_booking_window_candidate_before_lower_bound_excluded`
  (`backend/tests/test_scheduling_availability.py:450`): a candidate
  before `lower_bound` (`now + min_lead_time_hours`) is silently
  excluded, not rejected. So a past-`date_from` request returns 200 with
  an empty (or partially empty) `available_times` — the same "every day
  disabled, no reason given" ambiguity as the bullet above, not a
  distinct error path.
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
     name/phone. Left as a known issue, not addressed here. Fixed
     29.09.2026 for linked Customers, see "Guest booking keeps a linked
     Customer's name and phone".

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

   Corrected 20.09.2026 — the "explicit parameter passed down from the
   booking-creation call site" above undersold what actually landed
   (Cycle C):

   - The mode is passed to `record_and_dispatch_notification` as a
     keyword-only `booking_link_mode: str | None = None`, **and**
     persisted on the row as `Notification.booking_link_mode` (nullable,
     choices `guest`/`account`). Reason: the email is built later, in
     `_build_message`, inside a Celery task that may run in a different
     process — a call-site argument alone can't reach it there, so the
     validated value has to be written to the row for that later,
     out-of-process read to find.
   - For `BOOKING_CREATED`, a missing (`None`) or unknown mode raises
     `ValueError`, both at record time (before any DB write) and again
     in `_build_message` — explicit checks in both places, no
     fall-through to the guest link. Every other trigger doesn't use
     the field at all (stays `NULL`).
   - Migration `notifications.0005` adds the field and backfills every
     existing `booking_created` row to `"guest"` (every one predates
     this field and was recorded by `create_guest_appointment`, the
     only call site that has ever dispatched that trigger).
   - Cycle B's account-booking path must pass
     `booking_link_mode="account"` at its own call site when it's
     built — not yet done, since Cycle B's endpoint doesn't exist yet.

   Cycle B — account booking endpoint contract, decided 20.09.2026:

   - Endpoint: `POST appointments/`, same URL family and tenant
     resolution as `appointments/mine/` (`GET`) and
     `appointments/<id>/cancel/` (`POST`) (`backend/booking/urls.py`),
     authenticated via the project-wide `AccountJWTCookieAuthentication`
     + `IsAuthenticated` defaults — no explicit
     `authentication_classes`/`permission_classes` override, same
     posture as `AccountAppointmentListView`/`AccountAppointmentCancelView`.
   - Request: the same fields as `GuestBookingRequestSerializer`
     (`specialist` — a real pk or `"any"` — `service`, `start_datetime`)
     but no `customer_email` field: the Customer's email always comes
     from `request.user.email`, never the payload, per the Refined
     19.09.2026 decision above. `customer_name`/`customer_phone` are
     required only when `account.customer_id` is `None` (the
     new-Customer sub-case); when a Customer is already linked, both
     are ignored if sent — the linked Customer's existing `name`/`phone`
     are never overwritten by this endpoint.
   - Response: 201, `{"appointment": <AppointmentCreatedSerializer
     data>}` — the same nested shape and fields
     (`id`/`status`/`start_datetime`/`end_datetime`) as the guest
     endpoint's 201 body, minus the `guest_token` key (no guest token is
     issued for an account booking, per the Refined 19.09.2026 decision
     above).
   - Errors:
     - Unauthenticated: 401, same global default as every other account
       endpoint — no view-level check needed.
     - Unverified Account (`email_verified_at is None`): 403 with a
       stable, machine-readable `code: "email_not_verified"` body,
       identical whether or not a Customer with that email already
       exists in the salon — same anti-enumeration reasoning as the
       Refined 19.09.2026 decision's `customer_salon_email_uniq` point;
       a distinct response for the two cases would itself leak whether
       the email belongs to an existing Customer.
     - Missing `customer_name`/`customer_phone` in the unlinked-Customer
       case: 400 with the normal DRF field-error body.
     - Slot/domain errors (`SlotNotOfferedError` -> 400,
       `SlotUnavailableError` -> 409): the exact same mapping as
       `GuestBookingCreateView`, via the same
       `core.exceptions.exception_handler` — reused, not reimplemented,
       since both views wrap the same `create_appointment`/
       `create_appointment_for_any_specialist` core.
     - Throttling: none. `REST_FRAMEWORK["DEFAULT_THROTTLE_CLASSES"]`
       (`backend/config/settings/base.py:167`) globally installs
       `ScopedRateThrottle`, but `ScopedRateThrottle.allow_request`
       returns `True` unconditionally for any view with no
       `throttle_scope` attribute set — the global class alone throttles
       nothing. `GuestBookingCreateView`
       (`backend/booking/views.py:302-348`) declares no
       `throttle_scope`, unlike the guest-token views
       (`GuestAppointmentDetailView`/`GuestAppointmentCancelView`/
       `GuestAppointmentPayView`, all `throttle_scope = "guest_token"`,
       rate `"20/min"` per `DEFAULT_THROTTLE_RATES`
       (`backend/config/settings/base.py:176-182`)). So the guest
       booking-creation endpoint is unthrottled today, and the new
       account endpoint matches: also unthrottled.
   - Order of checks in the view, in this exact sequence: (1)
     authentication (`AccountJWTCookieAuthentication` + the global
     `IsAuthenticated`, -> 401), (2) the unverified-Account check (->
     403 `email_not_verified`), (3) request-body validation (->
     400), (4) the `transaction.atomic()` that resolves/links the
     Customer and creates the appointment. The 403 check must run
     before body validation — never folded into a serializer
     `validate()` — so that a validation error (e.g. missing
     `customer_name`) never differs depending on whether the Account is
     verified or on whether a Customer exists for its email; an
     unverified Account always gets the same 403 regardless of what
     else is wrong with the request body.
   - Dependency: this item's whole safety argument (Refined 19.09.2026
     above — linking an unproven email to an existing Customer would
     expose that Customer's history) rests on the invariant
     "`email_verified_at` set implies the Account's current email is
     proven." That invariant is not self-maintaining — see item 8 below,
     which must reset `email_verified_at` to `None` and require
     re-verification whenever the Account's email changes, or a changed,
     unverified email would keep reading as verified here.
   - Known issue, not fixed by this item: no rate limiting on booking
     creation, guest or account endpoint (see the Throttling point
     above) — a 15-minute slot hold (`create_appointment`'s PENDING
     window) can be created without limit by either flow. Not addressed
     here.
   - Concurrency: the `Account` row is locked with `select_for_update()`
     inside the same `transaction.atomic()` that resolves/links the
     Customer and creates the appointment (already decided, Refined
     19.09.2026 above) — restated here as this item's explicit
     concurrency contract: two simultaneous first bookings from the same
     Account converge on one Customer (the second request's
     `select_for_update()` blocks until the first commits, then sees
     `account.customer_id` already set and reuses it, never racing
     `get_or_create` into two Customers); a second booking for the same
     slot still gets the normal slot-conflict error
     (`SlotUnavailableError` -> 409) from the shared `create_appointment`
     core, unaffected by the Account-row lock.
   - Out of scope for this cycle: the `BOOKING_CREATED` notification
     link for account bookings is Cycle C's scope, not this endpoint's.

   Recon checks done before writing this contract:
   - `backend/booking/urls.py`: confirmed the `appointments/mine/` /
     `appointments/<id>/cancel/` URL family and naming convention.
   - `GuestBookingRequestSerializer` (`backend/booking/serializers.py:208-236`):
     confirmed its exact field set (`specialist`, `service`,
     `start_datetime`, `customer_name`, `customer_email`,
     `customer_phone`) and that `specialist`/`service` are
     `PrimaryKeyRelatedField`s rebound to tenant-scoped querysets in
     `__init__`.
   - `GuestBookingCreateView` (`backend/booking/views.py:302-348`):
     confirmed it has no view-level try/except — `SlotNotOfferedError`/
     `SlotUnavailableError` propagate to `core.exceptions.exception_handler`
     — and confirmed it declares no `throttle_classes`.

   Cycle D — frontend account-aware booking (step 4), decided 20.09.2026:

   - Structure: `booking/page.tsx`'s step-4 branch fetches the summary
     data server-side (`Promise.all`) and renders a new client wrapper
     component; the wrapper renders a shared `BookingSummary` above one
     of two forms. The existing `ContactInfoForm` stays the guest form;
     its only change is the button label (see below).
     `BookingContactInfoContext` is unchanged.
   - Summary data (both flows): service name and duration via a new
     frontend helper `getServiceDetailPage`, mirroring
     `getSpecialistDetailPage` (`GET /services/<id>/`, `null` on 404),
     always fetched; specialist name via `getSpecialistDetailPage` when
     `specialist !== "any"`; for `"any"` the summary shows "Будь-який
     спеціаліст". No backend change — `GET /services/<id>/`
     (`ServiceDetailView`) already exists. Steps 2/3's data cannot be
     reused because step 4 is a separate Server Component render with no
     state carried over from earlier steps, so step 4 makes its own 1-2
     requests.
   - Slot formatting: one pure helper that takes the date and time from
     the ISO string itself (the offset already carries the salon's wall
     time), never `new Date(iso)` in the browser's timezone; the date is
     formatted via `Date.UTC` from the `YYYY-MM-DD` part with
     `timeZone: "UTC"`. Precedent: `DateTimeSelectionGrid`'s
     `slot.slice(11, 16)`.
   - States: while `AuthContext` is loading, render nothing (precedent:
     `client/layout.tsx`); no session, role `"admin"`, or
     `email_verified` false -> `BookingSummary` + the existing guest
     form; role `"client"` + `email_verified` true + Customer linked ->
     summary + a line with the account email + confirm button, no
     fields; role `"client"` + verified + no Customer -> summary + email
     read-only + name and phone fields (values kept in
     `BookingContactInfoContext` across the step 4 -> 3 -> 4 round
     trip). Role `"admin"` gets the guest form because whether salon
     staff may book as customers was never decided; the backend does not
     restrict role on `POST appointments/` (open question, not changed
     here).
   - Button label: "Підтвердити запис" in both forms. The guest form's
     current label is "Забронювати" (`ContactInfoForm.tsx:140`); it is
     changed in its own small commit before the rest of cycle D,
     updating only the tests that match on the old label text, no other
     assertion.
   - Account-flow copy: the account email is shown read-only ("Email
     береться з вашого облікового запису"); a line under the button:
     "Місце тримається 15 хвилин. Оплатити запис можна в особистому
     кабінеті." It appears in the account flow only, because a guest
     goes straight to `/booking/pay`. No deposit amount is shown (no
     Payment row exists before payment starts).
   - "Customer linked" is derived from `me.name !== null` (`MeSerializer`
     returns `null` only when no Customer is linked), in exactly one
     helper function.
   - `Me` type gains `email_verified: boolean`; existing `Me` fixtures
     updated. This is a frontend-only correction, not a new backend
     field: `MeSerializer` already returns `email_verified`
     (`backend/accounts/serializers.py:94,98,110-111`, landed § Extended
     19.09.2026 above) — the frontend `Me` type
     (`frontend/src/lib/auth/Me.ts`) was simply never updated to include
     it. This item's own earlier recon (planning this cycle) stated
     `/me` "lacks" `email_verified`; that was true of the frontend type
     only, never the backend contract — corrected here.
   - Submit uses a new helper `createAccountBooking` built on
     `apiRequest` (cookie credentials + CSRF), not the credential-less
     `createGuestBooking`. Payload mirrors the guest one minus
     `customer_email`; `name`/`phone` are sent only in the unlinked
     case. Errors: 409 `SLOT_NO_LONGER_AVAILABLE` -> back to step 3 with
     context intact (same as guest); 403 `email_not_verified` -> inline
     message; anything else -> generic inline message, no navigation.
   - Success: `AuthContext` gets a new `refresh` function that re-fetches
     `auth/me/`, called after a successful booking and before redirecting
     to `/client` (`me` is fetched once on mount, so without it a stale
     `me` would keep showing the name/phone form and an empty profile).
   - Dependency: an account booking lives in `pending_payment` for 15
     minutes and can only be paid via item 14, so cycle D must not reach
     production without item 14.
   - Known issue, not fixed here: `client/page.tsx`'s `formatDateTime`
     uses `new Date(iso).toLocaleString` in the browser's timezone, so a
     client outside the salon's timezone sees a different wall time than
     the salon's. Fixed by § "Appointment times in the salon's time zone"
     (16.10.2026).

   Recon checks done before writing this cycle:
   - `ServiceDetailView.get_queryset()` (`backend/catalog/views.py:155-156`)
     builds on `Service.objects` (the tenant-scoped default manager, not
     `unscoped_objects`), so a service id belonging to another salon is
     simply absent from the queryset and
     `RetrieveUpdateDestroyAPIView`'s `get_object()` raises a plain 404
     — correct behavior, but no existing test in
     `backend/tests/test_catalog_api.py` exercises a cross-salon GET on
     `services/<id>/` specifically (its cross-salon coverage is
     `test_cross_salon_staff_include_inactive_gets_ordinary_public_result`
     and `test_posting_a_category_id_from_another_salon_returns_400`,
     neither of which is this case).
   - `booking/page.tsx` resolves every invalid/unresolvable param the
     same way: an invalid `entry`, a missing required id, `step === 1`,
     a non-numeric service/specialist id, or `getSpecialistDetailPage`
     returning `null` (§ step 2, entry=specialist) all call Next.js
     `notFound()` (a 404 page) — the one different case is a missing
     `SALON_SLUG_HEADER`, which renders a "platform still in
     development" placeholder instead, unrelated to param validity. The
     new summary fetch must follow the same convention: a `null` from
     `getServiceDetailPage`/`getSpecialistDetailPage` in step 4 must
     also call `notFound()`, matching every existing step's precedent.
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
   inventing a new one, if it fits (recon first to confirm); (b) keep
   the old email valid for login until the new one is verified — an
   in-flight, unverified email change must never lock the account
   holder out; and (c) preserve the invariant `email_verified_at`
   non-`None` only while the Account's *current* email is proven — set
   it at the moment the new email's own verification succeeds and
   becomes active per (a); if any implementation instead swaps the
   email before verification, it must reset `email_verified_at` to
   `None` in that same transaction. Item 5's account-aware booking (§
   Stage 15 planning, item 5, Cycle B) relies on this invariant.
   Surfaced in the profile panel (item 7).

   Note, recorded before implementation: `Account.email` and
   `Customer.email` are two separate columns that happen to be equal
   today only because nothing changes `Account.email` yet
   (`create_account_appointment` derives the Customer from
   `account.email` at booking time, per item 5's Cycle B). Once this
   item lets an Account change its email, the two columns diverge — the
   Customer keeps the old address until something else updates it,
   which nothing in this codebase does today.

   Consequence this item must decide *before* implementation (recon the
   actual current behavior first, don't assume): a later Account
   registered and verified with that same old address would, in
   `create_account_appointment`, `get_or_create` the Customer by
   `(salon, account.email)` and find the Customer already linked to the
   first Account. `Account.customer`'s `OneToOneField` uniqueness
   constraint then raises `IntegrityError` on the attempted relink,
   which is not translated by `core.exceptions.exception_handler` and
   surfaces as a bare 500. The outer `transaction.atomic()` rolls back,
   so no data leaks or gets overwritten — but the requesting user still
   sees a 500, not a clean error.

   This item's decision must cover: (a) whether `Customer.email` follows
   the Account's email change (keeping the two columns in sync going
   forward), and (b) what `create_account_appointment` should do when
   the Customer it finds is already linked to a *different* Account — a
   neutral, generic error, never one that reveals to the caller that
   another Account exists for that email (same anti-enumeration
   reasoning as the Refined 19.09.2026 entry's `email_not_verified`
   response).

   Not reachable today — `Account.email` cannot change by any existing
   path — so nothing is fixed in item 5 for this; it is this item's scope
   once it lands.

   **Superseded 20.10.2026 by "Account linked to a Customer held by
   another Account" below.** Kept for history. What was wrong in the
   "bare 500" paragraph above, found in the recon of 20.10.2026:
   - The `IntegrityError` *is* translated. Its cause is a
     `UniqueViolation`, so `core.exceptions.exception_handler` answers
     400 `unique_violation`, not 500. "No data leaks or gets overwritten"
     was correct.
   - The trigger it describes no longer leads to a relink. Item 8's
     confirm step changes the linked Customer's email together with the
     Account's, so a later Account registered with the old address gets
     its own Customer.
   - It names only booking. Verification (`_verify_and_link`) fails the
     same way, and its rollback also undoes `email_verified_at`, so the
     second Account can never verify.
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

    **Correction, recorded 15.10.2026:** not only the frontend is missing.
    The item 11 recon of 15.10.2026 found backend gaps (the reset email,
    the confirm step's check order, row lock and cookies). See § "Item 11
    decisions (forgot password)".
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

    Item 14 design details, decided 24.09.2026. Nothing is implemented
    yet.

    - Pay endpoint: `AccountAppointmentPayView`,
      `POST appointments/<id>/pay/`, calling
      `payments.services.initiate_payment`. Ownership is resolved exactly
      as in `AccountAppointmentCancelView`: 404 when the appointment is
      not the caller's linked Customer's, including an appointment in
      another salon. No role check and no verification check, same as
      cancel. Response identical to guest pay: `{payment, provider_data}`,
      201 when a Payment is created, 200 when an existing PENDING one is
      reused. No throttling, same as cancel. Cross-salon 404 tests are
      added for both pay and cancel (cancel has none today).
    - Money fix, landing before the dashboard button and applying to both
      the guest and the account paths: a `payment_succeeded` webhook on a
      CANCELLED appointment triggers a full refund (as the EXPIRED branch
      already does in `PaymentWebhookView`) and does not confirm the
      appointment. Full, not the <24h deposit-forfeit rule, because the
      appointment was cancelled before the payment was ever confirmed. A
      repeated webhook must not refund twice. Reason: `cancel_appointment`
      refunds only a SUCCEEDED Payment, so pay, then cancel, then a
      successful webhook currently leaves a SUCCEEDED Payment on a
      CANCELLED appointment with no refund.
    - Frontend: the "Оплатити" button lives on the `/client` card of a
      `pending_payment` appointment and calls the new endpoint via
      `apiRequest`. It does not use `/booking/pay`, which stays
      guest-token-only. After the call the card list is refetched; with
      `provider_data` the card shows the payment link, otherwise
      "Очікуємо підтвердження оплати". A hold countdown is deferred.
    - Order: P1 money fix, P2 pay endpoint + cross-salon tests, P3
      dashboard button.
    - Still open, not decided here: whether a salon admin may book or pay
      as a customer (Cycle D's open question above). If it is later
      decided "no", the role check goes into both `POST appointments/` and
      `POST appointments/<id>/pay/` together.
    - Cancel confirmation, decided 27.09.2026: `appointments/mine/`
      gains a read-only `refund_deadline` (start minus the refund cutoff,
      from `booking.services.refund_deadline`, the same rule
      `cancel_appointment` uses; null without a SUCCEEDED payment or once
      the appointment can no longer be cancelled). A deadline, not a
      boolean, because the cancel rule is evaluated at cancel time: the
      dashboard compares it with the clock at click time to word its
      confirm dialog, and the backend still makes the final refund
      decision.
    - Known issues, not fixed here:
      - `initiate_payment` does not check `hold_expires_at`, so between
        hold expiry and the next expiry sweep (up to 60s) pay still works.
        The slot stays blocked meanwhile, and a success that lands after
        the sweep is refunded.
      - `WayForPayProvider` stays unwired; `MockPaymentProvider` remains
        the default `provider_class` everywhere.
      - The Django admin Account add form can link a Customer from
        another salon (`Customer.unscoped_objects.all()`, no same-salon
        check). Operator-only; a separate task.
      - An access token carries no salon claim. Cross-salon rejection
        (a salon-A token sent to salon B's URL gets 401) relies only on
        the tenant-filtered Account lookup in
        `AccountJWTAuthentication.get_user`. A salon claim is a possible
        second layer, not done here.

Explicitly out of scope for Stage 15:

- **Any "claim a specific guest booking via its guest token while logged
  in" mechanic.** Not needed: the existing email-verification merge
  (Stage 3-R, `accounts/views.py`'s `_verify_and_link`) already links a
  guest `Customer` to an `Account` by matching email at verification
  time, covering the guest→Account linking need without a separate claim
  flow.

### Stage 15 closure checklist

Recorded 19.10.2026. What remains before Stage 15 is marked done, in this
order.

1. **Price and prepayment on dashboard cards.** Done 19.10.2026:
   implemented, browser-checked, "implemented" note added.
2. **Item 8 relink.** Decide what `create_account_appointment` does when
   the Customer it finds is linked to a different Account, add a test, and
   correct the "bare 500" note. Done 20.10.2026: decided, implemented and
   tested, the note marked superseded; see "Account linked to a Customer
   held by another Account".
3. **"Нове бронювання" link** on a non-empty client dashboard. Done
   20.10.2026: see "New booking button on the client dashboard".
4. **Logout target.** Deferred to the design stage; to be recorded in the
   deferred list under "Agreed stage order". Done 20.10.2026: recorded in
   the deferred list.
5. **Cleanup.** Done 20.10.2026. Covers:
   - "Known issue: `tsc --noEmit` does not type-check the project";
   - "Known issue: ESLint `react-hooks/set-state-in-effect` in the client
     dashboard";
   - the two remaining `tsc` errors in test files,
     `src/app/register/page.test.tsx` and
     `src/app/specialists/[id]/page.test.tsx`;
   - `ruff format` for `config/settings/development.py` and
     `notifications/migrations/0005_notification_booking_link_mode.py`.

   What was done:
   - the `typecheck` npm script (`tsc --noEmit --incremental false`) and
     the CLAUDE.md note, see the resolved tsc known issue;
   - the two test type errors: a type annotation on `fillForm`'s options in
     `register/page.test.tsx`, and `services_detail: []` in the
     `specialist()` fixture defaults in `specialists/[id]/page.test.tsx`;
   - the ESLint fix in `client/page.tsx`, see the resolved ESLint known
     issue;
   - `ruff format` of the two backend files (line joins only).
6. **Mark Stage 15 done.** Done 20.10.2026: marked ✓ in "Agreed stage
   order".

Found during Stage 15, fixed:

- "Persisted refund decision (`Payment.refund_due`)".
- "Refund retry sweep".

Found during Stage 15, deferred:

- "Known issue: `initiate_payment` writes the Payment outside its lock".
- "Known issue: the `flag_stuck_refunds` race test uses a savepoint".
- "Known issue: `Salon.deposit_percentage` is unbounded", together with the
  0% deposit booking flow.
- `formatPrice` shows ISO codes for some currencies ("24,00 USD", "24,00 EUR") 
 but a symbol for UAH ("₴"), and always ",00" for whole amounts. 
 Decide symbols and whole-amount format in the design stage.

**Stage 15 summary.** Done 20.10.2026.

- Built: all 14 scope items in § Stage 15 planning, from the account
  appointments endpoint to the "Оплатити" action on the dashboard.
- Found and fixed during the stage: "Appointment times in the salon's time
  zone", "Persisted refund decision (`Payment.refund_due`)", "Refund retry
  sweep", "Account linked to a Customer held by another Account", and a
  reliable frontend type-check (`npm run typecheck`, checklist item 5).
- Carried over: the deferred list under "Agreed stage order", the "Found
  during Stage 15, deferred" list above, and the open "Known issue" entries.

### Session renewal and session lifetime, decided 21.09.2026

Agreed in discussion after manual testing with a real backend. Extends
"Cookie mechanism resolved" (§ Stage 12) and Stage 15 planning item 12
(route protection); it does not edit either. The token lifetimes (15 min
access, 7 day rotating refresh with blacklist-after-rotation) were not
recorded in this file before (only in `config/settings/base.py`), so this
entry is their first record here.

Why: found in manual testing.

- The access token lives 15 minutes and the frontend never calls
  `auth/refresh/`. After 15 minutes `/client` shows a generic load error,
  and the next reload bounces the user to `/login`.
- Both auth cookies are session cookies (no `max-age`), so the "7 day
  refresh token" holds only while the browser stays open.
- `LogoutView` authenticates first. With an expired access token it answers
  401, clears no cookies and does not blacklist the refresh token, which
  stays alive server-side. Any silent renewal would then resurrect a
  logged-out user.

Decisions:

1. **Session lifetime.** The `refresh_token` cookie gets `max-age` equal to
   `REFRESH_TOKEN_LIFETIME` (7 days) and the `access_token` cookie gets
   `max-age` equal to `ACCESS_TOKEN_LIFETIME` (15 minutes). Both are re-set
   on every refresh. Tradeoff accepted: on a shared computer a person stays
   signed in for up to 7 days unless they log out, so logout must really
   work (item 3). A "remember me" checkbox is out of scope.
2. **Presence hint cookie.** The backend sets a third cookie next to the two
   auth cookies. It is NOT httpOnly, has the same `max-age` as the refresh
   cookie, holds no secret (a constant value such as `"1"`), and is cleared
   together with the others on logout. Attributes: `Path=/` (so the
   frontend can read it on every page), host-only like the other cookies,
   and `SameSite` and `Secure` equal to the access cookie's settings. The
   frontend reads it from
   `document.cookie`, as it already reads `csrftoken`, to decide whether a
   silent refresh is worth trying. The backend never trusts it and it is
   never used for authorization, so it does not conflict with "never in
   JS-readable state" in § Authenticated session transport (that rule is
   about tokens). Reason: most visitors are guests with no refresh cookie,
   so without the hint every guest page load would cost a guaranteed failing
   refresh request and log noise. If a refresh fails, the frontend deletes
   the hint cookie itself.
3. **Logout.** `LogoutView` no longer requires a valid access token. It
   always clears all cookies and, when a refresh token is present,
   blacklists it, answering 205 either way. A refresh token that is present
   but invalid, expired or already blacklisted is ignored: the cookies are
   still cleared and the answer is still 205. It stays CSRF protected (cookie
   plus header, § CSRF protection resolved). This deliberately replaces the
   earlier requirement that logout authenticates first, which was never a
   recorded decision: it came from the project-default authentication, and
   the `accounts/views.py` module docstring describes it as deliberate
   ("needs a valid access token to blacklist a refresh token", citing a
   Stage 3 entry that does not exist in this file). That docstring is
   updated in the same change as the code. The test
   `test_logout_with_invalid_access_cookie_is_still_401` (written
   21.09.2026 as an over-fix guard) has its expectation changed
   accordingly.
4. **Renewal is reactive, inside `apiRequest`.** On a 401 from a credentialed
   request, run one refresh and retry the original request once. Safe for
   unsafe methods too, because a 401 from authentication means the action
   never ran. Never retried: `auth/login`, `auth/refresh`, `auth/logout`,
   `auth/csrf`. The initial `auth/me` on mount goes through the same path,
   so a returning user after 15+ minutes is silently signed back in, but
   only when the hint cookie is present.
5. **One refresh in flight per tab**, shared by all requests waiting on a
   401. Cross-tab races are accepted (see known issues).
6. **When refresh fails.** Only a definitive 401 from `auth/refresh` means
   "session lost". A network error, a timeout or any 5xx from the refresh
   request must NOT log the user out: `me` and the hint cookie stay, and the
   original request fails with its own error, so a later attempt can
   succeed. If the refresh succeeded and the retried original request
   answers 401 again, that also means session lost (the fresh token was just
   rejected, for example a deactivated account); it is never retried a
   second time. On session lost, `me` is cleared through a window event that
   `AuthProvider` listens to, and the hint cookie is deleted. `/client`
   redirects to `/login` carrying the current path in a `next` query
   parameter, and after a successful login the user is sent to `next`.
   `next` is accepted only as a same-site path: it must start with a single
   `/`, must not start with `//`, must not contain a backslash or a scheme,
   and must not point at `/login` itself. Anything else falls back to
   today's default (`/client`, or `/admin` for staff). The account booking
   form simply degrades to the guest form when `me` becomes null. The
   return-path mechanism in this item is superseded by "S3 design details"
   (24.09.2026).
7. **Guests never call refresh** (no hint cookie, no request).
8. **No throttle scope is added to refresh** in this change.
9. **Order of work**, each cycle with its own tests and commit:
   S1 backend (cookie lifetimes, hint cookie, `LogoutView`; includes a test
   proving that a refresh issues a new refresh token whose expiry is about
   7 days from now, not inherited from the old token, and that all three
   cookies get fresh `max-age` values); S2 frontend
   (`apiRequest` retry and single-flight, mount check, session-expired
   event); S3 (login `next` parameter and redirects). Item 14 ("Оплатити")
   starts after S3, since a payment flow needs a session that does not die
   mid-payment.

Known issues, recorded and not fixed here:

- Token rotation is not atomic. Two truly parallel refreshes with the same
  token both succeed (reproduced 21.09.2026), and a request sent after a
  rotation with the old token gets 401. Within one tab the single in-flight
  refresh avoids it; across tabs it is accepted (worst case: one re-login).
  The Web Locks API is a possible later fix.
- `auth/refresh` has no throttle.
- The session is sliding: every refresh issues a new refresh token with a
  fresh 7 day expiry, so an active user is never signed out and there is no
  absolute maximum session length. Accepted for a booking site; an absolute
  cap can be added later.
- The settings comment in `config/settings/base.py` (lines 186-194) and the
  `accounts/views.py` module docstring cite a Stage 3 decisions entry that
  does not exist; both are corrected to point at this entry in cycle S1.
- `apiRequest` throws a raw `SyntaxError` for error responses with an empty
  or non-JSON body (for example a 502 from a proxy). Harmless today because
  callers catch any error.
- The public auth views ignore a stale access cookie (fix committed
  21.09.2026), but the `ScopedRateThrottle` of register, password-reset
  request and resend-verification now keys on the IP instead of the user pk,
  even for a logged-in caller.

#### S2 design details

Decided 21.09.2026, in discussion, before cycle S2 starts. Nothing here is
implemented yet. It fills in items 4 to 7 above and refines item 6 in one
place (point 2: the "own error" of a request after an unsure renewal is a
distinct error type). Two backend facts it rests on were re-read in code on
the same day: a missing `csrftoken` cookie makes `enforce_csrf_on_unsafe`
raise `PermissionDenied`, which answers 403 (`CSRF Failed: CSRF cookie not
set.`); and `RefreshView` answers 401 for all four of: no refresh cookie,
expired token, blacklisted token, malformed token.

1. **Three outcomes.** A renewal attempt ends as `renewed`, `lost` (the
   refresh request answered 401) or `unsure` (network error, timeout, 403,
   5xx, anything else). Only `lost` deletes the hint cookie and dispatches
   the session-expired event.
2. **What the original request throws.** On `renewed` it is retried once. On
   `lost` the original 401 `ApiError` is rethrown. On `unsure` a distinct
   error is thrown that is not an `ApiError` with status 401, so
   `AuthContext` keeps `me` and the hint (today it treats any 401 as
   signed out, in the mount effect and in its `refresh()`).
3. **A 401 on the retry is final.** After a successful renewal, a 401 on the
   retried request is treated as `lost`. No second refresh.
4. **The hint gates only the renewal attempt.** It applies to every 401,
   including the mount `/auth/me/`. `/auth/me/` is still called on mount
   with or without the hint; without it, the 401 simply passes through
   unrenewed. (Item 4 above only says the mount check renews silently "when
   the hint cookie is present"; it does not say to skip the call.)
5. **Exclusion list.** Exactly `auth/login`, `auth/refresh`, `auth/logout`
   and `auth/csrf`, matched exactly against the salon-relative path passed
   to `apiRequest` (`"/auth/login/"` and so on). No prefix matching.
6. **Renewal fetch.** Renewal uses a lower-level fetch that returns the raw
   `Response`, not `apiRequest`, so it needs no recursion guard and can read
   the status without parsing the body (a non-JSON 401 or 5xx from a proxy
   must not become a `SyntaxError`). It has a 10 second `AbortSignal`
   timeout; a timeout is `unsure`. The timeout applies to the refresh
   request only, not to other requests.
7. **Single flight, per slug.** One renewal in flight per slug, held in a
   module-level `Map` keyed by slug. The shared promise never rejects,
   resolves to the outcome, and is cleared in `finally`. `apiRequest`
   itself still takes `slug` on every call; the `client.ts` module comment
   that says there is no module-level state is updated in S2 to say what
   module-level state now exists (this map, and nothing else).
8. **Late 401s accepted.** A request sent before a renewal finished can get
   its 401 after the shared promise was cleared and start a second,
   sequential refresh. That works (it uses the rotated cookie) and is
   accepted. No generation counter.
9. **Missing `csrftoken`.** No priming call before renewal. The refresh POST
   then gets 403, which is `unsure`. Known limitation: a user with a valid
   session but no `csrftoken` cookie (cleared selectively) is not renewed
   and ends up at `/login`, whose mount effect primes the cookie.
10. **Logout invariant.** After `logout()` resolves: no renewal is in
    flight, none can start, and `session_hint` is deleted client-side even
    if the logout POST failed. `logout()` waits for any in-flight renewal
    before sending the POST, so a late refresh cannot set the cookies again
    after logout cleared them. Errors are still swallowed as today.
11. **Where the code lives.** The event name, the hint cookie helpers and
    the renewal itself live outside `client.ts`, in their own module(s).
    Reason: many tests mock `@/lib/api/client` with a factory that returns
    only `apiRequest`, so anything else `AuthContext` imported from there
    would be undefined under those mocks. The function is named
    `renewSession`, not `refresh`, because `AuthContext.refresh` already
    means "re-fetch me". The session-expired event carries no payload.
12. **Known, not fixed in S2.** The login page's `void apiRequest(slug,
    "/auth/csrf/")` in its mount effect has no `.catch`, so a network
    failure is an unhandled rejection; this goes to S3, which edits that
    page. `client.ts` reads `NEXT_PUBLIC_API_PORT` at import time, so
    `client.test.ts` (which sets it in `beforeEach`, after the import)
    builds `http://localhost:undefined/...` URLs; harmless while `fetch` is
    mocked, and any new test that asserts a URL has to account for it.
13. **Note for S3.** A deliberate logout must not add `?next=` to the
    `/login` redirect. `ClientAvatarMenu.handleLogout` and the `/client`
    layout effect both call `router.replace`; S3 must not rely on which one
    runs first.

#### S3 design details

Decided 24.09.2026, in discussion, before cycle S3 starts. Nothing here is
implemented yet. It fills in the return-after-login part of item 6 above and
**replaces its mechanism**: item 6 said the path travels in a `next` query
parameter and is validated as a same-site path, with `/admin` as a valid
target for staff. S3 keeps the path in `sessionStorage` instead, and accepts
only `/client` paths for the client role. Item 9's "login `next` parameter"
and S2 point 13's "`?next=`" refer to the mechanism replaced here.

1. **Goal and scope.** After an involuntary session loss (the
   session-expired event, or a definitive 401 on mount or `refresh()`), the
   user returns to the `/client` page they were on once they log in again. A
   deliberate logout does not return them anywhere. Only the `/client`
   subtree is covered; no admin or specialist layout exists yet.
2. **Mechanism: `sessionStorage`, not `?next=`.** The return path is kept
   under one key, `salonhub:return-path` (same prefix as
   `SESSION_EXPIRED_EVENT`). Reason: a crafted link cannot plant it, so there
   is no open redirect surface; and `sessionStorage` is per origin, so per
   salon subdomain. Tradeoff accepted: it is also per tab, so logging in from
   another tab does not return the user.
3. **Where the code lives.** The helpers live in their own small module,
   `frontend/src/lib/auth/returnPath.ts`, not in `client.ts` (same mocking
   reason as S2 point 11): `saveReturnPath(path)` and `consumeReturnPath()`,
   which reads and removes the value in one step. Every `sessionStorage`
   access is wrapped in `try/catch`; a failure means "no return path", never
   a broken login.
4. **Signal.** `AuthContext` exposes a boolean state,
   `loggedOutDeliberately`. `logout()` sets it to `true` before
   `setMe(null)`, so any render in between has `me` present and the flag
   `true`, which the layout ignores; the order, not React's batching, keeps
   the layout from ever seeing `me === null` with a stale flag. `logout()`
   also clears the stored return path (through `consumeReturnPath()`,
   discarding the value). A successful `login()` resets the flag to `false`.
   No other path sets it; the mount effect, the session-expired listener and
   `refresh()` leave it as it is.
5. **`client/layout.tsx`.** On `!loading && me === null`: if not
   `loggedOutDeliberately`, save `window.location.pathname +
   window.location.search`, read inside the effect; then
   `router.replace("/login")` in both cases. The URL is always plain
   `/login`, so the double `router.replace` from `ClientAvatarMenu` and
   `client/profile/page.tsx` stays harmless, whichever runs first.
6. **`login/page.tsx`.** After a successful login, consume the return path
   (it is removed even if it is then rejected) and use it only if all of
   these hold:
   - `new URL(path, window.location.origin)` does not throw;
   - its `origin` equals `window.location.origin`;
   - its `pathname` is exactly `/client` or starts with `/client/`;
   - the logged-in user's role is `client`.

   Otherwise use today's role-based target (`/admin` or `/client`). The
   validation stays even though a link cannot set `sessionStorage`: defense
   in depth. Values such as `//evil.com`, `https://evil.com`, `/\evil.com`,
   `javascript:alert(1)` and `/clientx` must be rejected, each with a test.
7. **CSRF priming (S2 point 12).** Fixed in S3, since S3 edits that page.
   If priming fails, the `csrftoken` cookie stays absent and every login
   attempt gets 403 until a manual reload, so a `.catch` alone is not
   enough. The mount effect's `apiRequest(slug, "/auth/csrf/")` gets a
   `.catch` that ignores the error, and on submit, if the `csrftoken` cookie
   is absent (`readCookie` from `browserContext`), the page first awaits
   `GET auth/csrf/` before the login POST. If that call fails too, the page
   shows its existing generic error. Each case gets a test.
8. **Acceptance check.** Tests asserting an exact `/login` for a deliberate
   logout keep passing unchanged: `ClientAvatarMenu.test.tsx:116` and
   `client/profile/page.test.tsx:178`. The `/client` layout tests
   (`client/layout.test.tsx:37`, `:97`) also keep their exact `/login`
   assertion, since the URL no longer changes; they only gain assertions on
   what was saved.

### Slot-taken notice on step 3, decided 21.09.2026

Found in manual testing: when a slot is taken by someone else, both booking
forms send the user back to step 3 with no explanation, so it looks like a bug.

- The slot-gone branch of `ContactInfoForm` and `AccountBookingForm` redirects to
  `buildSlotTakenUrl` (next to `buildStep3Url`, which is unchanged): the step-3 URL
  plus `&notice=slot_taken`.
- `booking/page.tsx` step 3 sets `slotTaken` only when `notice` equals exactly
  `slot_taken`. Any other value is ignored (no `notFound`, no message) and the value
  is never rendered.
- `DateTimeSelectionGrid` takes an optional `slotTaken` (default false) and shows a
  `role="status"` line above the day strip. Its "Далі" and "Назад" links do not carry
  the notice, so it disappears once the user pages on.
- The four slot-gone tests (409 and 400 `SLOT_NOT_OFFERED`, both forms) had their
  expected URL changed on purpose to include the notice: a contract change.

### Shared password field design details

Decided 29.09.2026, in discussion, before items 9 and 11 start.

Implemented 29.09.2026 (points 1-4, 6, 7 and the original point 5), with an
explicit `required` prop instead of a spread of input props, so a caller can
never override the input's type.

1. **One component.** A shared `PasswordField` replaces the three existing
   password inputs (login password, register password, register
   confirm-password) and is used by item 9 (change password) and item 11
   (reset-password confirm). It lives in `frontend/src/app/PasswordField.tsx`,
   with its test next to it. Reason: components live next to the route that
   uses them, and the one module shared across routes, `AuthContext.tsx`,
   sits at the `app/` root; there is no `components/` directory, and `lib/`
   holds no components.
2. **Toggle.** A text button inside the field: "Показати" while hidden,
   "Сховати" while shown. `type="button"`, so it never submits the form. Its
   accessible name is the visible text: no `aria-label` and no `aria-pressed`,
   since the label change already conveys the state. `aria-controls` points
   to the input's `id`. Each field has its own independent toggle.
3. **Only the user controls visibility.** Once shown, the password stays
   shown until the user clicks "Сховати". No timers, no hiding on blur, on a
   validation error or on any other event. A freshly mounted field starts
   hidden.
4. **`autoComplete`** is passed through per field (`current-password` or
   `new-password`) and does not change when toggling.
5. **Optional `hint` prop**, rendered as a `<p>` under the input and linked
   through `aria-describedby`. The register page's "Щонайменше 8 символів"
   uses it. Changed and implemented 29.09.2026: the hint is rendered
   between the label and the input, not under the input, so it reads as an
   instruction rather than as feedback on what was typed. Still linked through
   `aria-describedby`. The register hint text becomes "Пароль має містити
   щонайменше 8 символів."
6. **Login label.** Changes from "Password" to "Пароль".
7. **Tests.** Clicking the toggle does not submit the form; the field stays
   shown after advancing fake timers; toggles on two fields are independent;
   the hint is linked through `aria-describedby`. Existing `getByLabelText`
   regexes on password fields are anchored so they match only the input.
8. **Open, to verify in a real browser.** Whether the browser's password
   manager still offers to save the password when the form is submitted
   while the field is shown. If it does not, the fix is restoring
   `type="password"` at submit time only, which does not change point 3.
   Checked 29.09.2026 in Chrome on the local dev host: no save-password prompt
   appeared either with the field shown or, as a control, with it hidden, so
   the check is inconclusive here. To redo on a real HTTPS domain.

### Remaining Stage 15 build order

Decided 29.09.2026, in discussion.

The remaining Stage 15 planning items are built in the order 10, 8, 9, 11,
not 8, 9, 10, 11. Item 10 depends only on item 7 and nothing depends on it.
9 stays before 11 because item 11's link lives on item 9's screen. The
"Scope, in build order" list in § Stage 15 planning is not edited; this
entry overrides it for these four items.

### Guest booking keeps a linked Customer's name and phone

Decided 29.09.2026, in discussion, before item 10 starts.

Implemented 29.09.2026.

1. **Rule.** In `get_or_create_guest_customer`
   (`backend/accounts/services.py`), when the existing Customer is linked to
   an Account (`Account.customer`, reverse accessor `customer.account`), a
   guest booking no longer updates its `name` or `phone`. An existing
   Customer with no linked Account is still overwritten as today, and a new
   Customer is created as today. The appointment is created in every case.
2. **Reason.** Once item 10 lets a user edit name/phone, any guest booking
   under their email would silently undo the edit. For a registered user the
   profile is the single source of truth. Accepted cost: the name/phone typed
   in the guest form for that booking are not stored anywhere, since
   appointments read them through the Customer FK with no snapshot, so the
   salon sees the profile values.
3. **Unchanged.** The claim-time overwrite in `create_account_appointment`'s
   unlinked branch (`backend/booking/services.py`) stays: there the account
   holder's own data correctly replaces guest data.
4. **Planned tests.** A linked Customer keeps its name/phone; an unlinked
   existing Customer is still overwritten; the appointment is created in
   both cases. Any existing test that contradicts point 1 is changed on
   purpose, as a contract change. Recon on 29.09.2026 found none: the two
   existing overwrite tests in `tests/test_booking_create_guest_appointment.py`
   use Customers with no linked Account. A fourth guard test was added: an
   Account in the same salon that is not linked to the Customer does not
   stop the overwrite, so the rule is "linked", not "has an account".
5. **Related known issue.** This addresses the Option A known issue recorded
   in § Stage 15 planning, item 5, "Refined 19.09.2026" ("Known issue, not
   fixed by this item: `get_or_create_guest_customer` ... unconditionally
   overwrites").

### Item 10 design details (edit name and phone)

Decided 29.09.2026, in discussion.

Backend implemented 29.09.2026 (points 1-5 and 8). Frontend implemented
30.09.2026 (point 6). Browser-checked by Katya; the no-linked-Customer
redirect was not checked by hand and relies on its mutation-tested unit
test. Two details beyond the points below: the save also bumps
`updated_at` (`update_fields` alone skips `auto_now` fields); and the lookup
goes through the tenant-scoped `Customer.objects`, so a Customer linked from
another salon (the known admin cross-salon link issue) gives a 404, not a
500.

1. **Endpoint.** `PATCH /api/v1/salons/<slug>/auth/me/customer/`, next to
   `GET /api/v1/salons/<slug>/auth/me/`. Separate from `auth/me/` so that
   email changes stay in item 8's verified flow.
2. **Customer resolution.** Only from `request.user` (its `customer_id`),
   never from the URL or body. No linked Customer: 404 with the same body as
   the cancel/pay precedent (`NotFound()`, not `get_object_or_404`). The edit
   pages are never shown to such a user, so this only guards direct
   requests.
3. **Body.** `name` and/or `phone`; if neither is present, 400. The
   serializer lists exactly `name` and `phone` (never `__all__` or
   `exclude`); any other field in the body (`email`, `salon`,
   `preferred_language`) is ignored, and a test pins that.
4. **Validation** matches booking: `name` max 255, `phone` max 32, both
   non-blank after DRF's whitespace trimming, no phone format rule.
5. **Response.** 200 with the updated `name` and `phone`.
6. **Frontend.** Separate pages `/client/profile/name` and
   `/client/profile/phone`, as linked from the profile, each with one field,
   a "Зберегти" button and a back link to the profile. On success:
   `refresh()` from `AuthContext`, then navigate to `/client/profile`. If the
   user has no linked Customer (`me.name === null`), the page redirects to
   `/client/profile`. Errors use `role="alert"`.

   Frontend details, decided 29.09.2026:

   - One shared component, `EditProfileField`, in `app/client/profile/`,
     used by two thin pages, `/client/profile/name` and
     `/client/profile/phone`, that only pass settings (field, title, label,
     input type, `maxLength`). Name: title "Змінити ім'я", label "Ім'я",
     type `text`, `maxLength` 255. Phone: title "Змінити телефон", label
     "Телефон", type `tel`, `maxLength` 32.
   - The input is prefilled with the current value and has `required` and
     `maxLength`.
   - Submit: PATCH with only that field, then `await refresh()`, then
     `router.push("/client/profile")`, the same order as
     `AccountBookingForm`. The button is disabled while the request is
     pending.
   - No linked Customer (`me.name === null`):
     `router.replace("/client/profile")` and render nothing.
   - Errors: fixed Ukrainian text in a `role="alert"` element, no
     navigation on error:
     - `TypeError` or `RenewalUnsureError`: "Не вдалося з'єднатися.
       Перевірте інтернет і спробуйте ще раз."
     - 401: "Сесія завершилась. Увійдіть знову." (with a live session
       hint, the layout already redirects to `/login` and saves the path).
     - 404: `router.replace("/client/profile")`.
     - Anything else, including 400: "Не вдалося зберегти. Спробуйте ще
       раз."
   - Back link "← Профіль" to `/client/profile`, written with `&larr;` like
     the profile page's own back link.
   - The stale comment on the profile page saying these routes 404 is
     removed.
   - Planned frontend tests. On the component: prefill, type, `maxLength`,
     `required`; the PATCH body contains only the field; order PATCH,
     `refresh`, push; button disabled while pending; no linked Customer
     redirects and renders no form; each error case above, with no
     navigation except 404; back link target. On each page: it passes its
     settings (label, type, `maxLength`, title).
7. **Build order.** Backend first, then frontend, each with its own
   red/green and commit.
8. **Planned backend tests.** Name only, phone only, both; neither gives
   400; blank and too-long values give 400; no linked Customer gives 404;
   unauthenticated gives 401, as for cancel, pay and `appointments/mine/`;
   the mass-assignment guard from point 3; cross-tenant: an Account logged
   in to salon B sends the PATCH to salon A's URL, gets 401, and no Customer
   in either salon changes.
9. **Unchanged, out of scope.** The `/client` layout does not check the
   role, so an admin Account can reach these pages. Not addressed here.

### Email normalization and admin email lock (before item 8)

Decided 30.09.2026, in discussion, before item 8 starts.

1. **Login and resend-verification normalize the email.** Stored emails
   are lowercase (`AccountManager.create_account`,
   `backend/accounts/models.py:151`), but login looks the Account up by
   the email as typed (`backend/accounts/serializers.py:171`; SimpleJWT's
   plain `CharField`), and so does resend-verification
   (`ResendVerificationSerializer`, `serializers.py:65-66`, lookup at
   `backend/accounts/views.py:288`). A capitalised address therefore
   failed to log in; a space-padded one did not, because DRF's
   `CharField`/`EmailField` already trim whitespace by default. Fix: both
   strip and lowercase the email before the lookup, the same way register
   (`serializers.py:44-45`) and password-reset (`serializers.py:51-52`)
   already do. Implemented 30.09.2026: login and resend-verification get
   the same `validate_email` as `RegisterSerializer` (strip, lowercase).
   Two space-padded tests guard DRF's trimming; without it, resend would
   also return a 400 instead of its constant 202.
2. **Admin change form: email is read-only.** The Django admin change form
   lets an operator edit an existing Account's email
   (`backend/accounts/admin.py:147-165`, not in `readonly_fields` at
   `:129`) with no lowercasing and without resetting `email_verified_at`,
   which breaks "verified means the current email is proven". Fix: on the
   change form, `email` is read-only. The add form is unchanged. A client
   changes their own email through item 8. Implemented 30.09.2026 through
   `AccountAdmin.get_readonly_fields`, which adds `email` only when the
   object exists. A static `readonly_fields` entry would also lock the add
   page; a guard test pins that the add page keeps an editable email.
3. **Build.** Each fix gets its own red/green cycle and commit.

### Item 8 decisions (change email)

Decided 30.09.2026, in discussion, before item 8 starts.

1. **Request.** The user asks for the change on `/client/profile/email`
   with the new email and their current password. A wrong password sends
   nothing.
2. **Token.** A separate signed "email change" token, not the registration
   verification token (that one only verifies the current
   `account.email`). The new address lives only in the token until
   confirmed; nothing in the database changes before the link is clicked.
   The token is invalid once `account.email` differs from the email it
   was issued for.
3. **Emails.** A link goes to the new address. A notice goes to the old
   address saying a change was requested, and that if it was not them
   they should change their password and contact the salon. A cancel link
   in that notice is a later improvement, not in this item.
4. **Confirmation.** `Account.email` and the linked Customer's email both
   change, and `email_verified_at` is set (the new address is proven).
   The confirm step sets `email_verified_at` itself, because the existing
   verify step only stamps accounts that are not verified yet
   (`backend/accounts/views.py:156`). The old address keeps working for
   login until then.
5. **Address taken.** The new address counts as taken if another Customer
   or another Account in the same salon already has it. After a correct
   password, the request response is always the same, whether the address
   is free or taken: the page says a letter was sent to the new address.
   If the address is free, that letter carries the confirmation link. If
   it is taken, the letter carries no link and says only "Цю адресу не
   можна використати." So only the owner of the new mailbox learns the
   outcome, and nobody can probe which addresses exist in the salon, the
   same principle as password reset's constant 202. The notice to the old
   address is sent in both cases. The check runs again at confirmation,
   because the address may be taken between request and click; then the
   confirm page shows "Цю адресу не можна використати." and nothing
   changes. Merging the two Customers is a later step.
6. **Throttling.** The request endpoint is throttled like password reset.
7. **Still to design.** Endpoints, pages, email texts and tests are
   designed before the red phase and recorded as an addition to this
   entry. Emails are in Ukrainian.
8. **Relink error, to confirm.** The note in § Stage 15 planning, item 8,
   that relinking an already-linked Customer "surfaces as a bare 500"
   looks, from reading `core/exceptions.py`, like a 400
   `unique_violation`. To be confirmed by a test during item 8, and that
   note corrected then.
   Confirmed 20.10.2026: 400 `unique_violation`; see "Account linked to a
   Customer held by another Account".

   Design details, decided 30.09.2026:

   Backend
   - **Request.** `POST /api/v1/salons/<slug>/auth/me/email-change/`,
     authenticated, default authentication (CSRF enforced). Body:
     `new_email`, `password`. The new email is stripped and lowercased.
     Throttle scope `email_change`, 3/hour, keyed by the account. Errors,
     each a 400 with its own code: wrong password `invalid_password`
     (nothing sent); new email equal to the current one `same_email`;
     invalid format the standard field validation error on `new_email`.
     Otherwise always the same 202 with an empty body, whether the address
     is free or taken.
   - **Token.** `TimestampSigner` with its own salt (not the registration
     verification salt), payload `account_id`, `old_email`, `new_email`;
     valid 24 hours; rejected once `account.email` differs from
     `old_email`, which also makes it single-use.
   - **Confirm.** `POST /api/v1/salons/<slug>/auth/email-change/confirm/`,
     public, body: `token`. In one transaction with the Account row
     locked: check the token; re-check that no other Account and no other
     Customer in the same salon has `new_email`; set `Account.email`, the
     linked Customer's email (if any) and `email_verified_at`. Success 204.
     Invalid or expired token: the existing `InvalidOrExpiredTokenError`.
     Address taken at confirmation: 400 with code `email_unavailable`;
     nothing changes. Sessions are not ended by the change.
   - **Taken** means another Account or another Customer in the same
     salon; addresses in other salons do not count.
   - **Taken is case-insensitive.** Added 02.10.2026: both the request
     and the confirmation check match `email__iexact`, not an exact
     match. Guest booking stores `Customer.email` without lowercasing
     (see the known issue recorded 02.10.2026), so an exact match would
     miss a row like `Alice@X.com` and create a duplicate person.

   Emails (Ukrainian, sent as plain text by Celery tasks like the existing
   verification email; the view resolves the salon name with
   `resolve_translation(salon.name, "uk")` and passes it to the task as a
   plain string, since the tasks load no models)
   1. New address, free. Subject "Підтвердіть нову адресу пошти". Body: a
      change of email was requested for the account in salon {salon};
      follow the link to confirm; the link is valid for 24 hours; if you
      did not request it, ignore this letter.
   2. New address, taken. Subject "Зміна адреси пошти". Body: someone
      requested changing the email of an account in salon {salon} to this
      address; "Цю адресу не можна використати." No link.
   3. Old address, always after a correct password. Subject "Запит на
      зміну адреси пошти". Body: a change to {new_email} was requested for
      your account in salon {salon}; the current address keeps working
      until the new one is confirmed; if it was not you, change your
      password and contact the salon. The change-password and
      forgot-password pages arrive with items 9 and 11, so until then this
      advice has no page behind it.

   Frontend
   - **`/client/profile/email`**: back link "← Профіль" to
     `/client/profile`; heading "Змінити email"; label "Поточна адреса"
     with the current address as text; field "Нова адреса" (type
     `email`); the `PasswordField` for the current password (label
     "Поточний пароль", `autoComplete` `current-password`); hint text "Ми
     надішлемо лист із підтвердженням на нову адресу. Поточна адреса
     лишається робочою для входу, доки ви її не підтвердите."; button
     "Надіслати підтвердження". On 202 the form is replaced by "Ми
     надіслали лист на {new_email}. Перейдіть за посиланням у листі, щоб
     підтвердити нову адресу." Errors map by code: `invalid_password` to
     "Неправильний пароль.", `same_email` to "Це вже ваша адреса.", 429 to
     "Забагато спроб. Спробуйте пізніше.", the rest as in item 10.
   - **`/confirm-email-change`**, a separate public page reading `#token`
     like `/verify-email`. States: "Підтверджуємо нову адресу...",
     "Адресу пошти змінено." with a link to the profile, "Цю адресу не
     можна використати.", "Посилання недійсне або застаріле.", plus the
     connection text. How a logged-in user's `AuthContext` picks up the
     new email after confirmation is decided at the frontend recon.

   Planned backend tests
   - **Request:** 401 unauthenticated; cross-tenant login gives 401; wrong
     password 400 with no email sent; same email 400 (also when typed in
     different case); invalid format 400; free address 202 with the
     confirmation letter to the new address and the notice to the old
     one, database unchanged; taken by another Account, and taken by
     another unlinked Customer: 202 with the no-link letter and the
     notice, database unchanged; the free and taken responses are
     byte-identical; an address taken only in another salon counts as
     free; throttle gives 429 on the 4th request.
   - **Confirm:** a valid token changes `Account.email`, the linked
     Customer's email and `email_verified_at` (also for an already
     verified account); an account without a linked Customer changes only
     the Account; expired, tampered and reused tokens give the
     invalid-token error; a token issued before `account.email` changed is
     rejected; address taken between request and confirm (by an Account,
     and by a Customer) gives the taken error with nothing changed; a
     registration verification token is rejected by this endpoint and an
     email-change token by the verify-email endpoint; a token for another
     salon's account is rejected on this salon's URL.

   Cycle 1 (request) implemented, 02.10.2026
   - **View.** `MeEmailChangeView` at `auth/me/email-change/`, with the
     same default authentication and permissions as `MeCustomerView` and
     `ScopedRateThrottle` at scope `email_change` (3/hour). The token
     comes from `generate_email_change_token` in `accounts/tokens.py`
     (salt `accounts.email-change`); its validator lands with cycle 2.
   - **Error codes.** `invalid_password` and `same_email` are
     `InvalidPasswordError` and `SameEmailError`, `DomainError`
     subclasses in `core/exceptions.py`, raised from
     `MeEmailChangeSerializer.validate()` as `ReviewCreateSerializer`
     raises `DuplicateReviewError`. Not a DRF `ValidationError`: the
     exception handler gives every `ValidationError` the generic code
     `invalid`, so a distinct code needs its own `DomainError`.
   - **Taken check.** `is_email_taken` in `accounts/services.py`
     (case-insensitive, tenant-scoped managers only, excluding the
     account itself and its own linked Customer). The confirm step reuses
     it for its re-check.
   - **Emails.** Three Celery tasks in `accounts/tasks.py` take only plain
     strings (recipient, link, salon name, new email). The view resolves
     the salon name and builds the link; the tasks load no models.
   - **Order.** A bad email format is a field-level 400 on `new_email`,
     raised by DRF's field validation before `validate()`, so it comes
     before the password check. The wrong-password 400 still precedes the
     same-email check, the taken lookup and any mail.

   Cycle 2 additions, decided 02.10.2026
   - **Salon in the token.** The email-change token payload also carries
     `salon_id`. The confirm view compares it with the current salon
     before looking up the Account; a mismatch raises the same
     `InvalidOrExpiredTokenError` as any invalid token. Reason: a second
     guard independent of the tenant-scoped manager, because this token
     changes the login email. Cycle 1's `generate_email_change_token` and
     its payload test change accordingly.
   - **Public endpoint.** The confirm view uses `PublicEndpointMixin`,
     like `VerifyEmailView`, so a stale access cookie cannot turn it into
     a 401.
   - **Race at save.** If saving the new email hits a unique-constraint
     `IntegrityError` (the address was taken between the check and the
     save), the response is the same 400 `email_unavailable` and nothing
     changes.

   Cycle 2 (confirm) implemented, 02.10.2026
   - **View.** `EmailChangeConfirmView` at `auth/email-change/confirm/`,
     public through `PublicEndpointMixin`. In one `transaction.atomic()`
     it reads the token, re-checks the address with `is_email_taken`, then
     sets `Account.email`, `email_verified_at` and the linked Customer's
     email. Success is 204.
   - **Token reader.** `read_email_change_token` in `accounts/tokens.py`
     unsigns with `EMAIL_CHANGE_TOKEN_MAX_AGE`, compares the payload's
     `salon_id` with the bound salon before any lookup, locks the Account
     through the tenant-scoped `Account.objects` with
     `select_for_update`, and rejects the token once `account.email`
     differs from `old_email`. Every failure raises `BadSignature` or
     `SignatureExpired`, which the view turns into
     `InvalidOrExpiredTokenError`.
   - **Address taken.** `EmailUnavailableError` (`DomainError`, code
     `email_unavailable`, 400) is raised by the re-check and by an
     `IntegrityError` at save.
   - **Inner atomic block.** The saves run in an inner
     `transaction.atomic()`, kept although no test can observe it today:
     the `IntegrityError` is re-raised as `EmailUnavailableError` out of
     the outer block, which rolls everything back anyway. The savepoint
     matters only if code after the `except` ever keeps using the
     transaction.
   - **Untestable guard.** The tenant-scoped Account lookup cannot be
     isolated by an HTTP test while the `salon_id` check is in place: a
     token for another salon's account is already rejected by the
     `salon_id` check. A mutation test confirmed it (an unscoped lookup
     alone breaks no test; removing both guards does).

   Frontend details, decided 02.10.2026
   - **AuthContext after confirm.** On success the confirm page calls
     `refresh()` from `AuthContext`, so a logged-in user's profile shows
     the new address. A logged-out user is unaffected.
   - **Success text.** The success state adds the sentence "Для входу
     використовуйте нову адресу." before the profile link.
   - **Request page errors.** The generic text is "Не вдалося надіслати.
     Спробуйте ще раз."; a 400 field error on `new_email` shows
     "Перевірте адресу пошти."; the connection and 401 texts are as in
     item 10.
   - **Confirm page, unexpected errors.** A 500 or any other unexpected
     error shows "Не вдалося підтвердити адресу. Оновіть сторінку, щоб
     спробувати ще раз.", not the invalid-link text.
   - **Sent screen.** The "Ми надіслали лист" screen mirrors the
     registration check-email screen. A "Надіслати ще раз" button repeats
     the request with the values from the last successful send, held in
     component state, then shows "Лист надіслано ще раз."; a 429 shows
     "Забагато спроб. Спробуйте пізніше.". That 429 text is a deliberate
     difference from registration, whose resend shows the same outcome
     whatever happens: registration is public and hides outcomes, while
     here the password was already accepted. Other resend errors use the
     request page texts (connection, 401, generic). An `invalid_password`
     on resend (the password changed in between) shows "Неправильний
     пароль." and returns to the form like "Змінити", with the new
     address filled in and the password cleared. A "Вказали не ту адресу?
     Змінити" link returns to the form with the new address filled in and
     the password cleared, like registration (`register/page.tsx:133`). Each
     send issues a new token; once one is confirmed the others stop
     working, because their `old_email` no longer matches. Changed
     16.10.2026: the resend success text is now "Лист надіслано.", see §
     "Item 13 decisions (verification reminder)", point 9.
   - **Checks.** The new code adds no lint or tsc errors and uses no
     `eslint-disable` comments. `VerifyEmailStatus` is not changed in
     item 8.
   - **Stale comment.** The comment in `client/profile/page.tsx:10-12`
     saying the email route 404s is updated when the page lands.

   Frontend implemented, 02.10.2026
   - **Pages.** `ChangeEmailForm` at `/client/profile/email` and
     `ConfirmEmailChangeStatus` at `/confirm-email-change`.
   - **Reading the hash.** The confirm page reads the hash with
     `useSyncExternalStore`, so "no token" is derived during render and
     no set-state-in-effect lint error is added.
   - **Reload after success.** Reloading after a successful confirmation
     shows the invalid-link text, because the used token stays in the
     hash. Kept on purpose: clearing it would break "Оновіть сторінку"
     after a server error, which needs the token still there.

   Browser-checked by Katya on 05.10.2026 (wrong password, same email,
   request with both letters, resend 429, confirm, profile refresh, reload
   after success, login with old and new email, database state); item 8
   closed.

### Malformed body on token endpoints (verify-email, email-change confirm)

Decided 02.10.2026, in discussion.

1. **Same answer as a bad token.** A body that is not a JSON object, or a
   token that is not a string, gets the same `InvalidOrExpiredTokenError`
   (400) as an empty or forged token. Today both views answer it with a
   500 and log a traceback: they read the token with
   `request.data.get("token", "")` (`backend/accounts/views.py:206` and
   `:509`), so a list body raises `AttributeError` and an integer token
   raises `TypeError` inside the signer.
2. **Not a serializer.** A required `CharField` would change the error
   for a missing token, which today is the invalid-token error.
3. **Build.** Item 8 cycle 3, one red/green for both views.

Implemented 02.10.2026 as `_token_from_body` in `backend/accounts/views.py`,
used by both views; form-encoded bodies still work because `QueryDict` is a
`dict` subclass.

### Item 9 decisions (change password): ending sessions

Decided 05.10.2026, in discussion.

1. **Scope.** Changing the password (item 9) and resetting it
   (`PasswordResetConfirmView`) end every session of the account on every
   device, including the current one; the user logs in again with the new
   password. Changing the email does not (item 8 stays as decided).
2. **Mechanism.** `Account.session_version`, an integer defaulting to 0
   (migration). `get_token` puts it as a claim in every access and refresh
   token. `AccountJWTAuthentication` and the refresh serializer reject a
   token whose claim is missing or differs from the account's value, with
   the same 401 as any invalid token. A password change or reset
   increments it with an `F()` update in the same transaction as
   `set_password`, so the access token stops working on the next request,
   not after 15 minutes.
3. **One-time logout on ship.** Tokens issued before this change have no
   claim, so everyone is logged out once when it ships. Accepted: there
   are no real users yet, and a "missing means 0" branch would add code to
   the most sensitive path.
4. **Why not the blacklist.** `OutstandingToken.user` is a foreign key to
   `settings.AUTH_USER_MODEL` (`accounts.User`), not `Account`
   (`rest_framework_simplejwt/token_blacklist/models.py:8`, SimpleJWT
   5.5.1), so there is no per-account list of refresh tokens to blacklist.
5. **Build order.** Cycle S1: the `session_version` mechanism plus the
   reset increment (backend only). Then the item 9 endpoint, then the
   item 9 page. The endpoint's path, errors, throttle and texts are
   decided after S1.

   Cycle S1 implemented, 06.10.2026
   - **Mechanism.** `Account.session_version` (migration
     `accounts/0008_account_session_version`), the `session_version` claim
     in `get_token`, and the check in `AccountJWTAuthentication.get_user`
     and `AccountTokenRefreshSerializer` are in place, as decided in
     point 2.
   - **Reset.** `PasswordResetConfirmView` increments `session_version`
     with an `F()` update inside its `transaction.atomic()`, after
     `set_password`. A failed validation changes nothing.
   - **Still pending.** The change-password endpoint is not built yet, so
     its increment is still to come with it.

   Endpoint and page, decided 06.10.2026. Mockups are on the Stage 15
   design canvas, row "Item 9".

   Backend
   1. **Request.** `POST /api/v1/salons/<slug>/auth/me/password-change/`,
      authenticated with the project defaults (cookie JWT plus CSRF).
      Body: `current_password`, `new_password`.
   2. **Check order.** First the current password: a wrong one reuses
      `invalid_password` (`InvalidPasswordError`, whose docstring is
      widened beyond item 8). Then a new password equal to the current one:
      a new coded 400 `same_password`, a `DomainError`. Then
      `validate_password(new_password, user=account)`, a field-level 400 on
      `new_password`. The strength check therefore lives in `validate()`,
      not in a field validator.
   3. **Transaction.** The Account row is locked with `select_for_update`
      and the password is re-checked on the locked row. `set_password`,
      `save` and the `F()` `session_version` increment run in one
      `transaction.atomic()`, like the reset.
   4. **Success.** 204 with an empty body; `clear_auth_cookies` clears all
      three cookies. No blacklisting: `session_version` already ends the
      token.
   5. **Throttle.** New scope `password_change`, 5/hour, keyed by the
      account; every request counts.
   6. **Notice email.** "Пароль змінено" to the account's current address,
      sent by a Celery task queued with `transaction.on_commit`, so a
      rolled-back change sends nothing. The task receives only strings.
      Body: "Вітаємо!", "Пароль до вашого акаунта в салоні <назва салону>
      щойно змінено. Ви вийшли з акаунта на всіх пристроях.", "Якщо ви не
      змінювали пароль, відновіть доступ через «Забули пароль?» на
      сторінці входу.", then the salon name. No "if it was you" line.
      Changed 20.10.2026: the last sentence is now "Якщо ви не змінювали
      пароль, відновіть доступ за посиланням: <посилання>", a plain-text
      link to the same salon's `/forgot-password`. The view passes the
      slug of the salon the change was made in, and the task builds the
      link with `build_salon_frontend_url`, like the verification and
      reset emails; no `?from` parameter. Reason: a person whose password
      was changed by someone else needs the shortest path back, and the
      other account emails already carry links. Decided by Katya after a
      check on 20.10.2026 found the email named the login page's "Забули
      пароль?" in words only, with no link, while Item 11 point 13 read as
      if it linked there.

   Frontend (`/client/profile/password`)
   7. **Fields.** Current password, new password (hint "Пароль має
      містити щонайменше 8 символів."), confirm new password, all
      `PasswordField`. The confirmation is checked in the browser only,
      like registration; a mismatch shows "Паролі не збігаються." and
      sends no request.
   8. **Texts.** Above the form: "Після зміни пароля ви вийдете з акаунта
      на всіх пристроях, і потрібно буде увійти знову." Button "Змінити
      пароль". Link "Забули поточний пароль?"; its target arrives with
      item 11.
   9. **Errors.** Fixed texts, never the server's message:
      `invalid_password` "Неправильний поточний пароль." (the current
      password field is cleared, the others kept); `same_password` "Новий
      пароль має відрізнятися від поточного."; any 400 on `new_password`
      "Цей пароль занадто простий. Оберіть інший." (generic until the
      Stage 18-21 i18n pass, accepted); 429 "Забагато спроб. Спробуйте
      пізніше."; 401 "Сесія завершилась. Увійдіть знову."; `TypeError` or
      `RenewalUnsureError` "Не вдалося з'єднатися. Перевірте інтернет і
      спробуйте ще раз."; anything else "Не вдалося змінити пароль.
      Спробуйте ще раз."
   10. **After 204.** The local session ends the same way a deliberate
       logout ends it (no return path saved), then the page goes to
       `/login?notice=password_changed`. The login page shows "Пароль
       змінено. Увійдіть з новим паролем." only for that exact value. The
       email field is not prefilled.

   Backend implemented, 06.10.2026
   - **In place.** Points 1-6: `MePasswordChangeView`,
     `MePasswordChangeSerializer`, `SamePasswordError`,
     `send_password_changed_email`, and the throttle scope
     `password_change` at 5/hour. This closes the "Still pending" line of
     cycle S1: a password change now increments `session_version` too.
   - **Untested re-check.** The re-check of the current password on the
     locked Account row has no test: a sequential test cannot produce the
     race it guards against. Like item 8's inner atomic block, it stays
     without a test.
   - **Frontend.** Points 7-10: see "Frontend implemented, 15.10.2026"
     below.

   Frontend details, decided 06.10.2026
   1. **Ending the session.** After the 204 the form calls
      `AuthContext.logout()` unchanged, then
      `router.replace("/login?notice=password_changed")`. The redundant
      `POST /auth/logout/` this makes is accepted, to reuse `logout()`'s
      tested ordering.
   2. **Redirect race.** Found in the recon: `ClientLayout`'s
      `router.replace("/login")` runs after the form's navigation and would
      drop the notice. `ClientLayout` therefore no longer redirects when
      `loggedOutDeliberately` is true; whoever ends the session
      deliberately owns the navigation. This changes § "S3 design details",
      point 5, where the layout redirected in both cases. Both
      deliberate-logout callers already navigate themselves:
      `ClientAvatarMenu` and the logout button on
      `app/client/profile/page.tsx`. A test for each keeps it landing on
      `/login`. `app/client/layout.test.tsx:148` changes, because the
      required behaviour changed (not to make code pass): it must assert
      that with `loggedOutDeliberately` true the layout neither redirects
      nor saves a return path. This overrides § "S3 design details",
      point 8's "keep passing unchanged" for that one test.

      Refined 15.10.2026. The manual browser check (point 6) found that
      after a deliberate logout, pressing Back to a `/client` page showed a
      blank page forever, because the layout skipped the redirect whenever
      `loggedOutDeliberately` was true. Refined rule: `ClientLayout` skips
      the redirect only when `me` goes from an account to `null` while the
      layout is mounted (the logout its own page started, whose caller
      navigates). When the layout mounts with no account and the logout was
      deliberate (Back, a typed URL), it redirects to `/login` and saves no
      return path. A lost session is unchanged: it saves the return path
      and redirects to `/login`. The test
      `test_deliberate_logout_on_fresh_mount_saves_nothing_and_redirects_to_plain_login`
      in `app/client/layout.test.tsx` changes again to match the refined rule;
      this supersedes the 06.10 sentence about that test above.
   3. **Login page notice.** The login page reads `notice` from
      `window.location.search` with `useSyncExternalStore` (precedent:
      `/confirm-email-change` reading its hash) and shows "Пароль змінено.
      Увійдіть з новим паролем." with `role="status"` above the form, only
      for the exact value `password_changed`. The login page stays a
      client component. Its existing English texts are out of scope (a
      Ukrainian pass is queued after Stage 15).
   4. **Page.** Title "Змінити пароль", a "← Профіль" back link, labels
      "Поточний пароль", "Новий пароль", "Підтвердіть новий пароль";
      `autoComplete` `current-password`, `new-password`, `new-password`.
      After `same_password` or a weak-password error all fields keep their
      values.
   5. **No "Забули поточний пароль?" link yet.** Item 11 adds it together
      with its target page. This replaces the link in point 8 above.
   6. **Manual browser check.** The redirect race must be checked by hand
      in a real browser; unit tests with a mocked router cannot show it.

      Results, 15.10.2026. The notice on `/login?notice=password_changed`
      appears after a real change (the `useSyncExternalStore`/`popstate`
      concern did not show up). The other device's session ends. The old
      password is rejected and the new one accepted; login lands on
      `/client`. The notice email arrives once, with the agreed text. The
      check also found the blank page on Back described in point 2's
      refinement. Katya considered one generic error message instead of
      the separate texts of point 9 above and chose to keep the separate
      texts.

   Frontend implemented, 15.10.2026
   - **In place.** Cycle F1: `ClientLayout` leaves navigation to a
     deliberate logout. Cycle F2: the login page notice. Cycle F3: the
     change-password page. Cycle F4: no blank page on Back, the refined
     rule of point 2.
   - **Browser check.** Katya's browser check on 15.10.2026 passed,
     including Back after a password change and after an avatar-menu
     logout.
   - **Item 9 is complete.**

### Known issue: `OutstandingToken.user` holds the wrong row

Recorded 05.10.2026, from the item 9 recon.

- SimpleJWT's `outstand()` looks up `User` by the Account pk
  (`rest_framework_simplejwt/tokens.py:316-321`, SimpleJWT 5.5.1: it reads
  the token's user id claim and calls `User.objects.get(...)` on
  `get_user_model()`). So `OutstandingToken` rows get `user=None`, or an
  unrelated `User` that happens to share that pk.
- No security effect: blacklisting checks the token's `jti`, not the
  `user` column. But the column is wrong.

### Item 11 decisions (forgot password)

Decided 15.10.2026, in discussion, after the item 11 recon of the same day.

Backend
1. **Reset email.** In Ukrainian, with the salon name; the task receives
   only strings. Subject "Відновлення пароля". Body: "Вітаємо!", "Щоб
   задати новий пароль до акаунта в салоні <назва салону>, перейдіть за
   посиланням: <link>", "Посилання діє 1 годину.", "Якщо ви не просили
   змінити пароль, просто проігноруйте цей лист.", then the salon name.
   The link stays `/reset-password#uid=...&token=...`.
2. **Confirm check order.** `uid` and `token` first, then
   `validate_password(new_password, user=account)` as a field-level 400
   on `new_password`. A bad link always reports
   `invalid_or_expired_token`, even with a weak password.
3. **Row lock.** Confirm locks the Account row with `select_for_update`
   and re-checks the token on the locked row. `set_password`, `save` and
   the `session_version` increment stay in one transaction.
4. **Cookies.** A successful confirm (204) clears the three auth cookies
   with `clear_auth_cookies`, like item 9.
5. **No notice email after a reset.** Only someone with access to the
   inbox can reset, and the notice would go to that same inbox, so it
   tells the owner nothing new and an attacker can delete it. Item 9
   differs because a change goes through an open session, not the inbox.
6. **Request timing.** The request step queues the reset task only when
   the account exists, so it is not timing-identical, contrary to § Stage
   3 decisions (corrected there). Recorded as a known issue, not a fix:
   see § "Known issue: password-reset request is not timing-identical".
7. **Logging in invalidates a pending reset link.** Django's token covers
   `last_login`, which login updates. Accepted; the invalid-link text of
   point 11 covers it.

Frontend
8. **`/forgot-password`** (public). Back link "← Вхід"; title
   "Відновлення пароля"; text "Вкажіть адресу пошти, з якою ви входите.
   Ми надішлемо посилання, щоб задати новий пароль."; field "Email";
   button "Надіслати посилання". Errors: a 400 on `email` "Перевірте
   адресу пошти."; 429 "Забагато спроб. Спробуйте пізніше." (the limit is
   per IP and reveals nothing about accounts); `TypeError` "Не вдалося
   з'єднатися. Перевірте інтернет і спробуйте ще раз."; anything else
   "Не вдалося надіслати. Спробуйте ще раз."
   Changed 20.10.2026, found in manual testing after Stage 15 closure:
   from the change-password page the fixed "← Вхід" landed a logged-in
   user on `/login`, which looks like a logout. The back link now depends
   on a `from` query parameter, read on the server from the page's
   `searchParams` (so the label doesn't switch after the page loads) and
   passed to the form as a fixed value.
   "← Зміна пароля" to `/client/profile/password` only for the exact
   value `change-password` (an allowlist; the value is never used as a
   URL); a missing or any other value, including paths and full URLs,
   keeps "← Вхід" to `/login`. Only the change-password link
   (point 13) passes `?from=change-password`; the login page and the
   reset page's "Надіслати новий лист" link are unchanged.
9. **Sent screen.** "Якщо ця адреса зареєстрована, ми надіслали на неї
   лист із посиланням. Посилання діє 1 годину." with "Надіслати ще раз"
   (success text "Лист надіслано ще раз.", same errors as point 8) and
   "Вказали не ту адресу? Змінити" (back to the form, address kept), like
   registration and item 8. Changed 16.10.2026: the resend success text is
   now "Лист надіслано.", see § "Item 13 decisions (verification
   reminder)", point 9.
10. **`/reset-password`** (public). Reads `uid` and `token` from the hash
    once, keeps them in memory, then removes the hash from the address
    bar with `history.replaceState` so the link does not stay in browser
    history. Removing the hash must not turn the page into the
    invalid-link state. Title "Новий пароль"; fields "Новий пароль" (hint
    "Пароль має містити щонайменше 8 символів.") and "Підтвердіть новий
    пароль"; button "Зберегти пароль". A mismatch shows "Паролі не
    збігаються." and sends no request.
11. **Reset errors.** A missing or partial hash (no request sent) and
    `invalid_or_expired_token` both show "Посилання недійсне або
    застаріле." with a link "Надіслати новий лист" to `/forgot-password`.
    A weak password "Цей пароль занадто простий. Оберіть інший.";
    `TypeError` the connection text of point 8; anything else "Не вдалося
    змінити пароль. Спробуйте ще раз."
12. **After 204.** `await logout()`, then
    `router.replace("/login?notice=password_changed")`, reusing the item 9
    notice text.
13. **Links.** "Забули пароль?" on the login page and "Забули поточний
    пароль?" on `/client/profile/password`, both to `/forgot-password`.
    The login link is needed because a person who forgot the password
    cannot reach the profile; the item 9 notice email named the login
    page's "Забули пароль?" link in words only, with no URL, until
    20.10.2026; since then it links to `/forgot-password` directly (Item 9
    point 6, changed 20.10.2026). The item 9 frontend test asserting the
    change-password page has no forgot-password link changes, because the
    requirement changed.

Build order: B1 the email (point 1); B2 the confirm step (points 2-4);
F1 `/forgot-password` and both links; F2 `/reset-password`; then a browser
check.

Backend implemented, 15.10.2026
- **In place.** B1: the reset email in Ukrainian with the salon name
  (point 1). B2: points 2-4, the link checked first, `user=account` in
  `validate_password`, the row lock with the token re-checked on the
  locked row, and the cookies cleared on 204.
- **Deleted account.** The locked lookup uses `filter().first()`, so an
  account deleted in between gives `invalid_or_expired_token`, not a 500.
- **Untested re-check.** The token re-check on the locked row has no test:
  a sequential test cannot produce the race it guards against. Like item
  9, it stays without a test.
- **Frontend.** See "Implemented, 15.10.2026" below.

Early link check, decided 15.10.2026

Katya's browser check found that `/reset-password` shows the form for a
dead link and reports it only after the person has typed a new password
twice.

1. **Endpoint.** New public endpoint
   `POST /api/v1/salons/<slug>/auth/password-reset/check/` with body
   `{uid, token}` (`PublicEndpointMixin`, no throttle, like confirm). 204
   if the link is valid, otherwise the same 400 `invalid_or_expired_token`
   as confirm, including for a malformed body. It changes nothing: no
   password, no `last_login`, no `session_version`. POST, not GET, so the
   token never appears in a URL or server log.
2. **One validation function.** Check and confirm call one shared
   link-validation function (uid decode, tenant-scoped account lookup,
   token check), so they cannot drift apart. Confirm still validates the
   link itself; the check is a convenience, not a security layer.
3. **Page.** After reading `uid` and `token`, `/reset-password` sends one
   check request (one request even under StrictMode) and shows
   "Перевіряємо посилання..." meanwhile. 204: the form.
   `invalid_or_expired_token`: the invalid-link state with "Надіслати
   новий лист". A network error or any other failure of the check: the
   form anyway, because the hash is already removed and a reload would
   show a false invalid-link state; confirm will validate the link on
   submit.
4. **Tests.** They include a cross-salon case: a salon A link checked on
   salon B's URL gets 400.

Build order: B3 the check endpoint; F3 the page change; then repeat the
browser check of reopening an already-used reset link: the page must show
the invalid-link state before any password is typed.

Implemented, 15.10.2026
- **Backend.** B3: `POST auth/password-reset/check/`, which shares
  `read_password_reset_link` with confirm.
- **Frontend.** F1: `/forgot-password` and both links. F2:
  `/reset-password`. F3: the early link check.
- **Browser check.** Katya's browser checks on 15.10.2026 passed,
  including reopening an already-used link, which now shows the
  invalid-link state before any password is typed.
- **Item 11 is complete.**

### Known issue: password-reset request is not timing-identical

Recorded 15.10.2026, from the item 11 recon.

- `PasswordResetRequestView` queues the reset task only when an account
  with the address exists in the salon, so a request for a registered
  address takes one queue write longer than one for an unknown address.
  This contradicts § Stage 3 decisions' "a Celery task is enqueued either
  way" (corrected there).
- The difference is one queue write, about a millisecond, far below
  network jitter. Katya accepted it as a known minor issue given the
  threat model; it is not scheduled for a fix.

### Item 13 decisions (verification reminder)

Decided 16.10.2026, in discussion, after the item 13 recon of the same day.
Mockups are on the Stage 15 design canvas, row "Item 13" (dashboard with
the banner, profile with the banner, banner states, booking step 4).

Backend
1. **Verification email.** In Ukrainian, with the salon name, like item
   11's reset email; the task receives only strings. Subject
   "Підтвердження пошти". Body: "Вітаємо!", "Щоб підтвердити пошту для
   акаунта в салоні <назва салону>, перейдіть за посиланням: <link>",
   "Посилання діє 48 годин.", "Якщо ви не реєструвалися, просто
   проігноруйте цей лист.", then the salon name. The link stays
   `/verify-email#token=...`.
2. **No new endpoint.** The banner calls the existing public
   `POST auth/resend-verification/` with the account's own email. Its
   constant 202 is safe here because the page shows the same text for
   every outcome.

Frontend
3. **Banner.** In `app/client/layout.tsx`, on every `/client` page, for a
   client whose `email_verified` is false. Not dismissible; it disappears
   once the email is verified. Title "Підтвердіть пошту"; text "Ми
   надіслали лист на <email>. Після підтвердження тут з'являться ваші
   записи, і ви зможете бронювати з акаунта."; button "Надіслати лист ще
   раз".
4. **Resend states.** While sending, the button reads "Надсилаємо..." and
   is disabled. 202: "Лист надіслано на <email>."; 429: "Забагато спроб.
   Спробуйте пізніше."; `TypeError`: "Не вдалося з'єднатися. Перевірте
   інтернет і спробуйте ще раз."; anything else: "Не вдалося надіслати.
   Спробуйте ще раз." The server's message is never shown.
5. **Verified in another tab.** While the banner is shown, it calls
   `refresh()` from `AuthContext` when the tab becomes visible again
   (`visibilitychange`), so it disappears without a reload. Only the
   banner does this; `AuthContext` does not change.
6. **`/verify-email`.** After a successful verification the page calls
   `refresh()`, like `/confirm-email-change`. Its link goes to `/client`
   for a client and `/admin` for an admin (decided 16.10.2026, mirroring
   the login page's redirect), otherwise to `/login`. Refined 16.10.2026,
   because "Увійти" read wrongly for someone already logged in: the link
   reads "Увійти" only when nobody is logged in; for a logged-in client or
   admin it reads "Продовжити" (to `/client` and `/admin`). The page's
   other texts stay as they are.
7. **Booking step 4.** A logged-in client with `email_verified` false sees
   "Підтвердіть пошту, щоб бронювати з акаунта." above the guest form.
8. **Registration resend text.** "Якщо у нас є ваша адреса, ми щойно
   надіслали новий лист." becomes "Лист надіслано." The registration
   screen already tells the person to check their email, so the short
   text does not repeat it. Katya found the old text confusing (manual
   check of 21.09.2026). A constant text reveals nothing, and a person who
   typed a wrong address will not get the letter and can use "Вказали не
   ту адресу? Змінити email".
9. **One answer on the three resend screens.** For the same reason, the
   resend success text "Лист надіслано ще раз." on the item 8 change-email
   screen and the item 11 forgot-password screen becomes "Лист надіслано."
   too, so the resend buttons on the registration, item 8 change-email and
   item 11 forgot-password screens answer the same way. The banner keeps
   "Лист надіслано на <email>." from point 4, because the banner's text
   names the address itself. The item 8 and item 11 entries carry a note
   pointing here.

Build order: B1 the email (point 1); F1 the banner (points 3-5); F2 points
6-9; then a browser check.

Implemented, 16.10.2026: B1 (Ukrainian verification email), F1 (banner,
points 3-5) and F2 (points 6-9, plus the "Продовжити"/"Увійти" link
labels) are in place, and `VerifyEmailStatus` no longer has the
set-state-in-effect lint error (2 known lint errors remain). Katya's
browser check on 16.10.2026 passed: registration resend text, banner on
dashboard and profile, resend from the banner, step 4 line, verification
in a second tab with the banner disappearing on return, and "Лист
надіслано." on the change-email and forgot-password screens. The banner
layout is deferred (see the deferred list under "Agreed stage order").
Item 13 is complete.

### Appointment times in the salon's time zone

Decided 16.10.2026, in discussion, after the recon of the same day. Nothing
here is implemented yet.

Found in Katya's manual check of 21.09.2026: an appointment at 12:15 salon
time showed as 13:15 on `/client` in a browser at +4, while the email showed
12:15. `appointments/mine/` sends UTC (`Z`), and the dashboard's
`formatDateTime` formats it with `toLocaleString` in the browser's zone. The
emails were right because `format_datetime_for_salon` converts to
`salon.timezone` explicitly. This entry fixes the known issue recorded in §
Stage 15 planning, item 5, Cycle D ("`client/page.tsx`'s `formatDateTime`
uses `new Date(iso).toLocaleString`...").

1. **Rule.** Every API time meant for display carries the salon's offset,
   as availability slots already do (`scheduling/views.py`). The appointment
   serializers (account list `AppointmentAccountSerializer`, guest
   `AppointmentGuestSerializer`, created `AppointmentCreatedSerializer`)
   render `start_datetime`, `end_datetime` and `refund_deadline` (where the
   serializer has it) as ISO 8601 in `salon.timezone`, for example
   `2026-09-21T12:15:00+03:00`. They do it through one shared field, so the
   serializers cannot drift apart.
2. **Frontend.** The frontend reads the salon's wall time from the string,
   like `formatSlotForSummary`, and never formats these times with `Date`'s
   local getters or with `toLocaleString` without a `timeZone`. The
   dashboard keeps the year in its format. Comparisons with "now"
   (`isUpcoming`, the refund deadline in the cancel wording) stay
   instant-based and do not change.
3. **Tests.** Backend: the list returns the salon offset, not `Z`, including
   for a salon in a different zone and on both sides of a DST change.
   Frontend: the dashboard shows the salon's wall time whatever the test
   runner's time zone.
4. **Known issues, recorded and not fixed here.**
   - Review dates (`ReviewsList`) are formatted with `Intl.DateTimeFormat`
     without a `timeZone`, so they can shift by a day near midnight.
   - Booking step 3's default "today" is the UTC date
     (`booking/page.tsx`, `todayIsoDate`), not the salon's. For a salon
     ahead of UTC, in the first hours after the salon's midnight (00:00 to
     03:00 in Kyiv in summer) the UTC date is still the previous day, so
     the default window starts a day early.

Build order: T1 the shared backend field and the serializers; T2 the
dashboard formatting; then a browser check with the browser set to another
time zone.

Implemented, 16.10.2026
- **T1.** `SalonLocalDateTimeField` renders the appointment times on the
  guest, account-list and created serializers, `refund_deadline` included.
  The list selects the salon with `select_related`, so the zone costs no
  query per row. A two-salon test catches a zone taken from the wrong salon.
- **T2.** `formatSalonDateTime` reads the date and the time from the string.
  The dashboard tests run under Asia/Tbilisi and America/New_York.
- **Browser check.** Katya's browser check on 16.10.2026 passed: an 11:30
  booking shows 11:30 on `/client` in Katya's own browser in Batumi
  (Asia/Tbilisi) and with the zone overridden to Asia/Tokyo, matching the
  email.
- The known issue about the dashboard time zone is fixed.

### Price and prepayment on dashboard cards

Decided 17.10.2026, in discussion, after the recon of the same day. Closes
the Stage 15 item 4 refinement "Shows price/deposit amount per
appointment", which the dashboard did not do: it showed no full price, no
prepayment before payment, and a hardcoded `₴` instead of the salon's
currency.

**Implemented, 19.10.2026.** Backend: `deposit_amount`, `currency` and
`deposit_outcome` on `appointments/mine/`. Frontend: the money line comes
from one function, the price is shown only with a money line, every amount
goes through `formatPrice`, and the "Передоплата" line is in a `<strong>`.
Mutation testing: backend 6 of 6 killed (deposit_amount, currency, 17.10.2026) 
and 7 of 7 killed (deposit_outcome), frontend 8 of 8 killed.
Browser-checked on all states, including a real late payment: an expired
appointment whose Payment reached `refund_due` True and `REFUND_PENDING`
through the webhook.

Depends on "Persisted refund decision (`Payment.refund_due`)":
`deposit_outcome` (point 1) reads `Payment.refund_due`, so that entry is
implemented first.

1. **Backend.** `appointments/mine/` (`AppointmentAccountSerializer`) gains
   three read-only fields:
   - `deposit_amount`: `Payment.amount` when a Payment exists; otherwise
     computed from `service_price_at_booking` and
     `deposit_percentage_at_booking` by the same helper `initiate_payment`
     uses. `_compute_deposit_amount` in `payments/services.py` is made
     public as `compute_deposit_amount` for this; the rounding logic is not
     duplicated. This reverses the serializer docstring's earlier stance
     that the helper stays module-private. `null` whenever the deposit is
     0, whether the percentage is 0 or rounding to whole units gives 0
     (e.g. 20% of 2.00); `null` means no "Передоплата" line.
   - `currency`: `Payment.currency` when a Payment exists, otherwise the
     salon's `Salon.currency`. A historic payment keeps the currency it was
     actually paid in, even if the salon changes its currency later.
   - `deposit_outcome`: what happened to a paid deposit, stated
     explicitly so the frontend never infers it from `payment_status`:
     - `"refunded"`: the Payment is `REFUNDED`;
     - `"refund_processing"`: the Payment is `REFUND_PENDING`, or
       `SUCCEEDED` with `refund_due`;
     - `"withheld"`: the Payment is `SUCCEEDED`, not `refund_due`, and the
       appointment is `CANCELLED`;
     - `null` otherwise (no Payment, or any other combination).
     The frontend reads only this field for the cancelled and expired
     money lines (point 4), never `payment_status`.
   Checked before deciding: `amount_due_at_visit` is
   `service_price_at_booking - Payment.amount`, and `Payment.amount` is
   written only by `initiate_payment` from `compute_deposit_amount`, so
   `deposit_amount + amount_due_at_visit` always equals the price.
2. **Frontend never computes money.** It only formats backend amounts with
   the existing `formatPrice(price, currency)`. The hardcoded `₴` in
   `client/page.tsx` is removed.
3. **Full price in the title row.** The full price
   (`service_price_at_booking`) appears next to the service name, before
   the status badge ("Манікюр  120 $"), if and only if the card has a
   money line under the date (point 4). Completed cards therefore show no
   price and no money line.
4. **Money line under the date**, per status:
   - pending payment: "Передоплата: 24 $", in bold;
   - confirmed: "Оплата при візиті: 96 $";
   - cancelled and expired alike, by `deposit_outcome` (point 1):
     - `refund_processing`: "Повернення обробляється: 24 $";
     - `refunded`: "Повернено: 24 $";
     - `withheld`: "Передоплату утримано: 24 $";
     - `null`: none.
     The amount on these lines is `deposit_amount` (point 1); a `null`
     `deposit_amount` means no line. "Передоплату утримано" is never shown
     when a refund is owed;
   - completed: none.
5. **Wording.** "Передоплата" replaces "Депозит" in these texts. Ukrainian
   only.
6. **Tenant isolation.** The currency and the amounts come only from the
   appointment's own salon and payment. The two-salon test covers salons
   with different currencies.
7. **Mockup.** Stage 15 design canvas, board "Ціна поруч з послугою (в)",
   with the price removed on completed cards and on any card without a
   money line.

Left as they are:

- **`no_show`:** not changed here (today no badge and no line). By
  point 3 it shows no price and no line.
- **0% deposit booking flow: not decided here.** Today a 0% booking still
  goes through `PENDING_PAYMENT` and a 0.00 payment (`create_appointment`
  always sets `PENDING_PAYMENT`; `initiate_payment` has no zero check). A
  no-prepayment flow is a separate future decision, to be made together
  with salon settings. See "Known issue: `Salon.deposit_percentage` is
  unbounded".

### Known issue: `Salon.deposit_percentage` is unbounded

Recorded 17.10.2026, from the recon for "Price and prepayment on dashboard
cards".

- `Salon.deposit_percentage` (`backend/tenants/models.py`) has no
  validators and no database constraint, so negative values or values
  above 100 can be stored. Today only through the admin or the database:
  no API endpoint writes it.
- Fix later with a `CheckConstraint` for 0..100, together with the 0%
  deposit flow decision. A `Meta` change, so it needs its own approval
  before the change is written.
- Also unknown: what WayForPay does with a 0.00 amount. That can happen
  today when a small price rounds the deposit to 0, since
  `initiate_payment` sends whatever `compute_deposit_amount` returns.

### Persisted refund decision (`Payment.refund_due`)

Decided 17.10.2026, in discussion, after the recon of the same day.

**Implemented, 19.10.2026.** The field and migration `0007_payment_refund_due`.
`refund_due` is set inside the cancel transaction and inside the webhook
transaction. Both paths lock the Appointment, then the Payment, and the
webhook rechecks the Payment status after locking. Mutation testing killed
10 of 10 mutations; M9 (looking up the Payment by salon only) was killed only
after a same-salon test was added.

1. **Field.** `Payment` gains `refund_due` (boolean, default `False`).
2. **When it is set.** It is set to `True` in the same transaction in which
   the backend decides a refund is owed:
   - the payment webhook's late-payment path: the Payment becomes
     `SUCCEEDED` while the appointment is already `EXPIRED` or `CANCELLED`
     (`PaymentWebhookView`, today a local `should_initiate_refund`);
   - the cancel path, when the cancellation is refund-eligible
     (`cancel_appointment`, today a local `is_eligible`).
   It is never recomputed later from the rules.
   Implementation constraints:
   - `cancel_appointment` decides refund eligibility and sets `refund_due`
     inside the same transaction that cancels the appointment, not after
     the commit as today (`booking/services.py`, the `_is_refund_eligible`
     call after the `atomic()` block). Otherwise a crash between the two
     leaves the decision unrecorded.
   - Both the cancel path and the webhook set `refund_due` while holding a
     row lock on the appointment (`select_for_update`), so a concurrent
     cancel and payment-success webhook cannot overwrite each other's
     decision. Checked 17.10.2026: `cancel_appointment` already takes that
     lock as the first statement of its transaction; the webhook takes it
     too, after locking the Payment row.
   - Lock order: every code path that locks both an Appointment and its
     Payment takes the Appointment lock first, then the Payment lock
     (parent before child). The cancel path already locks the Appointment
     first; today it does not lock the Payment in that transaction at all,
     and when it starts to (for `refund_due`), it locks it second. The
     payment webhook changes to: read the Payment without a lock to find
     its appointment, lock the Appointment, then lock the Payment and
     re-check its status after locking. Reason: opposite lock orders
     between a concurrent cancel and payment-success webhook can deadlock;
     PostgreSQL would abort one of them with an error. At the start of
     implementation, recon every path that locks both rows (including
     `initiate_payment`, `initiate_refund` and the sweeps) and report its
     current lock order before changing anything.
3. **Why.** The decision cannot be reconstructed from stored fields. A
   customer can cancel a still-unpaid booking less than 24 hours before the
   visit, and the provider can confirm the payment afterwards; the webhook
   then refunds in full. That row looks exactly like a late cancellation of
   a paid booking, where the deposit is withheld: `CANCELLED`, `SUCCEEDED`,
   the same `cancelled_by` and `cancelled_at`. Replaying the rule would also
   rewrite history whenever the rule changes. Same principle as the
   `*_at_booking` snapshots on `Appointment`.
4. **Existing rows** keep `False`. There is no production data, so no
   backfill.
5. **Order.** Implemented before the dashboard card money lines ("Price and
   prepayment on dashboard cards", `deposit_outcome`) and before the refund
   retry sweep ("Known issue: an owed refund can stay unrefunded").

### Known issue: an owed refund can stay unrefunded

Recorded 17.10.2026, from the recon for "Price and prepayment on dashboard
cards". **High priority: fix right after "Persisted refund decision
(`Payment.refund_due`)".** **Resolved 19.10.2026 by "Refund retry sweep".**

- A `SUCCEEDED` payment with a refund owed can stay unrefunded forever if
  the process dies between the webhook's (or the cancellation's) commit
  and `initiate_refund`, which moves the Payment to `REFUND_PENDING` in its
  own transaction afterwards.
- Nothing picks it up again: the webhook event is already recorded as
  processed, so a redelivery returns early, and `flag_stuck_refunds` only
  looks at `REFUND_PENDING` payments.
- Planned fix: a periodic sweep that finds `SUCCEEDED` payments with
  `refund_due` and retries `initiate_refund`, which is already idempotent
  for a payment past `SUCCEEDED`. Decided in "Refund retry sweep" below.

### Refund retry sweep

Decided 19.10.2026, in discussion, after the recon of the same day. Fixes
"Known issue: an owed refund can stay unrefunded".

**Implemented, 19.10.2026.** `get_payment_provider()` in
`payments/providers/__init__.py`, the service `retry_owed_refunds` in
`payments/services.py`, the task `retry_owed_refund_payments` in
`payments/tasks.py`, and its beat entry every 1800 s. `PaymentProviderError`
is caught per row. Mutation testing killed 12 of 12 mutations. M5 (dropping
the `salon` filter, a second guard behind the tenant-scoped manager) was
killed only after a test calling the service with a salon other than the
bound tenant context was added.

1. **What it does.** A periodic task finds `Payment` rows with status
   `SUCCEEDED` and `refund_due` True, across all salons, and calls
   `initiate_refund` for each one inside that payment's own salon tenant
   context.
2. **Shape.** Same as the existing sweeps (`flag_stuck_refunds`,
   `expire_overdue_appointments`, `complete_overdue_appointments`): a
   service function per salon that finds candidate ids with an unlocked
   query, plus a Celery task that loops `Salon.objects.all()`, binds
   `tenant_context(salon.id)` per salon, reads `now` once before the loop,
   and wraps each salon in its own try/except. Each row gets its own
   transaction, the one `initiate_refund` already opens.
3. **Failures.** `PaymentProviderError` is caught per row, logged with the
   payment id and the salon id, and the sweep continues with the next row.
   The row is already `REFUND_PENDING` (`initiate_refund` commits that
   before it calls the provider), so `flag_stuck_refunds` flags it after
   `STUCK_REFUND_THRESHOLD`. Any other exception is not caught per row: it
   propagates to the per-salon try/except, as in the other sweeps, and ends
   that salon's batch for the run. Reason: a provider outage is expected
   and must not block other rows; an unexpected exception is a bug and
   must not be swallowed. The per-row catch of `PaymentProviderError` is
   the one difference from the existing sweeps, which catch only per
   salon.
4. **Interval.** 1800 s (30 min), the same as `flag_stuck_refunds`, the
   closest existing sweep. The recon found no reason to differ: a row only
   needs this sweep after a crash between a commit and `initiate_refund`,
   and the delay does not affect stuck-refund detection, whose clock
   (`refund_initiated_at`) starts when `initiate_refund` runs.
5. **No grace period.** The sweep may pick up a row while the cancel or
   webhook after-commit call is still about to refund it. That is safe:
   `initiate_refund` locks the Payment (`select_for_update`), proceeds only
   from `SUCCEEDED`, and commits `REFUND_PENDING` before the provider call.
   A second, concurrent call waits on the lock, then finds
   `REFUND_PENDING` and returns without calling the provider.
   `initiate_refund` locks only the Payment, so it cannot deadlock with the
   Appointment-then-Payment order of the cancel path and the webhook.
6. **Provider.** A small function `get_payment_provider()` in the
   `payments` module returns the provider; today it returns
   `MockPaymentProvider()`. The sweep gets its provider only through this
   function, and tests patch it. Views keep their `provider_class`
   attribute for now, because existing tests rely on it. Requirement for
   the stage that wires a real provider: views and the sweep both switch to
   `get_payment_provider()`, so payments and sweep refunds never go through
   different providers.
7. **Required tests.**
   - The sweep refunds a stuck payment (`SUCCEEDED`, `refund_due`).
   - It skips `SUCCEEDED` without `refund_due`, `REFUND_PENDING`, and
     `REFUNDED`.
   - Two salons are processed, each in its own tenant context.
   - One failing row does not block the other salons.
   - A `PaymentProviderError` on one row still lets the next row in the
     same salon be refunded.
   - An unexpected exception on one row ends that salon's batch, but not
     the other salons'.
   - A concurrent normal `initiate_refund` still calls the provider only
     once.

### Known issue: `initiate_payment` writes the Payment outside its lock

Recorded 19.10.2026, from the recon for "Persisted refund decision
(`Payment.refund_due`)". Not observed; to investigate later.

- `initiate_payment` locks the Appointment, but creates the Payment (or
  moves a `FAILED` one to `PENDING`) after the locked block, outside any
  transaction.
- Two concurrent "pay" requests could both see no active payment and both
  start a provider session. For a new Payment, the second insert would then
  hit the one-to-one constraint on `Payment.appointment`.

### Known issue: the `flag_stuck_refunds` race test uses a savepoint

Recorded 19.10.2026, while writing the tests for "Persisted refund decision
(`Payment.refund_due`)".

- The race test in `test_payments_flag_stuck_refunds.py` commits its
  "competing" write through the same connection, inside the service's own
  transaction, so the write is a savepoint, not a concurrent session as its
  docstring says.
- The test passes but proves less than it claims.
- Fix: make the competing write through a separate connection, as test 19
  in `test_payments_refund_due.py`
  (`test_webhook_rechecks_payment_status_after_locking`) does.

### Known issue: `WayForPayProvider` must be cleaned up before it is wired

Found 21.10.2026 during the Stage 16 sub-step 3 recon.

- `payments/providers/wayforpay.py` has "TEMPORARY" diagnostic logging in
  `start_payment()` (comment at ~lines 170-174): on failure it logs the
  network exception, or WayForPay's response status and body (~lines 193
  and 209). Remove it before WayForPay is wired. `refund()` logs the
  response body on failure too (~line 279), without the "TEMPORARY"
  marker; review it at the same time.
- "WayForPayProvider: first-time technical conventions" says the adapter
  raises no custom exceptions and `payments/services.py` does the
  wrapping, but `refund()` raises `PaymentProviderError` itself (~lines
  266 and 281). Reconcile the code and this entry when WayForPay is wired.

### Known issue: `tsc --noEmit` does not type-check the project

Recorded 19.10.2026, during the frontend GREEN for "Price and prepayment on
dashboard cards".

- `tsc --noEmit` in `frontend/` stops at syntax errors in the generated
  `.next/dev/types/routes.d.ts` and never type-checks the source files.
  Earlier "tsc clean" reports proved nothing.
- A temporary tsconfig, kept outside the repository, that extends the
  project's and excludes `.next` and `next-env.d.ts` does check the source.
  On 19.10.2026 it showed real errors in two test files,
  `src/app/register/page.test.tsx` and
  `src/app/specialists/[id]/page.test.tsx`. It also reports `LayoutProps`
  as missing in `src/app/layout.tsx`, but only because the generated
  Next.js types are excluded.

**Resolved 20.10.2026.**

- The 19.10 failure came from a corrupted `.next/dev/types` file, not from
  a permanent break. `next dev` writes `routes.d.ts` and `validator.ts`
  with a plain, non-atomic `writeFile`, and both files had leftover text
  after their real end. After a container restart the files were
  regenerated cleanly, and `tsc --noEmit` checked all 134 source files.
- The command also always exited 2, because `tsc` could not write
  `tsconfig.tsbuildinfo` into the root-owned `/app` bind mount
  (`incremental: true` in `tsconfig.json`).
- Fix: the `typecheck` npm script, `tsc --noEmit --incremental false`, run
  as `docker compose exec frontend npm run typecheck`. CLAUDE.md now says
  that errors inside `.next/` mean corrupted generated types and the
  frontend container should be restarted. `tsconfig.json` is unchanged.

### Known issue: ESLint `react-hooks/set-state-in-effect` in the client dashboard

Recorded 19.10.2026, during the frontend GREEN for "Price and prepayment on
dashboard cards".

- `frontend/src/app/client/page.tsx`: the `useEffect` that calls
  `loadAppointments()` on mount fails ESLint's
  `react-hooks/set-state-in-effect`.
- Pre-existing: the committed version of the file has the same error. The
  money-line change did not touch that effect.

**Resolved 20.10.2026.** `loadAppointments()` now sets state only in the
`then` callbacks of the `getMyAppointments` request. The mount effect only
starts the request, as in `VerifyEmailStatus`, and the page starts in the
loading state. Cancel and pay still call the same `loadAppointments()`.
The existing `client/page.test.tsx` tests are unchanged and pass.

**Fully resolved 20.10.2026.** `booking/pay/PaymentStatus.tsx:82` had the
same error: the mount effect set the "invalid" phase synchronously when the
URL fragment was missing or malformed. Now the hash is read with
`useSyncExternalStore`, as in `VerifyEmailStatus`. Its subscribe never
fires, so the fragment is still read once, when the page loads. The
invalid case is derived during render. A child `PaymentFlow` does the
detail fetch once on mount, with the same `cancelled` cleanup, and sets
state only after the request settles. The existing
`PaymentStatus.test.tsx` tests are unchanged and pass. `npm run lint`
reports no errors in the frontend.

### Account linked to a Customer held by another Account

Decided 20.10.2026, in discussion, after the recon of the same day. Closes
Stage 15 closure checklist item 2. Supersedes the "bare 500" note in §
Stage 15 planning, item 8.

**Implemented, 20.10.2026.** `AccountLinkUnavailableError` (409,
`account_link_unavailable`) is raised in `create_account_appointment`
before the Customer is changed. `_verify_and_link` keeps
`email_verified_at` and skips the link. `AccountBookingForm` shows the
message. Mutation testing killed 7 of 7 mutations, including a message
that would reveal another account and refusing a guest Customer.
No browser check: the state can only be created through the database; 
behaviour is covered by tests and mutations.

1. **Facts from the recon.**
   - The state (account B's email matches a Customer already linked to
     account A) arises only from Django admin edits. See "Known issue:
     Admin can create mismatched Account/Customer links".
   - No API path lets B reach A's Customer, A's appointments in
     `appointments/mine/`, cancel them, or receive A's notifications.
   - Today both booking (`create_account_appointment`) and verification
     (`_verify_and_link`) end in a 400 `unique_violation` from the
     `Account.customer` one-to-one constraint and roll back. Nothing is
     changed.
2. **Booking.** In `create_account_appointment`, before linking: if the
   matched Customer is already linked to another Account, raise a
   dedicated error mapped to 409 with code `account_link_unavailable` and
   the neutral message "Не вдалося оформити запис з облікового запису.
   Зверніться до салону." The message must not reveal that another
   account exists (anti-enumeration). The `IntegrityError` translation in
   `core.exceptions.exception_handler` stays as the backstop.
3. **Verification.** In `_verify_and_link`, in the same situation: set
   `email_verified_at`, skip the link, and return the normal success
   response, so B is never locked out of verification.
4. **Frontend.** `AccountBookingForm` shows that message for
   `account_link_unavailable`.
5. **Required tests**, with two accounts in the same salon:
   - booking returns 409 with `account_link_unavailable`, creates nothing,
     and leaves the Customer's name and phone unchanged;
   - verification succeeds without linking;
   - B's `appointments/mine/` stays empty, and B cannot cancel A's
     appointment (404);
   - a frontend test for the message.

### Known issue: Admin can create mismatched Account/Customer links

Recorded 20.10.2026, from the recon for "Account linked to a Customer held
by another Account". Deferred to Stage 21. Applies to any place that edits
Account/Customer links or a linked Customer's email: the Django admin and
the salon admin panel built in Stages 18-21.

- `AccountAdmin` can link any Customer to an Account.
- `CustomerAdmin` can change a linked Customer's email. Notifications go
  to `Customer.email`, so this can route A's notifications to someone
  else's mailbox.
- Deleting an Account leaves its Customer unlinked, and a new account with
  the same email can claim it.
- Planned fix: validation in the admin forms (option C from the recon).

### New booking button on the client dashboard

Decided 20.10.2026, in discussion, after the recon of the same day. Closes
Stage 15 closure checklist item 3.

**Implemented, 20.10.2026.** Shown only with at least one appointment;
a link to `/services`; a pill fixed to the bottom-right with the safe-area
offset; bottom padding on the list. The colour is reused from the
"Забронювати" link on `/specialists/[id]`. Mutation testing killed 6 of 6
mutations. Browser-checked at 390px width. The iPhone safe-area offset
cannot be checked in desktop Chrome and still needs a real device.

1. **When it is shown.** Only when the appointment list has loaded and
   contains at least one appointment, upcoming or past. Not shown in the
   empty state, which keeps its own "Забронювати візит" link to
   `/services` unchanged. Not shown while loading or on the error state.
2. **Target.** A link to `/services`, the only booking entry that needs
   no pre-selection. `/booking` without parameters is a 404 by the
   Stage 14 scope revision of 13.09.2026.
3. **Look and placement.** From the mockup board "Нове бронювання: внизу",
   variant (д): a pill fixed to the bottom-right of the viewport,
   "+ Нове бронювання", in the accent colour, at least 48px tall, above
   the iPhone safe area (`env(safe-area-inset-bottom)`).
4. **Accessibility.** The accessible name is exactly "Нове бронювання";
   the "+" is `aria-hidden`.
5. **Nothing hidden under it.** The list gets bottom padding at least as
   tall as the button, so the last card and its actions are never hidden
   under it.
6. **Styling.** Final styling belongs to the design pass (Stages 18-21);
   placement and behaviour are decided here.
7. **Required tests.**
   - The button is present, with that name and `href="/services"`, when
     there are upcoming appointments, and when there are only past ones.
   - It is absent in the empty state (where "Забронювати візит" is still
     present), while loading, and on the error state.
   - The "+" is not part of the accessible name.

## Stage 16 planning

Decided 21.10.2026. Not yet implemented.

### Scope and tools

- **Read-only grounding tools only:** `list_services`,
  `get_specialist_info`, `check_availability`, `get_salon_info`,
  `get_faq`, plus `propose_booking`, which reads and changes no data;
  the orchestrator intercepts it to validate and show the card (see
  "Sub-step 6 details"). `get_salon_info` returns the deposit
  percentage, the 24h cancellation/refund rule, the currency, the
  timezone and the customer-facing contacts. The per-service deposit
  amount and amount due at the visit come from `list_services`. Money
  amounts are computed by the backend with the same functions as
  booking; the model never computes money.
  - **Refined 21.10.2026,** see "Sub-step 4 details": this entry first
    put the per-service deposit and amount due in `get_salon_info`.
- **Propose-only handoff** (§ Business rules, unchanged). The proposal
  carries `service_id`, an optional `specialist_id` (`"any"` allowed),
  up to 3 validated slots and the card display data (see "Product
  decisions"), and only what the customer asked for. The backend
  validates every id through tenant-scoped querysets before sending the
  proposal. Normally the model learns from `check_availability` that a
  slot is taken and offers nearby validated slots in its reply. If a slot
  becomes unavailable between the tool call and the final proposal
  validation (a race), it is removed from the card without an `error`
  event; the card shows the remaining validated slots, or only "Another
  day or time" if none remain. The booking form still rejects a stale
  slot as today. The frontend builds the "Go to
  booking" button from the validated proposal using the existing
  `/booking` query parameters (`entry`, `service`, `specialist`, `slot`,
  `step`, `date_from`). The model never writes URLs.
  - **Refined 23.10.2026,** see "Sub-step 6 details": when no validated
    slot remains, the card shows a single "Choose a time" ("Обрати час")
    button instead of "Another day or time"; both go to step 3.
- **The assistant does not see the customer's own appointments**; the
  endpoint does not use account authentication.
- **New field `Salon.assistant_enabled`, default `False`.** The operator
  enables it per salon in Django admin, including for `bella-demo`. When
  `False`, `GET /api/v1/salons/<slug>/` exposes `assistant_enabled=false`
  so the widget is not rendered, and the chat endpoints return 404.
  - **Corrected 21.10.2026:** this entry first said `bella-demo` would be
    enabled by a data migration. There is no data migration: `bella-demo`
    exists only in local dev databases, created by hand in `/admin/`, so
    such a migration would be a no-op on fresh, test and production
    databases.

### Request flow

- **Streamed responses with typed events:** `text`, `proposal`, `error`,
  `done`. The proposal is sent after the text, once validated.
- **Synchronous Django view returning a streamed response.** The
  generator binds `tenant_context(salon_id)` itself: the middleware's
  context has ended by the time the body is iterated. It converts any
  exception into an `error` event: the DRF exception handler never sees
  exceptions raised during iteration.
- **Global cap of 16 concurrent streams**, counted in Redis across
  processes. Over the cap returns error code `assistant_busy`.
- **ASGI deferred.** Revisit when the concurrency cap is actually being
  hit.
- **Provider abstraction following the payments pattern.**
  `MockAssistantProvider` first (scripted streams including tool calls, no
  network in tests). First real provider: OpenAI, GPT-5.6 Luna with
  minimal reasoning effort; the exact model id and parameters are verified
  against OpenAI docs at implementation time. Anthropic remains possible
  behind the same interface. Before enabling for real customers, run
  10-15 real Ukrainian questions and review quality.

### Session

- **Server-issued opaque random session id**, stored by the widget in
  `sessionStorage` and sent in a request header. Not a cookie, so the chat
  has no CSRF surface.
- **The Redis key includes the salon id from tenant context**: a session
  id from another salon resolves to nothing. Separate Redis DB 3, not the
  throttle cache DB 2.
- **The conversation ends when the tab closes or 30 minutes after the
  last message** (sliding TTL).
- **A GET endpoint returns the live history** so the widget can restore
  it after a reload; empty once expired.
- **Message texts only** (no tool results), last 20 messages, max 2000
  characters per customer message. No "start over" endpoint for now.

### Language

- **Reply in the language of the customer's message.** Service names
  exactly as returned by tools in the site language (the request's
  `lang`); a name is translated only when the customer asks, keeping the
  original in parentheses. `Service.description` is conveyed faithfully
  in the chat language, adding nothing.

### Safety and privacy

- **No tool can modify data.** Tools never accept a salon id from the
  model; the salon comes only from tenant context.
- **Conversation text is never logged**; log only salon, token counts,
  latency and errors.
- **Health questions:** answer from salon data only, no medical
  conclusions, suggest checking with the specialist.
- **The customer is told about AI processing:** a short notice in the
  widget (Stage 17) plus the privacy policy. OpenAI retains API data up to
  30 days by default; zero data retention is a possible later improvement.

### Limits

- **Per session:** 10 messages per minute.
- **Per IP:** 200 messages per hour, counted per salon (key: salon + IP).
  Generous because mobile carriers and salon Wi-Fi share IPs; per salon so
  one salon's traffic never affects another.
- **Per salon:** 1000 messages per day, counted in the salon's timezone,
  error code `assistant_daily_limit`.
- **Widget texts (Stage 17)**, both followed by a "go to booking" button
  where relevant:
  - `assistant_daily_limit`: "Я сьогодні наговорився досхочу і пішов на
    заслужений манікюр 💅 Повертайтесь завтра! А записатися можна й без
    мене:"
  - `assistant_busy`: "Зараз у мене багато співрозмовниць. Спробуйте,
    будь ласка, за хвилинку 🤗"

### Product decisions (added 21.10.2026)

Agreed after the first planning commit. Mockups: Design canvas boards
"AI 1" to "AI 8" (not in the repo).

**Knowledge and data**

- **Salon FAQ.** New tenant-owned model (`question`, `answer`,
  `ordering`, `is_active`), one language like `Service.description`; the
  model conveys answers in the chat language, adding nothing. New
  read-only tool `get_faq` returns all active entries (small lists, no
  search/RAG). Limits: up to 50 entries, question up to 200 and answer up
  to 1000 characters. Filled by the operator in Django admin until the
  salon admin UI (Stages 18-21).
- **Customer-facing salon contacts.** New `Salon` fields `phone`,
  `address`, `instagram_url`. `get_salon_info` returns them; the
  assistant points to them whenever it cannot answer (including health
  questions). `Salon.contact_email` stays alerts-only.
- **Specialist rating.** `get_specialist_info` returns the average rating
  and review count (reviews come only from completed appointments). No
  review texts are given to the model.
- **`check_availability` filters:** date range up to 14 days per call,
  weekdays, time-of-day window, specialist id or `"any"`. The model only
  turns wishes into these filters; free time comes only from the
  availability engine. When nothing matches, the tool says so and the
  model explains and may offer a compromise.

**Assistant behaviour (system instructions)**

- **General knowledge is allowed for explaining terms in simple words.**
  If the salon does not offer that service, say so explicitly and suggest
  the closest service from the menu. Prices, durations and availability
  only from salon data. Never promise a result that depends on an
  individual consultation.
- **No invented rankings:** never call a specialist "the best"; state
  rating and review count as facts.
- **Specialist preference.** "Only <name>" means offer only that
  specialist (other days/times). "Preferably <name>" allows a compromise
  with another specialist, explained. Unclear: show that specialist's
  options and ask whether another specialist is acceptable.
- These behaviours are checked in the manual 10-15 question review before
  enabling for real customers (model behaviour, not enforceable by
  automated tests).

**Proposal card (contract for the `proposal` event)**

- **The proposal carries display data computed by the backend:** service
  name (site language), specialist name with rating and review count (or
  "any"), duration, price, deposit amount, amount due at the visit,
  currency, and up to 3 validated slots.
- **Stage 17 renders it as a card.** A slot chip opens `/booking` at
  step 4 with that slot; "Another day or time" opens `/booking` at step 3
  with the same service and specialist (no message to the model); a
  single named and still free slot shows one "Go to booking" button. A
  slot that became unavailable after `check_availability` (a race) is
  removed from the card without an `error` event; the card shows the
  remaining validated slots, or only "Another day or time" if none
  remain (see "Scope and tools").
  - **Refined 23.10.2026,** see "Sub-step 6 details": with 1-3 slots the
    card keeps the extra "Another day or time" ("Інший день або час")
    button; with 0 slots it shows a single "Choose a time" ("Обрати
    час") button instead, because "another" makes no sense when no time
    was shown. Both go to step 3.

**Widget-related backend**

- **Starter suggestions.** New `Salon` field `assistant_starters`,
  translatable like other names, up to 3 items of up to 60 characters;
  empty means no suggestions. Exposed with `assistant_enabled` in
  `GET /api/v1/salons/<slug>/`.
- **Retry.** The customer message is written to the session history only
  together with a successful reply, so the widget's "Try again" (after
  `assistant_busy` or a mid-stream error) resends the last message
  without duplicating it.

**Analytics (counters only, no conversation text)**

- **Collected from Stage 16:** conversations started, proposals per
  service, bookings created from an assistant proposal. The proposal's
  booking link carries a source marker (e.g. `via=assistant`) and the
  appointment stores it in a new `source` field; the backend accepts it
  as optional, the frontend passes it in Stage 17. Owner dashboard UI
  belongs to Stages 18-21.
- **Unanswered questions are not collected now** (they are conversation
  text).

**Deferred (agreed direction, not in Stage 16)**

- Opt-in sharing of a conversation with the salon to improve answers:
  built together with the salon admin UI, with explicit consent,
  retention and deletion decided then. Without consent nothing is kept.
- Human handoff and a knowledge base that learns from staff answers:
  needs the admin UI, a notification channel and a privacy decision.
- Fallback model (Anthropic behind the same interface) after the OpenAI
  provider works.
- Messenger channels: still deferred per the Stage 10 decision.
- `Specialist.languages` field: for now salons mention languages in the
  specialist bio.
- Voice input in the widget (Stage 17): the customer presses a
  microphone, sees the recognised text and can edit it before sending;
  browser-side versus server-side recognition to be researched in
  Stage 17. The backend stays text-only.

**Rejected**

- Binding booking with deposit inside the chat (conflicts with
  propose-only).
- Collecting customer contact details in the chat (the booking form does
  this).

### Implementation notes

- **The chat endpoints are public.** They need an explicit `AllowAny`
  plus the existing `PublicEndpointMixin` (`authentication_classes = ()`,
  `accounts/views.py`): the global default is `IsAuthenticated`, and DRF
  authenticates before it checks permissions.
- **The new session header must be added to `CORS_ALLOW_HEADERS`.** Same
  failure mode as `X-Guest-Token` (§ "Fix: CORS_ALLOW_HEADERS missing
  X-Guest-Token"): mocked-fetch tests cannot catch it; it needs a real
  browser check.
- **When Redis DB 3 is implemented**, update the Redis DB descriptions in
  § Stage 3 ("DRF throttling needs a shared cache"), the comment in
  `config/settings/base.py` and `.env.example` in the same code commit.
- **The chat session id is not an auth credential**, so storing it in
  `sessionStorage` does not conflict with the Stage 12 rule (§
  "Authenticated session transport: httpOnly cookie, not localStorage").

### Sub-step order

Each sub-step gets its own red/green/commit cycle.

1. **Salon fields:** `assistant_enabled` (default `False`, enabled per
   salon by the operator in Django admin; no data migration, see "Scope
   and tools"), `phone`, `address`, `instagram_url`,
   `assistant_starters`; expose `assistant_enabled` and
   `assistant_starters` in `GET /api/v1/salons/<slug>/`. Details in
   "Sub-step 1 details".
2. **`FAQItem` model**, admin, limits.
3. **Provider interface and `MockAssistantProvider`:** streamed events
   (text chunk, tool call, end). Plain Python, no views.
4. **Tools**, split into three sub-steps, each with its own
   red/green/commit cycle. Details in "Sub-step 4 details".
   - **4a. Shared helpers:** the deposit and amount-due helper extracted
     from `compute_deposit_amount`; the rating annotation extracted from
     the specialists view mixin. Behaviour-preserving refactors.
   - **4b. Simple tools:** `list_services`, `get_specialist_info` (rating,
     review count), `get_salon_info` (contacts, cancellation rule),
     `get_faq`, with their `ToolSpec` definitions and a registry.
     Two-salon and same-salon isolation tests.
   - **4c. `check_availability`** (filters, 14-day cap, at most 20
     slots). Two-salon and same-salon isolation tests.
5. **Session store in Redis DB 3:** salon in the key, sliding 30-minute
   TTL, last 20 messages, texts only, customer message stored only with a
   successful reply. Test: a session id from another salon resolves to
   nothing.
6. **Conversation orchestrator:** model/tool loop, system instructions,
   proposal validation and card data (tenant-scoped ids, stale slots
   dropped, up to 3 slots). Mock only. Split into three sub-steps, each
   with its own red/green/commit cycle. Details in "Sub-step 6 details".
   - **6a. Shared slot check:** the "is this start offered" function and
     the specialist-service link helper extracted from booking.
     Behaviour-preserving refactors.
   - **6b. Proposal tool `propose_booking`,** intercepted by the
     orchestrator: validation, card data, result to the model.
   - **6c. Orchestrator:** model/tool loop, system instructions, client
     events, storing the turn.
7. **Endpoints:** streaming POST, history GET, 404 when disabled, public
   mixin, session header in `CORS_ALLOW_HEADERS`. Key test: a tool called
   mid-stream sees the correct salon.
8. **Limits:** per session, per salon + IP, per-salon daily limit in the
   salon's timezone, global cap of 16 streams released even when a stream
   fails.
9. **Analytics counters and `Appointment.source`** (optional, accepted by
   the booking endpoint).
10. **Real OpenAI provider:** adapter, settings and key via env, tests
    without network; then the manual check with 10-15 Ukrainian
    questions, including the specialist-preference and name-translation
    cases.

Closure: docs update and a manual stream check.

**The real provider is wired only after sub-step 8 is committed.** Until
then only the mock exists, so endpoints without limits cost nothing and
are reachable only in local dev.

### Sub-step 1 details (decided 21.10.2026)

- **`Salon.assistant_starters`:** `JSONField`, a list of up to 3 items.
  Each item is a translation dict `{lang_code: string}`, like other
  translatable fields; each value is up to 60 characters; unknown
  language keys are rejected. Validated in the model's `clean()` (run by
  the admin). No `CheckConstraint`: the only writer is the operator, and
  per-item length checks on JSON are awkward in the DB. An empty list
  means no suggestions.
- **Read side:** `GET /api/v1/salons/<slug>/` resolves each starter with
  `resolve_translation` for the request's `?lang=` (same fallback as
  other reads); items that resolve to an empty string are omitted. The
  response keys are exactly `currency`, `assistant_enabled` and
  `assistant_starters`. The allowlist test is kept and updated, not
  removed.
- **Contacts:** `phone` is `CharField(max_length=32)` with no format rule
  (same as the Stage 15 customer phone rule, § "Item 10 design details
  (edit name and phone)"). `address` is `CharField(max_length=255)`, not
  translatable: the assistant conveys it in the chat language, like
  `Service.description`. `instagram_url` is a `URLField`. All three are
  optional and blank by default. They are not exposed in
  `GET /api/v1/salons/<slug>/` until a frontend consumer exists (design
  pass, Stages 18-21); the assistant reads them through `get_salon_info`.
- **No new `Meta` constraints in this sub-step.**

### Sub-step 2 details (decided 21.10.2026)

- **Model `FAQItem` in the `tenants` app.** One row is one question.
  FAQ content describes the salon (parking, payment, rules); `catalog` is
  services only, and `ai_assistant` owns no persistent models. This is
  the first tenant-scoped model in `tenants`.
- **Fields:** `TenantScopedModel` + `TimeStamped`. `question`
  `CharField(max_length=200)`, `answer` `CharField(max_length=1000)`,
  `ordering` `PositiveIntegerField(default=0)`, `is_active`
  `BooleanField(default=True)`. One language, not translatable.
- **`Meta` (approved 21.10.2026):** `ordering = ["ordering", "id"]`;
  inherits the base constraints only; no other constraints.
- **Limit:** at most 50 active items per salon, checked in `clean()`
  when the item is active (counting the salon's other active items). No
  DB enforcement: the limit spans rows, and the only writer is the
  operator, so the concurrent-save race is accepted.
- **Why 50:** `get_faq` sends the whole active list to the model on each
  call (cost and answer quality); typical salons need 10-30. If a salon
  outgrows it, move to search (RAG) then.
- **Inactive items** are visible in admin and excluded from `get_faq`.
  Hard delete is allowed: nothing references FAQ items.
- **Admin:** `SalonScopedAdmin`; `list_display` `salon`, `question`,
  `ordering`, `is_active`; filter by `salon` and `is_active`.

### Sub-step 3 details (decided 21.10.2026)

- **Two event layers.** Provider events are internal, from the provider
  to the orchestrator: `TextDelta(text)`, `ToolCall(id, name, arguments)`,
  `Finish(input_tokens, output_tokens)`. Client events go from the
  orchestrator/view to the widget: `text`, `proposal`, `error`, `done`,
  as decided in "Request flow". The customer never sees provider events
  or tool calls.
- **New app `ai_assistant`.** `AppConfig` like the other apps, added to
  `INSTALLED_APPS` after `reviews`. No `models.py` and no migrations,
  consistent with "owns no persistent models" (sub-step 2 details;
  `docs/ARCHITECTURE.md` § apps). Provider code lives in
  `ai_assistant/providers/{__init__,base,mock}.py`, mirroring
  `payments/providers/`.
- **`AssistantProvider(ABC)`** has one abstract method,
  `stream(*, system, messages, tools) -> Iterator[provider event]`,
  keyword-only, plain values only. A provider is a dumb pipe for one model
  turn: it never executes tools, knows nothing about salons or tenant
  context, and never logs request or response content.
- **Frozen dataclasses** for `ChatMessage` (role `user`/`assistant`/`tool`,
  content, an optional tool call id, and the tool calls the assistant
  made), `ToolSpec` (name, description, JSON-schema parameters) and the
  three provider events. Vendor formats are translated inside adapters
  only.
- **`AssistantProviderError(DomainError)`**, code
  `assistant_provider_error`, status 502, in `core/exceptions.py` next to
  `PaymentProviderError`. Real adapters wrap vendor exceptions into it;
  the orchestrator turns it into a client `error` event. This differs from
  the WayForPay convention ("WayForPayProvider: first-time technical
  conventions"), where the service layer wraps provider exceptions.
- **`MockAssistantProvider`** is built with a script: a list of turns.
  Each `stream()` call plays the next turn. A turn is a list of provider
  events and may contain an exception, raised at that point (a
  mid-stream failure). The mock records every call (`system`, `messages`,
  `tools`) for assertions. When the script is finished, a further call
  raises a clear error instead of replaying. Without a script it replies
  with one neutral test sentence and `Finish`, so local dev endpoints work
  before the real provider exists. No network, no logging.
- **`get_assistant_provider()`** returns the mock until sub-step 10 adds
  the OpenAI adapter and a setting to choose it. Tests inject providers by
  monkeypatching the factory where it is imported, as the payments tests
  do (`test_payments_refund_retry_sweep.py`).
- **Tests for this sub-step:** the ABC cannot be instantiated and has
  exactly one abstract method; `stream` is keyword-only; the mock plays
  turns in order, records calls, raises mid-stream, errors when the script
  is exhausted, gives the default reply without a script, and opens no
  network connection (socket-blocking test like
  `test_payments_providers.py`).

### Sub-step 4 details (decided 21.10.2026)

**4a. Shared helpers.** Behaviour-preserving refactors that give existing
code a second caller.

- **Money.** No function computes a deposit without an existing
  appointment: `payments.services.compute_deposit_amount` takes an
  `Appointment` and reads its price and percentage snapshots. A helper is
  extracted from it that takes a price and a percentage and returns the
  deposit (the same half-up rounding to whole units) and the amount due
  at the visit (price minus deposit). `compute_deposit_amount` calls the
  helper. The assistant uses only this helper; `ai_assistant` contains no
  money formula. Existing booking and payment tests stay unchanged and
  green.
- **Rating.** The average rating and review count annotation is
  extracted from the specialists view mixin (`_with_card_annotations`)
  into a reusable function, used by the view and by the assistant. The
  aggregate stays separate from the `services` prefetch, as documented
  there (a join on `services` would inflate the counts). Existing
  specialist tests stay unchanged and green.

**4b. Simple tools,** in `ai_assistant`: plain functions, their `ToolSpec`
definitions and a registry from tool name to function.

- **Context.** The salon comes only from the bound tenant context. The
  site language is passed in by the orchestrator, never by the model.
  Names are resolved with `core.i18n.resolve_translation`.
- **Output** is JSON-serializable dicts. Money is a string like in the
  API (`"40.00"`), together with the salon currency.
- **`list_services`:** active services only (the same visibility as the
  public catalog), with name, category, description, duration, price, and
  the per-service deposit and amount due at the visit from the 4a helper.
  This refines "Scope and tools": the per-service amounts are returned
  here, next to the price; `get_salon_info` gives the deposit percentage
  and the cancellation rule.
- **`get_specialist_info`:** active specialists, only their active
  services, average rating and review count from the 4a function. No
  review texts.
- **`get_salon_info`:** name, timezone, currency, deposit percentage,
  the 24h cancellation/refund rule taken from booking's existing cutoff
  (`REFUND_ELIGIBILITY_CUTOFF`, not restated as a new constant), and the
  customer-facing contacts (`phone`, `address`, `instagram_url`).
- **`get_faq`:** active FAQ items in their order.
- **Bad model input.** An unknown id and another salon's id return the
  same "not found" result. Tools never raise on bad model input.

**4c. `check_availability`.**

- **Inputs:** service id, specialist id or `"any"`, date range, optional
  weekdays, optional time-of-day window. `now` is passed in.
- **The tool enforces what the engine does not:** the service is active;
  the specialist is active and linked to the service; the range is at
  most 14 days. A longer range returns `{"error": "range_too_long"}` so
  the model narrows the range.
- **Weekday and time-of-day filters** are applied to the engine's output
  in the salon's timezone.
- **Output:** at most the 20 earliest matching slots, plus a flag that
  more exist, in the same format as the slots endpoint (salon-local ISO
  8601 with offset).
- **Availability comes only from `scheduling.services`**
  (`compute_candidate_start_times` / `compute_multi_specialist_availability`);
  the tool has no availability logic of its own.

Input details for 4c (decided 22.10.2026):

- **Arguments from the model:** `service_id`; `specialist_id` (an id or
  `"any"`); `date_from` and `date_to` as salon-local dates `"YYYY-MM-DD"`;
  optional `weekdays` as lowercase English names (`"monday"` ..
  `"sunday"`); optional `time_from` and `time_to` as salon-local `"HH:MM"`.
  A slot matches when `time_from <= start < time_to`.
- **`now` and `lang`** are passed by the orchestrator, never by the model.
- **Range:** at most 14 calendar days inclusive
  (`(date_to - date_from).days <= 13`).
- **Errors.** Bad model input never raises; the tool returns
  `{"error": code}` with one of:
  - `not_found`: unknown, inactive or other-salon service or specialist,
    identical in all cases;
  - `specialist_does_not_offer_service`;
  - `range_too_long`;
  - `invalid_range`: an unparseable date, or `date_from` after `date_to`;
  - `invalid_filter`: an unknown weekday name or a bad time format.
- **Success:** `{"slots": [...], "has_more": bool}`, where `slots` holds at
  most the 20 earliest matches as salon-local ISO strings with offset, in
  the same format as the slots endpoint.

Additions for 4c (22.10.2026):

- **`time_from >= time_to`** (both given) returns
  `{"error": "invalid_filter"}`, not empty slots (approved 22.10.2026).
  Both bounds are within one day. A request for "evening or morning"
  could be mapped to 18:00-10:00 and silently get no slots; the error
  makes the model split it into two calls. The `ToolSpec` description
  says so.
- **An empty `weekdays` list** means no weekday filter.
- **Slots are sorted in salon time** before the 20-earliest cut, so the
  result does not depend on the order the engine returns them in.
- **The explicit tenant check** (`_current_salon()`) at the start of every
  tool is a deliberate second guard. In 4b, a mutation that weakened the
  helper was caught only by `get_salon_info`, the one tool that touches no
  tenant-scoped model; in 4c, removing the call survived. For tools that
  query a tenant-scoped model first, mutation tests cannot distinguish the
  explicit check from the manager's own guard.

### Sub-step 5 details (decided 23.10.2026)

- **Direct redis-py client** (already installed, no new dependency), not
  Django's cache API. The cache API has no atomic append: a
  get/modify/set sequence loses a turn when two messages of one session
  overlap. New env var `ASSISTANT_REDIS_URL`
  (`redis://redis:6379/3`). The existing unused `REDIS_URL` is left as
  is.
- **Module `ai_assistant/session_store.py`** with `new_session_id()`,
  `load_history(session_id)` and
  `append_turn(session_id, user_text, assistant_text)`. The salon comes
  only from tenant context; both read and write raise
  `TenantContextMissingError` without it.
- **Storage:** one Redis list per session at key
  `assistant:session:<salon_id>:<sha256 hex of the session id>`; each
  item is a JSON object `{role, content}`. `append_turn` pushes the user
  and assistant messages, trims to the last 20 and sets the 30-minute
  expiry in one `MULTI`/`EXEC` transaction. `load_history` does not
  extend the expiry; only a new turn does.
- **Session id:** `secrets.token_urlsafe(32)`. Only its hash is in the
  key, like guest tokens, so a Redis snapshot or key listing does not
  reveal usable ids.
- **Overlapping messages of one session:** both turns are kept (each pair
  is written atomically); the widget disables input while a reply streams
  (Stage 17). No server-side lock.
- **The atomicity of `append_turn` (one `MULTI`/`EXEC`) is verified by
  code review, not by a test.** Mutation M1 (no transaction) survives
  because tests run sequentially; a threading test was deliberately not
  added.
- **Tests use a separate Redis DB (15)** through an autouse fixture that
  overrides `ASSISTANT_REDIS_URL` and flushes only that DB, so running
  tests never wipes live dev sessions in DB 3.
- **The § Stage 3 Redis note, the `config/settings/base.py` comment and
  `.env.example`** are updated in the same code commit (as already
  required by "Implementation notes").

### Sub-step 6 details (decided 23.10.2026)

**6a. Shared slot check.** Behaviour-preserving refactors that give
existing booking code a second caller.

- **"Is this start offered."** A function extracted from
  `booking/services.py` that answers whether a start is offered for
  (service, specialist or `None` for any, aware start, `now`). It uses
  `compute_candidate_start_times` / `compute_multi_specialist_availability`
  exactly as booking does. Booking calls it; booking's tests stay
  unchanged and green.
  - **Refined 23.10.2026, during implementation:** "booking's tests stay
    unchanged" could not hold together with "booking calls it". Seven
    existing tests force a start to look offered by monkeypatching the
    bare name `booking.services.compute_candidate_start_times`; once
    `create_appointment` goes through `is_start_offered`, that patch no
    longer reaches the check. The alternative was to keep booking's
    inline check and give `is_start_offered` only the assistant as a
    caller. Agreed instead: the seven tests patch
    `booking.services.is_start_offered` (true only for the forced start)
    and each still exercises the same conflict path. The pinned name in
    `tests/test_booking_create_appointment.py`'s module docstring and in
    `booking/services.py`'s module docstring changes accordingly. The
    "any" booking path keeps its own `compute_multi_specialist_availability`
    call: it needs the list of free specialists, not a bool.
- **Specialist-service link check.** One shared helper, used by booking,
  the slots view and the tools, in place of the three inline
  `SpecialistService` queries.

**6b. Proposal tool `propose_booking`.** Intercepted by the orchestrator:
it is not executed against data and never books anything.

- **Model arguments:** `service_id`, `specialist_id` (an id or `"any"`),
  optional `slots` (0-3 ISO strings with offset).
- **Validation:** active service; active and linked specialist, or
  `"any"`; each slot parsed and checked with the 6a function. Invalid or
  no-longer-offered slots are dropped (the race case in "Scope and
  tools"; no `error` event).
- **Card data computed by the backend:** service name in the site
  language; specialist name with average rating and review count (or
  "any"); duration; price; deposit; amount due at the visit; currency;
  the remaining validated slots (salon-local ISO). Booking links use
  `entry=service`; the frontend builds them.
- **Card shape by slot count:** 1 slot → one "Go to booking" button; 2-3
  → slot chips; 0 → only "Choose a time" (step 3). With 1-3 slots the
  card also shows "Another day or time" ("Інший день або час"); with 0
  slots the single button is "Choose a time" ("Обрати час") instead,
  because "another" makes no sense when no time was shown. Both go to
  step 3.
- **Result returned to the model:** what was shown and which slots were
  dropped, or an error code (`not_found`,
  `specialist_does_not_offer_service`).
- **At most one proposal per reply;** a second call returns
  `{"error": "one_proposal_per_reply"}`.

**6c. Orchestrator.**

- **At most 5 model calls per customer message.** When exceeded, an
  `error` event with code `assistant_turn_limit` (widget text, Stage 17:
  "Відповідь обірвалась. Спробуйте ще раз.").
- **Invalid tool calls.** A tool call with an unknown name, or arguments
  that do not match the tool's schema (unknown or missing required
  arguments), returns `{"error": "invalid_tool_call"}` to the model and
  never raises. It counts toward the 5 calls.
- **Tool results** are sent back to the model as JSON strings, non-ASCII
  characters kept.
- **System instructions** live in a Python module in `ai_assistant`,
  written in English, built per message with the salon name, today's
  date and weekday in the salon's timezone, and the site language. They
  carry every assistant behaviour rule recorded in this section, by
  reference: § Business rules (propose-only, session-only memory);
  "Scope and tools" (grounding, money, no URLs, only what the customer
  asked for); "Language"; "Safety and privacy"; "Product decisions"
  (knowledge and data, assistant behaviour).
- **`now`** is taken once per customer message and passed to every tool
  call.
- **Text deltas** are forwarded to the client as they arrive, also before
  later tool rounds.
- **Stored turn:** the full reply text of the message; tool results and
  proposals are not stored. The customer message and reply are stored
  only on success ("Product decisions", Retry).
- **Logging:** token counts are summed across the message's model calls
  for the log line (salon, tokens, latency, errors; never text).
- **The new session id** reaches the widget in a response header
  (sub-step 7).

### Fix: the specialist must perform the service (decided 22.10.2026)

**Problem.** The public slots endpoint and appointment creation (guest
and account, specific-specialist path) never check `SpecialistService`,
and the slots and booking serializers accept an inactive service. A
booking for an unlinked specialist/service pair or for an inactive
service succeeds end to end. The normal click path only offers linked
pairs, so this is reached through stale links (including assistant
cards), settings changed mid-flow, or direct API calls. The gap was
already noted in the module docstring of
`tests/test_booking_create_appointment.py` and never scheduled. 31
backend tests book unlinked pairs only because the shared fixtures create
a specialist and a service without linking them.

**Decisions.**

- **Honest errors.** Neither "no free time" (an empty slot list) nor
  `SLOT_NOT_OFFERED`: both would mislead the customer into retrying.
- **New domain error** `SpecialistDoesNotOfferServiceError` in
  `core/exceptions.py`, code `SPECIALIST_DOES_NOT_OFFER_SERVICE`, 400
  (same casing as `SLOT_NOT_OFFERED`).
- **Inactive service.** The slots and booking serializers bind only
  active services, so an inactive service gets the same 400 "does not
  exist" as an unknown id. This matches the catalog's 404 for inactive
  services.
- **Slots endpoint, specific specialist.** An inactive specialist still
  returns 200 `[]` (Stage 6.I unchanged; checked first). An active but
  unlinked specialist returns `SPECIALIST_DOES_NOT_OFFER_SERVICE`.
- **Booking.** `create_appointment` checks the link before the slot
  check, so both the guest and account paths and any other service-layer
  caller are covered. The "any" path is unaffected: its candidates are
  already linked.
- **Frontend, step 3** reads the error code instead of falling through
  to the default error page:
  - `SPECIALIST_DOES_NOT_OFFER_SERVICE`: "Цей майстер не виконує обрану
    послугу." with a link "Обрати іншого майстра" (step 2 for this
    service).
  - A service error: "Ця послуга зараз недоступна." with a link "До всіх
    послуг".
  - Any other error: "Не вдалося завантажити доступні години." with two
    actions, "Спробувати ще раз" (reloads step 3) and "Почати запис
    спочатку" (back to the booking start).
- **Frontend, step 4** (guest and account forms): for
  `SPECIALIST_DOES_NOT_OFFER_SERVICE`, the same specialist message and
  link as step 3, instead of the generic retry text.
- **Frontend details (decided 22.10.2026):**
  - Step 3 recognises a service error from the validation details on the
    `service` field. An inactive, unknown or other-salon service id all
    show "Ця послуга зараз недоступна.", which is true for the customer
    in each case.
  - "Обрати іншого майстра" always links to step 2 with `entry=service`
    for this service, even if the customer started from a specialist.
  - Step 4 handles only `SPECIALIST_DOES_NOT_OFFER_SERVICE`. An inactive
    service is already a 404 there, via the service detail fetch.
- **Order of work,** each its own commit:
  1. This docs entry. Done 22.10.2026.
  2. Test fixtures link the default specialist to the default service;
     tests that need an unlinked pair create one explicitly. The whole
     suite is green on unchanged code. Done 22.10.2026.
  3. Backend checks, red/green with mutations. Done 22.10.2026: the new
     error, the link check in `create_appointment` and in the slots view,
     and active-only service binding in the slots and booking serializers.
  4. Frontend messages, red/green. Done 22.10.2026, verified in a real
     browser: unlinked pair at step 3 from both entries, unknown
     specialist id, unlinked pair at step 4, inactive service, normal
     booking unaffected. Shared pieces: `apiErrorFromBody` in
     `lib/api/errors.ts`, used by both `apiRequest` and `getAvailability`;
     `classifyStep3Error` in `lib/booking/step3Errors.ts`; and
     `SpecialistNotOfferingNotice`.

### Fix: specialist list cards lead to the specialist page (decided 22.10.2026)

**Problem.** `/specialists/[id]` has the "Забронювати" entry into
booking (`entry=specialist`), but nothing links to it: the `/specialists`
list cards are plain blocks. Found during the Stage 16 manual check.

**Decision** (variant A, mockup boards "Майстри А" on the Design canvas):

- On the `/specialists` list, each card (`SpecialistCard` variant
  `"list"`) is one whole-card link to `/specialists/<id>`, with a
  decorative chevron. The link's accessible name is the specialist's
  name. A new `"list"` variant rather than the `"grid"` one, because
  booking step 2 (`SpecialistSelectionGrid`) renders `"grid"` inside a
  radio label, where a link would be nested and would leave the booking
  flow (decided 22.10.2026, at implementation).
- No nested interactive elements inside the card.
- The `"detail"` variant is not a link.
- Variant B (two buttons per card) was rejected: heavier cards, fewer
  on screen.

**Open item.** No navigation leads to `/specialists` or `/services` at
all. Site navigation belongs to the design pass (Stages 18-21).

### Open items (not decisions)

- Stage 22 requirements: the production server must support streaming
  (threaded workers, no proxy buffering); decide Redis persistence, since
  snapshots could hold conversation text.
- A privacy policy page must exist before the assistant is enabled for
  real customers.
- Check whether the public slots endpoint and appointment creation verify
  that the specialist performs the service (the availability engine does
  not). Found 21.10.2026. Checked 22.10.2026: they do not; the fix is
  decided in "Fix: the specialist must perform the service (decided
  22.10.2026)" above.
- `SpecialistsAtTimeQuerySerializer` (`scheduling/serializers.py` ~93)
  still binds `Service.objects.all()`. Low risk: it only returns linked
  specialists, and booking step 4 already 404s for an inactive service.
  Make it active-only in a separate small fix. Found 22.10.2026.
