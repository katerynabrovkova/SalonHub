# Backend Architecture

This document is the detailed design for the backend, produced in Stage 1 (see the
"Agreed stage order" in `docs/DECISIONS.md`). It describes *how* the system is built;
`docs/DECISIONS.md` remains the source of truth for *why* the foundational choices
(multi-tenancy shape, identity model, timezone, currency, payments/notifications
provider strategy, frontend cadence) were made — this document references those
decisions rather than restating them, and goes one level deeper into structure and
mechanism.

Stages 1–3 (domain models, and auth/roles/tenant isolation/guest identity) are done;
this document is revised as later-stage implementation surfaces problems with it. The
concrete numbers referenced throughout (deposit percentage, cancellation cutoff,
booking window limits, payment hold duration) live in `docs/DECISIONS.md` § Business
rules, not here.

---

## 1. Django project structure and app boundaries

One Django project (`config`, already scaffolded), domain-bounded apps added as their
stage arrives. Proposed app list and responsibility:

| App | Responsibility |
|---|---|
| `core` | Cross-cutting infrastructure with no domain meaning of its own: the tenant-scoped abstract base model and manager/queryset, the domain exception hierarchy, shared permission base classes, common mixins (e.g. a `TimeStamped` abstract base with `created_at`/`updated_at`), shared pagination/error-envelope config. |
| `tenants` | `Salon` — the tenant root entity — plus salon-level settings (timezone, business hours defaults, deposit percentage — single salon-wide value, no per-service override — booking lead-time/advance-window limits) and the tenant-resolution middleware. |
| `accounts` | `User` (platform/admin auth only), `Account` (per-salon login credential every JWT authenticates as), `Customer` (per-salon identity, guest or registered, see `docs/DECISIONS.md` § Identity), salon-scoped registration/login/verification. |
| `catalog` | `ServiceCategory`, `Service`. |
| `specialists` | `Specialist`, which services a specialist performs, `WorkingHours` (recurring weekly schedule), `TimeOff` (dated exceptions). |
| `scheduling` | The availability engine: pure, stateless computation of open slots from `specialists` schedule data and `booking` appointments. Owns no models of its own (see § 6). |
| `booking` | `Appointment`, booking creation/cancellation service layer, concurrency guarantees. |
| `payments` | `Payment` (deposit lifecycle), the `PaymentProvider` interface, the mock adapter, webhook ingestion, refunds. |
| `notifications` | `Notification` (send log / idempotency record), the `NotificationChannel` interface, the email adapter (Telegram adapter added later, same interface). |
| `reviews` | `Review`, completed-appointment eligibility gating. |
| `ai_assistant` | Grounding tool functions, propose-only booking handoff. Owns no persistent models — conversation state lives in Redis (see § 10). |

Rationale for boundaries: each app maps to one bounded context from the product
requirements, matching the stage order in `docs/DECISIONS.md` (each app's first
version lands in its own stage). `scheduling` is split out from `specialists` even
though it *reads* specialist data, because it has a fundamentally different nature
(pure computation, heavily unit-tested, no persistence) and its own stage (6) — mixing
it into `specialists` would blur "data" from "algorithm."

**Assumption:** `WorkingHours`/`TimeOff` belong to `specialists` (they describe a
specialist's own schedule), not to `scheduling`. This is a reversible internal
boundary, not called out as an open question.

## 2. Domain entities and relationships

Described, not modeled in code:

- **Salon** — tenant root. Everything else scopes to it via a `salon` FK. `is_active`
  (default `True`) gates tenant resolution — an inactive or unknown slug resolves the
  same way, `404` (§ 3, `docs/DECISIONS.md` § Stage 3 decisions). `name` and `about`
  (salon-profile free text, blank/optional, added in Stage 11.5) are translatable
  `JSONField`s — `{lang_code: string}` dicts, `en` fallback — per § Content
  localization.
- **User** — Django auth identity, platform-wide (not salon-scoped). Post-3-R it
  authenticates only via the session-based Django `/admin/` (platform operators) — it
  is not the customer or salon-staff login. `email_verified_at` (nullable) has no
  write path anymore: the registration/verification flow (§ 3) runs against
  `Account.email_verified_at` instead.
- **Account** — per-salon login credential (`AbstractBaseUser` + `TenantScopedModel`);
  every JWT authenticates as this, not `User` (§ 3). Fields: `email`, `role`
  (`AccountRole`: `client`/`admin`), `is_active`, `email_verified_at`, `customer`
  (nullable `OneToOneField`→`Customer`, `SET_NULL`) — the link from a login to its
  per-salon `Customer` row runs this direction, not a FK on `Customer`.
- **Customer** — per-salon identity, guest or registered, per `docs/DECISIONS.md` §
  Identity. All customer-facing domain rows (appointments, reviews) hang off this, not
  off `User` or `Account`. A registered customer is linked from an `Account` via
  `Account.customer` (above), not via any FK on this model.
- **ServiceCategory** — salon FK, name, ordering. `name` is a translatable
  `JSONField` (Stage 11.5, § Content localization).
- **Service** — salon FK, category FK, name, duration, price, buffer minutes (time
  blocked on the calendar after the appointment for cleanup/room turnaround, never
  itself offered as a bookable start — see § 6–7; kept per-service, not salon-wide,
  because turnaround differs by service). `name` is a translatable `JSONField`
  (Stage 11.5, § Content localization).
- **Specialist** — salon FK, name, bio, is_active (Stage 5). No login in this build
  (`docs/DECISIONS.md` § Business rules). `bio` is a translatable `JSONField`
  (Stage 11.5, § Content localization).
- **`Service` and `Specialist` rows can never be hard-deleted once an `Appointment`
  references them** — the FK would either cascade-delete real booking history or
  require `SET NULL`/`PROTECT` gymnastics that make "delete" mean something
  different depending on whether the row has ever been booked. `Service` and
  `ServiceCategory` got an `is_active` soft-deactivation field in Stage 4;
  `Specialist` gets its own `is_active` in Stage 5, not at Stage 20 as
  originally planned here (see `docs/DECISIONS.md` § Stage 5 decisions for why
  that moved earlier). Its meaning on `Specialist` is employment status only —
  True currently employed, False no longer employed — not a general
  availability/visibility flag: temporary absence (vacation, sick leave,
  parental leave) is modelled as `TimeOff` rows, never as a status on
  `Specialist`. For these three models, soft deactivation means flipping
  `is_active`, never a `DELETE` of a row an `Appointment` might reference.
  Deactivating a `Specialist` is additionally refused outright (409) while
  they still have a future non-cancelled `Appointment` — Stage 5 has no
  payment/notification/conflict-resolution layer yet to honor the standing
  rule that such a conflict be explicitly resolved, never silently orphaned
  (`docs/DECISIONS.md` § Business rules, § Stage 5 decisions); revisit at
  Stage 19.
- **Specialist ↔ Service** — many-to-many via an explicit `SpecialistService`
  through model, not a bare `ManyToManyField` (not every specialist performs
  every service) — the explicit through model is needed so the pairing
  itself can carry the composite cross-tenant FK protection on both sides
  (§ 5).
- **WorkingHours** — specialist FK, day-of-week, start/end time (recurring weekly
  template).
- **TimeOff** — specialist FK, date range, reason (exception to `WorkingHours`).
- **Appointment** — salon FK, customer FK, specialist FK, service FK, start/end
  timestamp (UTC) for the service itself, `blocked_until` (UTC — `end` plus the
  service's buffer minutes at the time of booking, stored rather than recomputed via
  a join; see § 7), `service_price_at_booking` and `deposit_percentage_at_booking`
  (the `Service.price` and `Salon.deposit_percentage` values at the moment of
  booking, snapshotted onto the row for the same reason as `blocked_until` — if the
  salon later changes a price or the deposit percentage, an already-made booking's
  amount owed must not silently change with it; § 8's deposit formula reads these
  snapshots, never the live `Service`/`Salon` values), `hold_expires_at` (set at
  creation for `PENDING_PAYMENT` appointments — see § lifecycle), status (see §
  lifecycle), audit fields (`created_at`, `cancelled_at`, `cancelled_by`,
  `cancellation_reason`).
- **GuestAccessToken** — salon FK, appointment FK, `token_hash` (SHA-256 of the
  signed token, never the raw value), `expires_at`, `cancelled_via_token_at`
  (nullable — separate from `expires_at` so cancelling doesn't revoke view access;
  see § 3). Lives in `booking`, not `accounts` (`docs/DECISIONS.md` § Stage 3
  sub-step 3 decisions).
- **Payment** — salon FK, appointment FK (one deposit payment per appointment),
  amount, status (see § lifecycle), provider reference id.
- **ProcessedWebhookEvent** — provider event id (unique), processed timestamp. Not a
  domain entity as such — an idempotency ledger for § 8/9.
- **Notification** — salon FK, recipient (customer or user), channel, trigger type,
  natural key for dedup (see § 9), status, sent timestamp.
- **Review** — salon FK, appointment FK (**one-to-one** — see § 11), customer FK,
  denormalized specialist FK (grouped-by-specialist read path; snapshot of who
  performed the visit — see § 11), rating, text, created_at.
- **AI assistant conversation state is not a persisted entity at all** — held in
  Redis with a TTL, not a database table. See § 10.

**Every tenant-owned model gets its own direct `salon` FK**, even where it is
technically derivable through a join (e.g. `Payment` could derive `salon` via
`Appointment`, `Review` via `Appointment`). This is deliberate, not denormalization
for its own sake — see § 5 for why.

## 3. Authentication architecture, including guest identity

Two separate identity tracks, per `docs/DECISIONS.md` § Identity:

**Accounts (`Account`).** Standard email + password, issued JWTs via DRF SimpleJWT,
authenticated by `AccountJWTAuthentication` (`accounts/authentication.py`) — the only
JWT auth path in the API (`DEFAULT_AUTHENTICATION_CLASSES`). Login, refresh and logout
are salon-scoped (`/api/v1/salons/<slug>/auth/login|refresh|logout/`, landed in Stage
3-R.E). Every issued token carries an `identity_model: "account"` claim, checked
*before* any pk lookup: `User` and `Account` are separate tables with independent
auto-increment sequences that can legitimately share a pk, so this guards against a
still-valid legacy token resolving against the wrong table. Once the claim passes,
`Account.objects.get(pk=...)` resolves the token tenant-scoped, and `request.user` is
always an `Account` — never a `User`. `User` authenticates only via the session-based
Django `/admin/`; there is no `User` JWT path anywhere in the API.

**Guests (a `Customer` with no linked `Account`).** No login at all. At booking time, the
customer supplies name/email/phone; the `accounts` service layer does a `salon`-scoped
get-or-create on `Customer` by email (per `docs/DECISIONS.md`, email is unique per
salon, so a returning guest updates their existing row). The confirmation email links
to a frontend route with a signed token (Django's `core.signing`, keyed off
`SECRET_KEY`, encoding the `Customer`/`Appointment` id) in the URL **path**
(`{FRONTEND_URL}/salons/<slug>/appointments/<id>/manage/<token>/`), never a query
string or fragment — this is a persistent, long-lived resource link, not a
one-time credential (`docs/DECISIONS.md` § Step (d) decisions). How the frontend
reads the token out of that route and presents it to the API is a Stage 18-21
frontend detail, not yet specified. A `GuestAccessToken` row stores the SHA-256
hash of the signed token (`token_hash`), an `expires_at` (30 days after
`Appointment.end_datetime`), and a nullable `cancelled_via_token_at`. The API
re-hashes the presented token, looks it up by `token_hash`, and checks
`expires_at`. Viewing only requires a valid, unexpired token;
cancelling additionally sets `cancelled_via_token_at`, which is checked independently
of expiry — a cancelled appointment stays viewable through the same link.

**Guest → registered linking.** Registration creates an `Account` (`role = client`,
the default) directly — never a `User`. Linking happens at verification, not
registration: `VerifyEmailView` stamps `Account.email_verified_at` and, only if the
account has no `Customer` yet, looks for a `Customer` row in *that same salon*
matching the verified email and adopts it (`Account.customer` set) — same-salon and
email-matched, never a cross-salon sweep keyed off a `User`'s email. The
`Customer.user` field and the cross-salon `link_guest_customers` sweep this paragraph
used to describe were both removed (Stage 3-R.D.2; the field itself, migration
`0006_remove_customer_user`).

**Salon staff.** A per-salon `Account` (§ 4) carrying `role = admin`; back-office
authorization is that role, not a separate auth mechanism. (`SalonStaff`, the former
`User` × `Salon` join, was removed in Stage 3-R.B — see `docs/DECISIONS.md` § Stage 3-R.)

## 4. Authorization: roles, permission classes, reach

Roles:

- **Anonymous / Guest** — no `User`. Reach limited to public catalog browsing, guest
  booking creation, and the signed-token view/cancel flow for their own appointment.
- **Customer** (an `Account` with `role = client` whose `Account.customer` points at
  a `Customer` row in the target salon) — everything Guest can do, plus: view booking
  history, leave reviews.
- **Salon admin** (a per-salon `Account` with `role = admin`) — back-office reach for
  that one salon only: catalog, specialists, schedules, appointments, clients,
  reviews, notifications. **One staff role for v1**; `Account.role` is kept open-ended
  so a second, more restricted role can be added later without a shape migration
  (`docs/DECISIONS.md` § Stage 3-R decisions).
- **Platform superuser** (Django `is_superuser`, not tenant-scoped) — Django admin
  only, for operational access across all salons. Not part of the product UX.

Permission classes (DRF, added when the API lands):

- `IsSalonStaff(*roles)` — checks `request.user` is an `Account` carrying a staff role
  for the resolved tenant (§ 5); empty `*roles` means the staff-role set (`{admin}` for
  v1), or pass explicit roles to narrow it. The `isinstance` check against `Account` is
  also the token-type guard for the `USER_ID_FIELD` cross-model collision
  (`docs/DECISIONS.md` § Stage 3-R decisions).
- `IsAuthenticatedCustomer` — checks `request.user` is authenticated and has a
  `Customer` row in the resolved tenant (§ 5).
- `IsOwnCustomer` — object-level check that the acting `Customer` (from the JWT or
  a validated guest token) matches the object's `customer` FK.
- `HasValidGuestToken` — validates the signed token from § 3 in `has_permission` (not
  `has_object_permission`, so the check runs regardless of view shape — see
  `CLAUDE.md`'s "DRF object-level permissions" rule); the target object is then
  resolved *from* the validated token, never validated against a separately-sourced
  (e.g. URL-supplied) id.

These are a second line of defense on writes; the tenant-scoped manager (§ 5) is the
first line, so a missing permission check fails toward "wrong data invisible," not
"wrong data mutable."

## 5. Multi-tenancy: making the unscoped query the hard path

Building on `docs/DECISIONS.md` § Multi-tenancy (shared schema, row-level isolation,
path-prefix resolution):

- `core.TenantScopedModel` — abstract base with `salon = ForeignKey(Salon, ...)`,
  used by every tenant-owned model.
- `core.TenantScopedManager` / `QuerySet` — the model's **default** manager
  (`objects`). It reads the current tenant from a context variable set by the
  resolution middleware for the duration of the request and auto-filters every query
  against it. The resolution middleware is last in `MIDDLEWARE`, so the tenant
  context is bound only around the view, not around any other middleware's
  request/response processing. If no tenant context is bound, `objects` **raises** rather than
  returning an unfiltered queryset — there is no silent "all tenants" default.
  Deliberately bypassing scoping (Django admin, platform-level tooling, Celery tasks
  that operate across salons) requires calling `Model.unscoped_objects` — a
  differently-named, greppable manager — never a flag or kwarg on `objects` itself.
  A one-word manager name that shows up in a repo-wide search is the point: nobody
  reaches for it by accident. `core.admin.SalonScopedAdmin` is the concrete Django
  admin implementation of this: it overrides `get_queryset` (list views) and
  `formfield_for_foreignkey` (add/change form FK dropdowns onto another tenant-scoped
  model) to route through `unscoped_objects`, since admin requests never bind tenant
  context the way `/api/v1/salons/<slug>/...` requests do.
- **Celery tasks have no request, so no middleware.** Any task that touches
  tenant-scoped models must explicitly bind the tenant context at the top of the
  task (e.g. a `@tenant_context(salon_id)` decorator wrapping the context-var
  set/reset), or must operate salon-by-salon in an explicit loop over
  `Salon.objects.all()` for cross-tenant sweeps (e.g. the appointment-expiry sweep in
  § 12). This is a rule to enforce in code review, not something the framework can
  fully guarantee — flagged explicitly so it isn't improvised per-task.
- **Business uniqueness constraints are scoped per salon at the DB level**, not just
  in application code — e.g. `Customer` email unique-together with `salon` (already
  decided). This generalizes: any future "unique" requirement gets `salon` added to
  the constraint, never a bare global-unique field on a tenant-owned model.
- **Cross-tenant FK assignment** (e.g. an `Appointment` pointing at a `Specialist`
  from a different salon than the `Appointment.salon` itself) is the sharpest edge
  case here. Two layers: the service layer validates every FK target's `salon`
  matches before saving; at the DB level, the implemented pattern is a composite
  unique index `(id, salon)` on parent tables plus a composite FK `(child.parent_id,
  child.salon) → (parent.id, parent.salon)` on children (`core/db.py`'s
  `composite_tenant_fk`, 14 call sites across migrations as of Stage 3) — this makes
  cross-tenant linkage a constraint violation, not just an application bug waiting to
  happen.

## 6. Availability computation

**Inputs:** the target specialist's `WorkingHours` (recurring) and `TimeOff`
(exceptions); existing non-cancelled `Appointment`s for that specialist in the
requested date range, read as `(start, blocked_until)` busy intervals — i.e.
duration **plus buffer**, not just duration (see below); the requested `Service`'s
duration and `buffer_minutes`; the salon's slot granularity, minimum booking lead
time, and maximum advance-booking window (all salon-level settings — defaults
recorded in `docs/DECISIONS.md` § Business rules); the salon's timezone (for
local-time display); the current time.

**Algorithm shape** (pure, no side effects): for a given specialist + service + date
range —

1. Build the specialist's open windows for the range from `WorkingHours` —
   overlapping rows for the same specialist/day are merged into disjoint windows
   first — minus every overlapping interval from a generalised blocking-interval
   list, subtracted in full whether it clips an edge, splits the window, or
   covers it entirely (currently just `TimeOff`; a future closure source is one
   additional entry in that list, not a rewrite of this step — see
   `docs/DECISIONS.md` § Stage 6 decisions and § Open questions).
2. Subtract busy intervals from existing appointments in that range, using each
   appointment's `(start, blocked_until)` — the buffer is calendar-blocking time, so
   it occupies the busy interval exactly like the service itself does.
3. Walk the remaining windows in slot-granularity steps, keeping any candidate start
   time that has the full **service duration plus that service's `buffer_minutes`**
   free before the next busy interval or window end. The buffer following a
   candidate slot is never itself offered as a start time for another appointment —
   a following appointment may start exactly when the buffer ends.
4. Drop candidates violating minimum lead time or the max-advance-booking window.
5. Convert to salon-local time (`zoneinfo`) for the response.

Concretely: a 90-minute service with a 15-minute buffer blocks 105 minutes of the
specialist's calendar. The client is only ever offered the 90-minute window itself
as a bookable start — never a start time that would fall inside someone else's
buffer.

**Multi-specialist composition:** the algorithm above always takes a specific
specialist. A separate, thin composition function in the same `scheduling` app
handles the case where no specialist is specified — it enumerates the service's
`SpecialistService`-qualified, active specialists and unions each one's
per-specialist availability (`docs/DECISIONS.md` § Stage 6 decisions).

There are two booking entry paths into this engine, differing only in the
wrapper above it: an **"any specialist"** path, which reads through the
composition function above (a time is available if at least one qualifying
specialist is free then), and a **"specific specialist"** path, which calls
the per-specialist algorithm directly with no composition. In the
"any specialist" path, which specialists are actually free at each time is
computed as part of the union but served separately, from a second lookup
once a client has chosen a time, not inline with the first response
(`docs/DECISIONS.md` § Stage 6 and § Stage 6.G decisions).

**Exposed via one `GET` endpoint** taking the service, a date range, and an
optional specialist: present, it serves the specific-specialist path above
directly; absent, it serves the any-specialist composition's result. Both
return the same shape — a list of candidate start times converted to the
salon's local time — never a list of specialists; which specialists are
actually free at a chosen time is the second, separate lookup already
described above. Endpoint path, status-code mapping, and permission class are
Catalog-precedent detail (`docs/DECISIONS.md` § Stage 4 decisions' endpoint
lists), not restated here — see `docs/DECISIONS.md` § Stage 6.I decisions.

**Where it lives:** `scheduling`, as a stateless service function, not a persisted
"slots" table. This is a deliberate correctness-over-performance call: a materialized
slot table needs invalidation on every write to `WorkingHours`, `TimeOff`, and
`Appointment`, and a missed invalidation path is a silent stale-availability bug. If
computing on demand ever becomes a real bottleneck, the fix is a cache invalidated by
those writes, not a change in what the source of truth is.

**Edge cases:**

- Two concurrent requests computing the "same" slot as available — this function
  is read-only and can't fully guard against that; the actual guard is at booking
  time (§ 7), not here.
- A specialist's `WorkingHours` changes after appointments already exist outside the
  new hours — existing appointments are not retroactively invalidated.
- `TimeOff`/`WorkingHours` changes that conflict with a slot that's already booked —
  this function only stops offering that slot going forward; it does not resolve
  the existing conflict. Detection and resolution (rebook with another specialist,
  rebook the same specialist later, or a fully-refunded salon-initiated
  cancellation) is a recorded requirement (`docs/DECISIONS.md` § Business rules),
  with the resolution UI landing in the admin calendar (Stage 19).
- A service duration longer than any single open window never produces a slot —
  correct behavior, not a bug.
- Specialists working across midnight — assumed not to happen (salons close
  nightly); flagged as an assumption, not built for.
- DST transitions — `WorkingHours.start_time`/`end_time` are salon-local
  wall-clock time with no date attached, so building step 1's UTC windows
  requires a DST-aware `zoneinfo` localization against the specific calendar
  date being computed, not a fixed offset — the same DST-aware conversion
  also applies when converting final candidate slots back to local time for
  the response (step 5). Handling this only at display time and building
  step 1's windows in naive UTC would be wrong on a transition day
  (`docs/DECISIONS.md` § Stage 6 decisions).

## 7. Booking: transaction boundaries and double-booking prevention

Booking creation is wrapped in a single `transaction.atomic()` block: re-validate the
requested slot is still free, create the `Appointment` in `PENDING_PAYMENT` (see §
lifecycle), all inside one transaction.

**Double-booking prevention has two layers:**

1. **Application-level re-check** — immediately before insert, re-run a narrow
   availability check scoped to just that specialist and time window. Cheap, and
   gives a fast, friendly error for the common case.
2. **Database-level guarantee** — a Postgres exclusion constraint (`EXCLUDE USING
   gist`, requiring the `btree_gist` extension) on
   `(specialist_id, tsrange(start, blocked_until))` filtered to non-cancelled
   statuses. **The constraint operates on the buffered interval, not the bare
   service interval** — `blocked_until` already includes the service's buffer
   minutes (§ 2), stored on the `Appointment` row itself at creation time rather
   than recomputed via a join to `Service`, so the constraint only ever reads a
   column on the row it's checking. This is the actual race-condition guard: two
   concurrent requests can both pass the application-level check before either
   commits, and only the DB constraint is guaranteed to see both attempts
   serialize.

Why not rely solely on `select_for_update()`: there's no existing row to lock for a
not-yet-created appointment. A per-specialist advisory lock
(`pg_advisory_xact_lock`) held for the transaction was considered as an alternative
or supplement, but the exclusion constraint is preferred as the primary guard because
it's declarative and holds even if some future code path forgets to take the lock —
defense that doesn't depend on every caller remembering it.

On constraint violation, the transaction rolls back and the API returns a
`SLOT_NO_LONGER_AVAILABLE` domain error (§ 14) — the client's "you lost the race" case.

**Cancellation** transitions `Appointment` to `CANCELLED`, recording who cancelled
(`cancelled_by`: customer, guest via token, staff, or system) and why. Refund
eligibility branches on `cancelled_by` first, then, only for a customer-initiated
cancellation, on a cutoff against `now()` vs. appointment start — a salon-initiated
cancellation is always fully refunded regardless of timing. The concrete cutoff and
the full policy are recorded in `docs/DECISIONS.md` § Business rules, not restated
here.

## 8. Payments: state machine, deposit calculation, webhook idempotency

**Deposit calculation:**
`deposit_amount = round(appointment.service_price_at_booking ×
appointment.deposit_percentage_at_booking)` — read from the snapshot stored on the
`Appointment` at booking time (§ 2), never from the live `Service.price` or
`Salon.deposit_percentage`, so a later price or deposit-percentage change never
rewrites what an existing booking owes. The percentage itself is a single
salon-wide setting, no per-service override in v1 (`docs/DECISIONS.md` § Business
rules).

**Payment/deposit lifecycle** (separate from the appointment lifecycle — see the
combined state-machine section below).

**Webhook idempotency:** the payment provider (mock now, Stripe-shaped later) may
redeliver the same event. Every inbound webhook event id is recorded in
`ProcessedWebhookEvent` (unique constraint on `provider_event_id`) before any state
change is applied; a replayed event id is a no-op. Additionally, the state-transition
handlers themselves are idempotent (e.g. `PENDING → SUCCEEDED` applied twice is a
no-op, not an error) — belt and suspenders, since at-least-once delivery is the
standard guarantee providers like Stripe actually offer, not a hypothetical.

The remaining balance (80% of price) is paid in person at the salon and is not
tracked or collected by the platform in any form (`docs/DECISIONS.md` § Business
rules).

## 9. Notifications: channel abstraction, duplicate-send prevention

Building on `docs/DECISIONS.md` § Notifications (channel abstraction from day one,
email adapter first): a `NotificationChannel` interface with a `send(notification)`
method, one adapter per channel.

**Trigger points:** booking confirmed, booking cancelled, appointment reminder
(scheduled, § 12), payment succeeded/failed, review request after an appointment
completes. **Guest→User email verification and password reset** (§ 3) are not
routed through this abstraction for Stage 3 — they're sent by a standalone Celery
task in `accounts/tasks.py` (plain `django.core.mail.send_mail`), with none of the
dedup/idempotency machinery below. Routing them through `Notification` is deferred
to Stage 9: `Notification.salon` is non-nullable, and verification/reset are
salon-less `User` events.

**Duplicate-send prevention** uses the same idempotency shape as § 8: a `Notification`
row is created in `PENDING` status *before* any send is attempted. `Notification.
appointment` is **nullable** — a salon-less trigger (e.g. a future, Stage-9-routed
verification email) would have no appointment to hang off — so the dedup natural key
can't be `(trigger type, appointment id, channel)` as originally stated: a nullable
column doesn't enforce uniqueness in Postgres (multiple `NULL`s never collide).
Instead the unique constraint is on `(trigger type, channel, dedup_key)`, where
`dedup_key` is a plain string the caller populates — `f"appointment:{id}"` for the
appointment-scoped triggers this app currently handles — so every trigger type has
an explicit, always-populated
collision key regardless of what it's actually about. The sending task only
transitions `PENDING → SENT` under that row's lock, so a retried Celery task or a
duplicate trigger (e.g. a webhook firing twice) cannot produce two emails for the same
event. Reusing the same pattern in two independent apps (`payments`, `notifications`)
is intentional consistency, not coincidence.

**Message language (Stage 11.5):** `_build_message` renders each notification in the
recipient's language (`Customer.preferred_language`, `en` fallback) — the templates in
`_MESSAGES` and the dynamic builders become per-language-keyed. Human-readable dates in
those emails come from `core/formatting.py`, which carries hardcoded English **and**
Ukrainian weekday/month tables (never the OS locale) and selects one by language; the
component order is identical across languages. See § Content localization.

## 10. AI assistant: grounding, session memory, booking handoff

**Grounding:** the assistant is grounded in the salon's real catalog via read-only
tool functions the LLM can call (`list_services`, `check_availability`,
`get_specialist_info`, ...) rather than the full catalog being stuffed into the
prompt. This keeps context small and always current — catalog edits take effect
immediately, with no re-embedding or prompt-template step.

**Session memory is Redis, not a database table — no `AIConversationSession` or
`AIMessage` models exist.** The client is issued an opaque session id (cookie or
local storage); conversation turns for that id are stored as a Redis value with a
sliding TTL, refreshed on each new message up to a fixed inactivity cutoff (e.g.
expire 30 minutes after the last message). When the TTL lapses, Redis drops the key
itself — no cleanup task, no retained history, nothing left to query later. This
applies uniformly to every customer, including logged-in ones; there is no
"logged-in customers keep their history" exception. Salon chat routinely surfaces
health-adjacent personal information (skin conditions, allergies, treatment
contraindications), and the deliberate choice is to not retain that for anyone by
default, not to make retention opt-in per user type (`docs/DECISIONS.md` § Business
rules). Redis is already in the stack for Celery, so this adds no new
infrastructure.

**Booking handoff is propose-only.** The assistant can call the same read-only
grounding tools to identify a candidate service and slot and present it to the user,
but it has no tool that calls the appointment-creation service function directly —
turning a proposal into a real appointment always goes through the normal booking
flow and its service layer (§ 7). This is a firm decision: a hallucinated or
malformed parameter must never be able to produce a real, paid appointment.

## 11. Reviews: eligibility gating

A `Review` may be created for any `Customer` — guest or Account-linked — that has
an `Appointment` in `COMPLETED` status. `Review.appointment` is a **one-to-one** FK,
not a general "has this customer ever visited" flag — this ties a review to a specific
service experience and caps it at one review per completed visit, rather than one
review per customer-salon relationship ever. The one-to-one appointment anchor is also
the anti-spam barrier, which is why no moderation layer exists.

`COMPLETED` is reached automatically (§ 12), specifically so review eligibility
never depends on a staff member remembering to mark a visit done; staff retain a
manual override for correcting mistakes. Reviews are **immutable after posting**, the
salon cannot post a public reply in v1, and deletion is exposed to no one (`PROTECT`).
Reviews are displayed grouped by specialist; `Review` carries a denormalized
`specialist` FK (a snapshot of who performed the visit) so that read path does not
join through the appointment.

## 12. Background tasks: scheduled vs. event-driven

**Event-driven** (Celery task fired by a service-layer call, not polling): booking
confirmation/cancellation email, payment webhook processing, review-request email
(fired the moment an appointment is marked `COMPLETED`).

**Scheduled** (Celery beat): appointment reminders (a single day-before reminder, hourly sweep),
expiring `PENDING_PAYMENT` appointments once `hold_expires_at` passes (hold
duration recorded in `docs/DECISIONS.md` § Business rules), transitioning
`CONFIRMED` appointments to `COMPLETED` once `end_datetime` passes (with a staff
override available in the admin, per § 11), sweeping abandoned payment sessions to
`EXPIRED`.

The split follows directly from whether there's an event to hook into: anything that
must react immediately to a state change is event-driven; anything driven purely by
the passage of time, with nothing that "happens," has to be polled on a schedule
because there is no trigger to attach to.

## 13. API surface: versioning, URL shape, pagination, filtering, error format

- **Versioning:** URL path versioning, `/api/v1/...`. Chosen over header-based
  negotiation for explicitness and cacheability — the version is visible in every
  request without inspecting headers.
- **URL shape:** essentially everything lives under `/api/v1/salons/<slug>/...`,
  consistent with the path-prefix tenant resolution in `docs/DECISIONS.md` — this
  includes auth (register, verify-email, password-reset, password-reset/confirm,
  resend-verification, login, refresh, logout; landed across Stage 3-R.D/3-R.E)
  alongside the domain resources. The only paths outside a salon prefix are `/admin/`
  (Django's session-based admin) and `/api/v1/webhooks/...` (payment provider
  callbacks, inherently cross-tenant). There is no flat `/api/v1/auth/...` prefix — it
  was fully removed in 3-R.E — and no `/api/v1/me/...` endpoint; it was never built.
- **Pagination:** `PageNumberPagination`, a fixed default page size, overridable up to
  a capped maximum (so `?page_size=100000` can't be used to force an unbounded
  response). Chosen over offset/limit for predictability in admin list UIs (page N of
  appointments/clients).
- **Filtering:** `django-filter` for structured list-endpoint filters (status, date
  range, specialist, service). **Not yet in `backend/requirements/*.txt`** — flagged
  here so it's added deliberately when the stage that needs it starts, not silently.
- **Error format:** a consistent JSON envelope —
  `{"error": {"code": "...", "message": "...", "details": {...}}}` — with a
  machine-readable `code` per domain error type (e.g. `SLOT_NO_LONGER_AVAILABLE`), so
  the frontend branches on semantics, not on parsing the human-readable message.

## 14. Error handling strategy and exception hierarchy

A small hierarchy of domain exceptions lives in `core.exceptions`
(`DomainError` base; e.g. `TenantMismatchError`, `SlotUnavailableError`,
`InvalidStateTransitionError`, `PaymentProviderError`), raised by the service layer.
The service layer raises these — never DRF's `ValidationError` directly — so that
layer stays framework-agnostic and is callable identically from API views, Celery
tasks, and the AI assistant's tool functions (this is what makes the "service layer,
not views" rule in `docs/DECISIONS.md`/`CLAUDE.md` actually hold in practice, not just
on paper).

A DRF `EXCEPTION_HANDLER` is the single place that translates `DomainError`
subclasses into the § 13 JSON envelope and the right HTTP status — that translation
happens once, at the API boundary, not scattered per-view. Unhandled/unexpected
exceptions become a generic 500 body in production (no stack trace leaked to the
client); the full traceback is still logged server-side.

---

## Content localization

Salon-authored, client-facing text is translatable (Stage 11.5). Supported languages
are `en` and `uk` for now, with `en` as the single global fallback; there is no
per-salon default-language field. Each translatable field is a `JSONField` storing a
`{lang_code: string}` dict rather than per-language columns or a translation table, so
adding a language needs no migration. Translatable fields: `ServiceCategory.name`,
`Service.name`, `Salon.name`, `Salon.about` (new, 2000-char-per-language DB check),
`Specialist.bio`, and the notification message templates (§ 9). Not translatable —
customer-authored or internal-only: `Review.text`, `TimeOff.reason`,
`Appointment.cancellation_reason`.

`(salon, name)` uniqueness on `ServiceCategory` / `Service` is checked per populated
language key, not against one canonical language — a value shared under the same key
within a salon is a conflict. The email language a customer receives is
`Customer.preferred_language` (new field, set at booking, `en` fallback). Date
rendering for emails lives in `core/formatting.py`, now with English and Ukrainian
weekday/month tables (still never OS-locale dependent).

Out of scope: frontend UI-string translation (a separate frontend i18n concern) and
any staff/admin interface language. Full contract: `docs/DECISIONS.md` § Stage 11.5.

---

## State machines

Modeled separately, as required — an appointment can be `CONFIRMED` while its payment
is independently mid-refund, and conflating the two into one status field would make
either dimension unrepresentable in some states.

### Appointment lifecycle

```
PENDING_PAYMENT --(payment succeeded)--------> CONFIRMED
PENDING_PAYMENT --(cancelled before paying)--> CANCELLED
PENDING_PAYMENT --(hold_expires_at passed)---> EXPIRED
CONFIRMED       --(cancelled before start)---> CANCELLED
CONFIRMED       --(end_datetime passed)------> COMPLETED
CONFIRMED       --(staff marks no-show)------> NO_SHOW
```

`CANCELLED`, `EXPIRED`, `COMPLETED`, `NO_SHOW` are terminal — no transitions out.
`NO_SHOW` has no automatic effects in v1 — the deposit is already forfeited by
appointment start regardless (per the cancellation policy), and there is no further
customer penalty. It exists for admin record-keeping and statistics only
(`docs/DECISIONS.md` § Business rules).

A slot is reserved (visible to the double-booking exclusion constraint, § 7) as soon
as `PENDING_PAYMENT` is created, not only once payment succeeds — otherwise two
customers could be mid-checkout for the same slot simultaneously with no guard at all.
`hold_expires_at` is set at creation (`created_at` plus the hold duration recorded in
`docs/DECISIONS.md` § Business rules); the `EXPIRED` transition (§ 12) is what
releases a held slot once that passes without a completed payment.

### Payment / deposit lifecycle

```
PENDING     --(provider intent/session created)--> PROCESSING
PROCESSING  --(provider confirms)-----------------> SUCCEEDED
PROCESSING  --(provider declines)-----------------> FAILED
PROCESSING  --(checkout session times out)--------> EXPIRED
PENDING/PROCESSING --(appointment cancelled first)-> CANCELLED
SUCCEEDED   --(refund initiated)------------------> REFUND_PENDING
REFUND_PENDING --(provider confirms refund)-------> REFUNDED
```

`FAILED`, `EXPIRED`, `CANCELLED`, `REFUNDED` are terminal.

The two machines are coupled only at specific, explicit points: the webhook handler
that receives "payment succeeded" transitions **both** `Payment → SUCCEEDED` and
`Appointment → CONFIRMED` inside one transaction (§ 8/9's idempotency guard applies to
this handler); a cancellation initiated from the `Appointment` side while a `Payment`
is `SUCCEEDED` triggers a refund (`Payment → REFUND_PENDING`) rather than mutating
`Appointment` and `Payment` independently.

---

## Business rules

The concrete numbers this document's mechanisms depend on — deposit percentage,
cancellation cutoff, booking window limits, payment hold duration, and the rest —
are recorded once, in `docs/DECISIONS.md` § Business rules, not restated here, to
avoid a second source of truth.
