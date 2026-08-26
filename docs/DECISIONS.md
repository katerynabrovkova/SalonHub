# Architectural Decisions

This is the source of truth for architectural decisions on this project.
Read it before changing anything structural (tenancy, data model shape,
payment/notification abstractions, etc.). If a decision here needs to
change, update this file in the same change — do not let the code and this
document drift apart.

Decisions are recorded in the order they were made, not grouped by topic,
so the history stays legible.

---

## Agreed stage order

The project is built one stage at a time, in this order. Do not implement
ahead of the current stage. When a decision elsewhere in this file
references "Stage N," it refers to this list — keep those references in
sync if the order ever changes.

0. Scaffolding (done)
1. Detailed backend architecture, written to `docs/ARCHITECTURE.md`, no code (done)
2. Domain models + migrations (done)
3. Auth, roles, tenant isolation, guest identity (done)
4. Catalog (categories, services) (done)
5. Specialists — full CRUD (read public, writes gated by IsSalonStaff — same split as Stage 4 catalog) (done)
6. Availability engine — pure slot computation, read-only, heavily unit-tested
7. Booking core — creation, statuses, cancellation, concurrency
8. Payments — provider abstraction, deposit, webhooks, refunds
9. Celery + notifications (email + channel abstraction)
10. Telegram adapter
11. Reviews — read endpoints and submission (completed-appointment gating)
11.5. Content localization — per-salon language settings, translatable content
    across catalog, salon profile, and notification templates. Numbered 11.5,
    not renumbered into the sequence, so it doesn't invalidate every existing
    `Stage N` reference elsewhere in this file, `ARCHITECTURE.md`, and code
    comments for a placement that doesn't require it.
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

**Stage 3-R (per-salon identity revision, decided 2026-08-25 — see §
Stage 3-R decisions and § Stage 3-R reversals, near the end of this
file)** is inserted as a revision block, out of chronological order, the
same way Stage 11.5 was inserted without renumbering every existing
`Stage N` reference. It reopens closed Stage 3 identity work and must
land — model, migration, auth views, and the `docs/ARCHITECTURE.md`
follow-up rewrites its entries list — before Stage 9 (Celery +
notifications) resumes, since Stage 9's own work (the verification-email
trigger, the guest-token response-body reversal) depends on the final
client/admin identity shape, not the one that was on disk when Stage 9
was first scoped. Stage numbers 4–23 above are not renumbered by this
insertion.

## Open questions

Not yet decided — recorded here so they surface before the stages that depend
on them, rather than being forgotten and improvised in the moment.

- **Business model: one-off site sale vs. recurring SaaS subscription.**
  Affects the stages covering salon onboarding, plans, and billing. Decision
  needed before those stages.
- **Tenant resolution source: URL path prefix only, or also custom domain per
  salon.** Currently path prefix only (Stage 3 middleware, § Multi-tenancy
  below). Adding domain-based resolution would be additive to the existing
  middleware, not a rewrite. Decision needed before the public frontend stage.
- **Salon-level closure/holiday model.** No salon-wide closure exists today —
  only per-specialist `TimeOff` (§ Stage 6 decisions). Needed before the
  admin calendar stage (Stage 19/20), which is where staff would actually
  declare a closure.
- **Overlap-prevention validation on `WorkingHours`.** Nothing today stops
  two overlapping rows for the same specialist/day being saved (no
  uniqueness constraint, no `clean()`, admin-writable) — the availability
  engine defends itself by merging overlapping rows at ingestion time (§
  Stage 6 decisions), but the write side has no equivalent guard. Add
  validation when `WorkingHours` gains a real write API (Stage 20); until
  then the merge is the only protection.
- **Buffer-at-shift-end rule revisit.** Stage 6 requires the buffer to fit
  inside the specialist's own working window (§ Stage 6 decisions), because
  no salon-level closing time exists yet to compare against. Once salon
  working hours (or the closure model above) exist, the rule should become
  "buffer must fit before the earlier of specialist shift end and salon
  closing time," so a specialist who finishes before the salon closes
  doesn't lose a slot needlessly. Revisit alongside the closure model
  decision.
- **Blocker provenance through the availability engine.**
  `compute_open_windows` (§ Stage 6.D decisions) flattens every blocking
  source — `TimeOff`, `Appointment`, and any future closure source — into
  indistinguishable `Window`s before subtraction. If a future consumer
  (e.g. Stage 19's admin calendar, or the AI assistant) needs to explain
  *why* a specific slot is unavailable, this loses that information by
  design. Not solved now: the one imagined use case can read the
  underlying rows directly instead.
- **`Salon.timezone` write-time validation.** Nothing currently validates
  that `Salon.timezone` is a real IANA zone name at write time (model,
  serializer, or admin form) — a malformed value would only surface as an
  unhandled `zoneinfo.ZoneInfoNotFoundError` the first time
  `scheduling.compute_open_windows` tries to resolve it (§ Stage 6
  decisions), from the orchestrator specifically, by deliberate placement —
  not read-time defended against there either. Needs a decision on where the
  validation belongs, and whether a bad existing value should be caught and
  translated into a clearer error or left as an uncaught 500, since it would
  indicate corrupted configuration data, not user input.
- **Lower-bound validation on `Salon.slot_granularity_minutes` and
  `Service.duration_minutes` at the model level.** Today
  `Service.duration_minutes >= 1` is enforced only in `catalog/serializers.py`
  (`min_value=1`), not at the model/DB layer, and
  `Salon.slot_granularity_minutes` has no lower-bound enforcement anywhere —
  no serializer exists for `Salon` yet, and the admin form has no validator.
  `scheduling._step_windows` (§ Stage 6.E decisions) now guards against
  `granularity_minutes <= 0` / `occupied_minutes <= 0` at read time, but that
  guard exists precisely because the write side has no equivalent one; the
  real fix is closing the gap at the source. Needed by Stage 19/20, when
  salon settings (and possibly `Service`) get a write API.
- **`compute_candidate_start_times`'s `salon` argument is not checked
  against `specialist.salon`.** (§ Stage 6.E decisions.) The orchestrator
  takes `specialist: Specialist` and `salon: Salon` as two independent
  parameters; nothing in `scheduling/services.py`, `TenantScopedManager`, or
  anywhere else verifies the caller passed the specialist's actual salon.
  Passing a mismatched `salon` would silently resolve
  `granularity_minutes` from the wrong tenant's settings and localize the
  specialist's real `WorkingHours` against the wrong tenant's timezone —
  wrong output, not a crash, and nothing in the current test suite would
  catch it either since query counts and return values would both look
  superficially plausible. No guard added yet (deliberately, per the 6.E
  design decision) — needs a decision on whether this belongs in 6.E itself
  (e.g. `assert specialist.salon_id == salon.id`, or deriving `salon` from
  `specialist.salon` after all and accepting the extra query) or stays
  documented-only on the theory that every real caller already gets both
  objects from the same tenant-scoped request context and could not produce
  a mismatch without a bug elsewhere that unit tests on this function alone
  wouldn't be positioned to catch regardless. **6.F widens the blast
  radius:** a mismatched `salon` now also supplies the lead time and the
  max-advance window, on top of granularity and timezone. Also, correct the
  cost argument this entry originally implied: a guard does not cost a
  fourth query — `specialist.salon_id` is a plain column on the
  already-loaded row (verified in § Stage 6.F decisions), and only
  `specialist.salon` (the lazy relation) would fire one. The affordability
  objection therefore does not apply; what remains is a judgement call about
  likelihood, not cost.
- **Local-time presentation across a DST transition** — on a spring-forward
  day the candidate list jumps (…01:40, 02:00, 03:00, 03:20…) because 02:xx
  does not exist locally. Whether to show that as-is, label it, or suppress
  the affected candidates is a presentation decision belonging to the
  local-time formatting substage, not to stepping.
- **Max-advance boundary computed as local midnight can land on a
  non-existent local time.** (§ Stage 6.F decisions.) The boundary is
  midnight at the start of the day following `today + max_advance_days`, in
  the salon's timezone. In IANA zones whose DST transition happens exactly
  at midnight (e.g. `America/Santiago`, `America/Havana`), that local
  midnight does not exist on one day per year, and `zoneinfo` resolves a
  non-existent local time by shifting rather than raising — so the boundary
  would silently move by an hour for that salon on that day. Not defended
  against now: `Salon.timezone` defaults to `Europe/Kyiv`, which transitions
  at 03:00, not midnight. But `Salon.timezone` is already a per-salon field
  accepting any zone string (§ `Salon.timezone` write-time validation,
  above), so this does not require international expansion to trigger — a
  single salon configured with such a zone is enough. This is a different
  question from "Local-time presentation across a DST transition" directly
  above, which is about what to *display*; this one is about where the
  boundary silently *moves to*. Do not merge them.
- **Per-specialist service duration and the "any specialist" union rule.**
  Today `occupied_minutes` (`service.duration_minutes +
  service.buffer_minutes`) is identical for every specialist in the union —
  `Service.duration_minutes`/`buffer_minutes` are single fields on
  `catalog.Service`, and `SpecialistService` (the through-model) carries no
  per-specialist override of either — so "free" is unambiguous: the same
  occupied span is checked against every qualifying specialist's calendar.
  If per-specialist duration overrides are ever introduced, the union rule
  (§ Stage 6 decisions, "Multi-specialist availability") needs revisiting —
  "at least one specialist free" would then mean free for *that
  specialist's own* duration, not a shared one, which changes what a bare
  time in the response actually promises.
- **`compute_multi_specialist_availability` query count is ~`1 + 3*N`**
  (§ Stage 6.H decisions) — one query for `_fetch_qualifying_specialists`,
  plus `compute_candidate_start_times`'s own three queries per qualifying
  specialist. Confirmed exact, not just an upper bound: every specialist
  reaching the loop is already `is_active=True`, so `compute_open_windows`'s
  own `is_active` early return, which would otherwise skip those three
  queries, never fires. Accepted N+1 for now; revisit if salons routinely
  have many specialists per service. Tests assert this as a formula of N,
  not a fixed number.
- **Specialist experience/seniority as a structured field.** Today experience is
  not modelled — a specialist can mention years of experience in the free-text
  `bio` field, but there is no structured `experience_years` (or similar) on the
  `Specialist` model. The Stage 6.J specialist-picker mockup showed both options;
  the free-text approach was chosen for Stage 6 because seniority is a specialist-
  profile concern, not an availability-engine one, and adding a field mid-Stage-6
  would pull in its own sub-decisions. A structured field is wanted later (frontend
  specialist-profile / admin stage, ~Stage 13). Key sub-decision when it lands:
  store a raw number ("8 years") vs. a career-start date/year. A raw number freezes
  and goes stale (it will still read "8" two years on); a start date recomputes
  itself and never lies — lean toward the date. Decide before the specialist-profile
  frontend stage.
- **"Resume payment"** — if the client loses the frontend state (closed the
  tab, returned minutes later while the hold is still alive), the one-shot
  `provider_data` is gone from both frontend and DB. Re-issuing it would need
  a new provider method to fetch instructions for an existing intent.
  Deferred until the frontend exists (Stage 14+), since the shape depends on
  how the frontend holds state. Distinct from the double-click case (§ Stage
  8.C decisions).

## Overall style

- **Modular monolith**, not microservices. A single Django project with
  domain-bounded apps (`catalog`, `scheduling`, `booking`, `payments`, etc.).
  No technical justification yet exists for splitting into services; revisit
  only if a concrete scaling or team-boundary problem appears.

## Multi-tenancy

- **Shared database, shared schema, row-level isolation.** Every
  tenant-owned model carries a `salon` FK. Tenant scoping is enforced
  through a shared base manager/queryset, not left to each view to
  remember. Chosen over schema-per-tenant (e.g. `django-tenants`) because
  the migration/ops overhead of schema-per-tenant isn't justified by one
  demo tenant, and shared-schema is easier to write isolation tests against.
- **Tenant resolution: path prefix**, `/api/salons/<slug>/...`. Chosen over
  subdomain-based resolution because it works locally without DNS/hosts-file
  changes. The resolution logic must live in a single, isolated middleware
  component so it can be swapped for subdomain-based resolution later
  without touching every view. **Not yet implemented** — this is a Stage 3
  (auth, roles, tenant isolation, guest identity) commitment, recorded now
  so it isn't improvised differently when that stage starts.

## Identity: Customer vs. User

- Guest booking is allowed. There is **one `Customer` table for the whole
  platform**, scoped to a salon (`salon` FK), with `name`, `email`, `phone`,
  and a **nullable** FK to `User`.
  - Guest = `Customer` with `user=NULL`.
  - Registered = `Customer` with `user` set.
- **`Appointment` always references `Customer`, never `User` directly.**
  This keeps booking logic identical for guests and registered customers —
  there is exactly one path through the booking code, not two.
- **Email is unique per salon on `Customer`.** A returning guest using the
  same email within the same salon is treated as the same `Customer`
  (updates their existing row rather than creating a duplicate).
- **One `User` may be linked to multiple `Customer` rows** — one per salon,
  since a platform-wide user account can be a customer at more than one
  tenant.
- **Linking a guest `Customer` to a new `User` account happens only after
  email verification.** Never link by phone number — phone numbers are not
  a reliable identity signal (reassigned, unverified, shared).
- **Guests manage their appointment through a single-use signed token link**
  sent by email (view/cancel), not through an account login.
- **Guests cannot leave reviews.** Review submission requires an
  authenticated `User`-linked `Customer`.

## Timezone

- **Single timezone per salon.** Store all timestamps in UTC
  (`USE_TZ = True`, Django's global `TIME_ZONE = "UTC"`). Local-time
  rendering for a given salon is an application-layer concern driven by a
  per-`Salon` timezone field (added when the `tenants` app is built), not a
  global Django setting — this avoids baking in an assumption that gets
  reopened the moment a second salon in a different timezone is onboarded.

## Currency

- **Assumption: UAH**, stated by the product owner. Flagged for
  confirmation because it has downstream effects once the `payments` app
  exists (minor-unit rounding for the 20% deposit, display formatting,
  Stripe currency configuration). Has **no effect on Stage 0** — no models
  or money fields exist yet. Revisit this note when the `payments` app is
  designed, since real Stripe test mode requires knowing the currency
  up front (it affects rounding behavior for the 20% deposit calculation).

## Payments

- **Mock `PaymentProvider` implementation first.** A provider-agnostic
  interface is defined; a Stripe adapter is added later behind the same
  interface. **Tests must never require network access** — the mock
  provider is what test suites exercise, not a sandboxed Stripe.

## Notifications

- **Channel abstraction from day one**, but only an email adapter is
  implemented in the notifications stage. Telegram is added later as a
  second adapter in its own stage, behind the same interface.

## Frontend cadence

- **Backend-first.** No frontend work starts until the booking and payments
  domains are complete on the backend. Avoids building UI against APIs that
  are still shifting shape.

## Dependency versions (Stage 0)

Pins in `backend/requirements/*.txt` are verified against PyPI directly
(`pip index versions`), not assumed from training knowledge, and checked for
mutual compatibility via each package's declared `requires_dist`, not just
"does it install." Two packages are deliberately *not* pinned to their
newest release because the newest release is incompatible with something
else in the stack:

- **`redis` is capped at `6.4.0`**, not the newest `8.1.0`. `kombu`
  (Celery's transport library) declares `redis!=4.5.5,!=5.0.2,<6.5,>=4.5.2`
  for its `redis` extra. `redis-py` 7.x/8.x are not yet supported by Celery's
  transport layer — pinning the newest client would leave the Redis
  broker/result-backend running against an untested, unsupported version.
  `base.txt` uses `celery[redis]` (not a bare `celery` line) specifically so
  pip enforces this constraint automatically on any future version bump,
  instead of relying on a comment staying in sync.
- **`mypy` is capped at `1.19.1`**, not the newest `2.3.0`. `django-stubs`'s
  `compatible-mypy` extra declares `mypy<1.20,>=1.13`. Same reasoning:
  `development.txt` uses `django-stubs[compatible-mypy]` so pip enforces
  this rather than a comment.
- **`django-stubs` is pinned to the `5.2.x` line (`5.2.9`)**, not the newest
  `6.0.9`. `6.0.9` targets Django 6.0 with only partial support for 5.2;
  `5.2.9` is the line built specifically for our pinned Django 5.2.

**Django is pinned to `5.2.17`** — the latest patch of the current LTS line
(5.2), not `6.1` (latest overall release, but not LTS) — per the standing
preference for LTS unless there's a compatibility reason not to.

When bumping any pin later, re-check `requires_dist` for the packages above
before taking "latest," not just whether `pip install` succeeds — a clean
install doesn't guarantee the runtime integration (e.g. Celery↔Redis) was
actually tested against that combination.

## Stage 0 scope note

Stage 0 is infrastructure and project skeleton only: no domain apps, no
models, no API endpoints, no business logic. Settings, Docker, Celery
wiring, and tooling exist so every later stage has a consistent foundation
to build on — nothing in Stage 0 should need to be revisited for
architectural reasons, only extended.

## Business rules

Recorded here because these are product decisions that must survive a fresh
session, not architectural shape — `docs/ARCHITECTURE.md` describes the mechanisms
that implement them; this section is the source of truth for the actual numbers, so
they are stated once, not duplicated.

- **Deposit: 20% of service price, paid online at booking.** A single salon-wide
  setting (`Salon.deposit_percentage`, default 20%), not overridable per service in
  v1 — add a per-service override only if a real salon actually asks for one.
- **The remaining 80% is paid in person at the salon.** Not tracked or collected by
  the platform in any form.
- **Cancellation refund depends on WHO cancelled, not only WHEN.** For a
  customer-initiated cancellation: ≥ 24 hours before appointment start, deposit
  refunded; < 24 hours before start, deposit forfeited (a single cutoff, no
  tiered/partial refund). For a **salon-initiated cancellation** (specialist
  illness, `TimeOff` added over an already-booked slot, a working-hours change that
  displaces a booking, etc.), the deposit is **always fully refunded regardless of
  timing** — the customer is never penalized for a change the salon made.
  `Appointment.cancelled_by` is what refund eligibility is computed from; it is not
  a bare time comparison.
- **Staff changes to `TimeOff`/`WorkingHours` that conflict with an existing
  appointment must be detected and explicitly resolved — never silently orphaned.**
  Affected customers are offered: rebook with another specialist who performs the
  same service, rebook with the same specialist at a later time, or cancel with a
  full refund (the salon-initiated-cancellation rule above). The resolution UI/flow
  itself is Stage 19 (admin calendar) work, not Stage 2 — this only records that the
  Stage 2 data model must be able to represent the conflict and that the
  Stage 7/8 cancellation path must support a salon-initiated, always-refunded
  cancellation reason.
- **Reviews require a `COMPLETED` appointment; exactly one review per appointment.**
  Guests cannot review (see § Identity above). Reviews are immutable once posted and
  the salon cannot post a public reply in v1; staff can hide a review, but deletion
  is not exposed to anyone.
- **Appointment completion is an automatic scheduled transition**
  (`CONFIRMED → COMPLETED` once `end_datetime` passes), with a staff override
  available in the admin for correcting mistakes. Automatic because review
  eligibility depends on `COMPLETED`, and that can't be left waiting on a staff
  member remembering to mark every appointment.
- **`NO_SHOW` is a staff-marked appointment status with no automatic effects in
  v1** — no customer penalty, and the deposit is already forfeited by that point
  regardless. It exists purely for admin record-keeping and statistics; revisit
  once there's a real pattern worth reacting to.
- **Booking window: minimum 3 hours' lead time, maximum 60 days in advance.**
  Salon-configurable; these are the defaults.
- **A `PENDING_PAYMENT` appointment holds its slot for 15 minutes before the
  expiry sweep releases it.** Fixed for v1, not salon-configurable — this is a
  payment-UX parameter, not a business lever a salon would tune. Reasoning: long
  enough to complete a card payment including a 3-D-Secure challenge (which
  normally resolves in well under 10 minutes), short enough that an abandoned or
  malicious checkout only blocks a slot briefly rather than making it trivially
  blockable for an extended window.
- **Service buffer time blocks the calendar but is never itself offered as a
  bookable start.** E.g. a 90-minute service with a 15-minute buffer occupies 105
  minutes of the specialist's schedule; a following appointment may start exactly
  when the buffer ends. Buffer is set per service (equipment/room turnaround
  differs by service), not a salon-wide value. See `docs/ARCHITECTURE.md` § 6–7 for
  the mechanism, including how it's enforced in the double-booking exclusion
  constraint.
- **No specialist logins in this build.** Specialists are managed entirely by
  salon staff/admin; add specialist accounts only if a later stage needs them.
- **One `SalonStaff` role for v1.** The `role` field is kept on the model so a
  second role can be added later without a shape migration — only the field's
  value space grows.
- **The AI assistant only ever proposes a service and slot; it never creates a
  binding appointment directly.** The user must confirm through the normal booking
  flow, which is what actually calls the booking service layer. Chosen over letting
  the assistant book directly because a hallucinated parameter should never be able
  to produce a real, paid appointment.
- **AI assistant memory is session-only, for every customer including logged-in
  ones — nothing is persisted to a database.** Held in Redis with a TTL instead
  (see `docs/ARCHITECTURE.md` § 10). Salon chat routinely surfaces health-adjacent
  personal information (skin conditions, allergies, treatment contraindications);
  the deliberate choice is to not retain that by default for anyone, rather than
  carve out an exception for logged-in customers.
- **`Salon.timezone` defaults to `Europe/Kyiv`.** The demo tenant is a Ukrainian
  salon; the field is per-salon and overridable at creation (see § Timezone
  above) — this is only the default for a newly created `Salon` row.
- **`Salon.slot_granularity_minutes` defaults to 15.** The step size the
  availability engine (Stage 6) walks a specialist's open windows in when
  generating candidate start times. Salon-configurable, like the lead-time and
  advance-window defaults above.

## Formatting/linting tooling

- **Consolidated on `ruff format`, dropped `black`.** Stage 0 originally pinned both;
  no reason for running two formatters was ever recorded, and by Stage 2 `ruff format`
  had matured into a deliberately black-compatible formatter (~99.9% identical output),
  making the second tool redundant. Removed `black` from
  `backend/requirements/development.txt`; `[tool.black]` dropped from `pyproject.toml`
  (`ruff format` reads the existing `line-length`/`target-version` from `[tool.ruff]`).
  Checked at a point where the codebase had almost no formatted history yet, so the risk
  from any one-time reformat diff was effectively zero — revisit only if a future need for
  byte-for-byte black compatibility (e.g. an external tool that assumes black output)
  comes up.

## Stage 3 decisions (auth, roles, tenant isolation, guest identity)

- **Tenant-resolution middleware placement: last in `MIDDLEWARE`.** Not because it
  depends on `request.user` (it doesn't — salon resolution is a pure slug lookup, and
  JWT auth runs inside DRF view dispatch, not in `AuthenticationMiddleware`, which only
  populates session-based auth for Django admin). Placed last so the `tenant_context`
  binding window is as narrow as possible — it wraps only the view, not any other
  middleware's request/response processing. Revisit only if a future middleware
  genuinely needs tenant context bound around it.
- **Unknown or inactive salon slug → `404`, not `403`.** The slug is part of the URL
  path, not a credential — a nonexistent salon is a routing miss. `404` also avoids
  confirming/denying slug existence differently to authorized vs. unauthorized callers.
  Both cases resolve through the same query (`Salon.objects.filter(slug=slug,
  is_active=True)`) so there is no code path that could accidentally distinguish them
  in the response.
- **Guest token: signed value, only its hash stored.** `GuestAccessToken` stores
  `token_hash` (SHA-256 of the signed token), never the raw token — a database dump
  must not hand out working links. Validation re-hashes the presented token and looks
  up by hash.
- **Guest token: `cancelled_via_token_at`, not a blanket `consumed_at`.** Viewing and
  cancelling are separate capabilities of the same token; a single "consumed" flag
  would kill view access the moment the guest cancels, which is wrong — they should
  still be able to see the cancelled appointment afterward.
- **Guest token expiry: 30 days after `Appointment.end_datetime`**, not 90. The link
  sits in an inbox indefinitely; a shorter window bounds the blast radius if that
  inbox is compromised or the mail is forwarded, while still covering the realistic
  window for viewing/cancelling a recent booking.
- **Guest token transport: URL fragment in the emailed link, header on the API call.**
  The email links to a frontend route with the token in the URL **fragment**
  (`#token=...`), never a query string. A fragment is never transmitted in any HTTP
  request — not to the frontend server, not in `Referer` to third-party resources the
  page loads — so it reaches no access log anywhere, frontend or backend. Frontend JS
  reads `window.location.hash` client-side and sends the token to the API as an
  `X-Guest-Token` header on view/cancel requests. This is what keeps the token out of
  Django's (and any WSGI server's) request logs, which record method + path + query
  string, not arbitrary headers. Browser history still retains the full URL regardless
  of query-vs-fragment — that residual risk is bounded by the hash-only storage and
  30-day expiry above, not eliminated by transport choice.
- **Throttle rates** (DRF `ScopedRateThrottle`, keyed by IP — all four are pre-auth or
  guest endpoints with no user id to key on): `login` 5/min (blunts credential
  stuffing from one source, allows normal typo-retry); `password_reset` 3/hour and
  `resend_verification` 3/hour (both trigger an email send to a third party, so
  tighter than login — abuse here means spamming someone else's inbox, not just
  guessing a password); `guest_token` 20/min (the token is a signed value, not
  brute-forceable in a human timeframe, so this guards against endpoint hammering
  rather than credential guessing).
- **Registration and password-reset-request never reveal whether an email exists.**
  Both return an identical response (status, body) regardless of whether the email is
  already registered, and enqueue a Celery task either way so response timing doesn't
  diverge on that branch. On a duplicate registration email, a "you already have an
  account" notice is sent instead of creating a second row — the caller-visible
  response is the same either way.
- **DRF throttling requires a shared cache, not the default `LocMemCache`.** DRF's
  throttle classes count requests through the Django cache; `LocMemCache` (the default
  when `CACHES` is unset — true of this project until Stage 3) is per-process, so
  behind N gunicorn/uvicorn workers the effective limit becomes `rate × N`, silently.
  Fixed with Django's built-in `django.core.cache.backends.redis.RedisCache` (no new
  dependency — uses the already-pinned `redis` package), pointed at Redis logical **DB
  2** via `REDIS_CACHE_URL`, kept separate from the Celery broker (DB 0) and result
  backend (DB 1) so throttle keys can never collide with Celery's own.
- **Email verification does not route through the `Notification`/channel abstraction
  described in `docs/ARCHITECTURE.md` § 9, for Stage 3.** `Notification` extends
  `TenantScopedModel`, whose `salon` FK is non-nullable — but registration/email
  verification is a platform-wide `User` event with no salon in scope
  (`/api/v1/auth/...` is explicitly outside the salon prefix, § 13). Reconciling that
  (nullable `salon` on `Notification`, or a different representation for
  salon-less triggers) is Stage 9 (Celery + notifications) work, not Stage 3 — recorded
  here as a known gap rather than silently worked around. Stage 3 sends these emails
  via a small standalone Celery task in `accounts/tasks.py` (plain
  `django.core.mail.send_mail`), with none of the dedup/idempotency machinery
  `Notification` provides — acceptable for now because both underlying operations
  (marking an email verified, requesting a password reset) are themselves idempotent,
  so a duplicate send is a minor annoyance, not a correctness bug.
- **Email verification token: stateless, not single-use, payload includes the target
  email.** `TimestampSigner`-signed `{"user_id", "email"}`, 48h expiry
  (`EMAIL_VERIFICATION_TIMEOUT`). Verifying is idempotent (setting
  `email_verified_at` twice is a no-op), unlike the guest token's *cancel* action,
  which has a real one-time consequence — that asymmetry is why the guest token needs
  a stored single-use record and this one doesn't. Including the target email in the
  signed payload, checked against the user's *current* `email` at verify time, means a
  future "change email" feature gets automatic invalidation of old tokens for free —
  no separate revocation step needed, since a changed email simply stops matching.
- **Password reset uses Django's built-in `PasswordResetTokenGenerator`**, not a
  hand-rolled signer — it's already self-invalidating on password change (the hash it
  produces incorporates the current password hash), which is exactly the single-use
  property this token needs and the verification token doesn't.
  `PASSWORD_RESET_TIMEOUT` is set to 1 hour, tighter than email verification's 48h,
  since a live reset token is the highest-value credential in this scheme and is
  normally acted on within minutes of being requested.
- **`User.username` is dropped; `email` (unique) is `USERNAME_FIELD`, with a custom
  `UserManager`.** Record of how this actually happened, not just what changed:
  - **Not required by JWT.** `djangorestframework-simplejwt` is fully agnostic to
    `USERNAME_FIELD` — `TokenObtainPairView` authenticates against whatever field
    `USERNAME_FIELD` names, `username` included, with zero changes needed on its side.
    Nothing about adding JWT auth forced this.
  - **A less invasive alternative existed and wasn't raised at the time:** keep
    `username` as `AbstractUser` shipped it, add a non-unique `email`, and leave login
    on `username` for Stage 3, deferring "email is the login identity" until it was
    actually load-bearing.
  - **What actually happened:** while implementing Stage 3 sub-step 2, this was
    inferred from `docs/ARCHITECTURE.md` § 3's product-level phrase "standard email +
    password" and written directly into `accounts/models.py` — `username = None`,
    `email` made unique, `USERNAME_FIELD` changed, a new `UserManager` added — without
    surfacing it as a decision first. It was only written up here, in this file,
    *after* the diff already existed, which inverted this file's purpose: it is meant
    to record what was agreed, not to notarize a change after the fact.
  - **Resolution:** the user reviewed the diff, agreed with the outcome (email as
    `USERNAME_FIELD` is right for this product) but not the process, and approved it
    after the fact. The rule this produced — model/manager/migration changes of this
    kind must be raised and approved *before* implementation — is now in `CLAUDE.md`.

## Stage 3 sub-step 3 decisions (roles, permissions, guest identity)

- **`GuestAccessToken` lives in `booking`, not `accounts`, and is a full
  `TenantScopedModel`.** It FKs `Appointment`, and `booking` already depends on
  `accounts` (via `Customer`), never the reverse — putting it in `accounts` would have
  introduced a new backward dependency for one model. As a `TenantScopedModel` it gets
  the standard `salon` FK, the composite `(id, salon)` unique constraint, and a
  `composite_tenant_fk` on its `appointment` FK, same as every other tenant-owned
  model. This works cleanly with the tenant-resolution middleware because the guest
  endpoints are single-object routes nested under `/api/v1/salons/<slug>/...` — tenant
  context is already bound by the time the permission class or view touches
  `GuestAccessToken.objects`. It also means a token issued for one salon, presented
  against a different salon's URL, is simply invisible to the hash lookup (filtered to
  the current tenant first) — cross-salon token misuse collapses into the same
  "not found" path as any other invalid token, with no separate check needed.
- **Every guest-token failure path raises the same `InvalidOrExpiredTokenError`.** Bad
  signature, unknown hash, expired, and — for the cancel action only — a cancel
  capability already spent, all produce the identical 400 response (same code,
  message, status). This runs inside `HasValidGuestToken.has_permission` (not
  `has_object_permission` — see the next decision for why), so DRF's own exception
  propagation carries it to the shared envelope; there's no hand-written branch that
  could leak which case fired. A URL/token appointment mismatch produces the same
  error too, but from a different place — see below.
- **`HasValidGuestToken` does its real validation in `has_permission`, not
  `has_object_permission`, and every view using it must be a DRF generic, not a plain
  `APIView`.** DRF only calls `check_object_permissions()` (and therefore
  `has_object_permission`) when a view explicitly triggers it — its generic views do
  this automatically inside `get_object()`; a plain `APIView` has to call it itself.
  The original version of this permission put all its logic in `has_object_permission`
  and returned `True` unconditionally from `has_permission`, which meant a future view
  that forgot to call `check_object_permissions` (e.g. a list endpoint) would silently
  let every request through. Closed with four layers, not one:
  1. **Fail-closed marker**: `has_permission` requires `guest_token_action` to be
     exactly `"view"` or `"cancel"`, with no default — a view that forgets to declare
     it is denied, not silently treated as `"view"`.
  2. **DRF generics**: both guest views (`booking/views.py`) are now
     `generics.RetrieveAPIView`/`generics.GenericAPIView` subclasses, whose
     `get_object()` calls `check_object_permissions()` automatically — removing "the
     view forgot to call it" as a failure mode for these two endpoints.
  3. **Token-driven object resolution**: `_GuestTokenAppointmentMixin.get_object()`
     resolves the target `Appointment` from `request.guest_access_token.appointment_id`
     (set by `has_permission`), never from the URL's `appointment_id` — so even if
     `check_object_permissions` were somehow skipped, the wrong appointment is never
     fetched in the first place, because the lookup itself is keyed off the token. The
     URL id is still compared and must match, purely so a stale or wrong link fails
     loudly instead of silently ignoring what's in the address bar.
  4. **Test-level backstop**: `tests/test_guest_token_permission_safety.py` walks every
     URL pattern using `HasValidGuestToken` and asserts the view is a DRF generic —
     catches a future plain-`APIView` regression in CI rather than in production.
  None of this is airtight on its own — DRF's object-permission mechanism is
  fundamentally opt-in per view, and no permission class can force
  `check_object_permissions()` to run — so the combination is the mitigation, not any
  single layer. The general rule (not just this one permission class) is now in
  `CLAUDE.md`.
- **The guest -> registered-`User` merge (on email verification) is a per-salon loop
  (`for salon_id in Salon.objects.values_list(...): with tenant_context(salon_id):
  Customer.objects.filter(...).update(user=user)`), not `Customer.unscoped_objects`.**
  `Salon` itself isn't tenant-scoped, so enumerating every salon needs no special
  access, and looping keeps this — the one deliberately cross-tenant operation in
  Stage 3 — narrow and explicit rather than reaching for the broader
  `unscoped_objects` bypass reserved for cases where the salon set isn't already
  known.
- **A guest cancel only handles the two transitions the state machine already
  documents** (`PENDING_PAYMENT`/`CONFIRMED` → `CANCELLED`); any other starting status
  is rejected with a new `InvalidStateTransitionError` (409) — an ordinary,
  distinguishable domain error, not a guest-token security concern, so it doesn't hide
  behind the generic envelope above. Refund eligibility and the full concurrency-safe
  cancellation service layer remain Stage 8 and Stage 7 work respectively; this only
  flips the appointment's own status, with a comment marking where Stage 8's refund
  hook goes.

## Stage 3 sub-step 4 decisions (Django admin, Stage 3 cleanup)

- **`core.admin.SalonScopedAdmin` is a deliberate, blanket cross-tenant tool for
  platform operators, not a per-salon back office.** Every salon's data is visible
  together in `/admin/` by design — that's Django `is_staff`/`is_superuser` territory
  (`docs/ARCHITECTURE.md` § 4's "Platform superuser" role), not the product back
  office, which is Stages 18–21, built on the JWT + `IsSalonStaff` API surface
  instead. It overrides `get_queryset` (list views) to read through
  `unscoped_objects`, since admin requests never bind tenant context the way
  `/api/v1/salons/<slug>/...` requests do. It also overrides
  `formfield_for_foreignkey`, which turned out to need more than just supplying a
  `queryset` kwarg: Django's `ForeignKey.formfield()` unconditionally evaluates the
  related model's `_default_manager` while building its own `defaults` dict, *before*
  applying any `queryset` override passed in — so opening the add/change form for a
  model with a FK to another `TenantScopedModel` (e.g. `Service` → `ServiceCategory`)
  still 500s unless *something* is bound. The fix binds a throwaway sentinel tenant
  (`tenant_context(-1)`) around that one call — the eagerly-computed queryset it
  produces is immediately discarded in favor of the real `unscoped_objects` one, so
  which id is bound doesn't matter, only that one is. Found by a test
  (`test_service_add_form_renders_without_a_bound_tenant`) that actually opened the
  form rather than only checking the changelist.
- **`GuestAccessToken` is registered, read-only.** `token_hash` is a SHA-256 hash, not
  the raw token — the entire reason it's hashed (this file, Stage 3 sub-step 3
  decisions) is that knowing the hash doesn't grant access, so showing it to an
  authorized platform operator isn't equivalent to leaking a working link, and it has
  real support value (did this guest's link get used? when does it expire?).
  Read-only because rows are only ever meant to come from `issue_guest_token` — an
  admin-typed `token_hash` wouldn't correspond to any real signed token.
  `token_hash` is left out of `list_display`, and — found only by writing a test that
  actually loaded the change page rather than reasoning about it — was still rendered
  on the change/detail form regardless, since with no `fields`/`exclude` set Django's
  default `ModelAdmin` includes every model field on that page no matter what
  `list_display` says, and `has_change_permission=False` only disables editing, not
  visibility. Now excluded explicitly (`exclude = ("token_hash",)`), with
  `tests/test_admin_tenant_scoping.py::test_guest_access_token_change_view_does_not_render_token_hash`
  guarding the regression.
- **Read-only models: `Appointment`, `Payment`, `Notification`, `ProcessedWebhookEvent`,
  `GuestAccessToken`, `Review`.** `Appointment` and `Payment` each carry a `status`
  field driven by a service layer that doesn't exist yet (booking core is Stage 7,
  payments Stage 8) — made the *whole* model read-only rather than only `status`,
  since the other fields (`cancelled_by`, the price/deposit snapshots,
  `hold_expires_at`) are just as capable of corrupting invariants that layer will
  assume hold, and there's no legitimate hand-edit case before it exists.
  `Notification.status` drives Stage 9's dedup/idempotency machinery the same way.
  `ProcessedWebhookEvent` is a pure idempotency ledger with no legitimate hand-edit
  case, kept read-only for debugging visibility only — not a `TenantScopedModel` (no
  `salon` field, arrives before salon is known), so it uses `ReadOnlyAdminMixin`
  directly rather than `ReadOnlySalonScopedAdmin`. `Review` is the one case where the
  reasoning is stronger than "not built yet": reviews are explicitly **immutable once
  posted** (this file, § Business rules), so admin edit would directly violate that
  rule, not just get ahead of an unbuilt stage. `hidden_at` is the one sanctioned
  staff mutation, but its real UI is Stage 21 — deferred rather than building
  partial-field editability now. Editable: `SalonStaff`, `Customer` (support/operator
  tooling this surface exists for), `ServiceCategory`, `Service`, `Specialist`,
  `SpecialistService`, `WorkingHours`, `TimeOff`, `Salon` — reference/config data with
  no not-yet-built state machine underneath any of it.
- **Cross-tenant FK mismatch via admin (e.g. a `Service` saved with `salon`=A but
  `category` from salon B) is already closed at the database layer — no new
  application-level guard added.** `salon` stays a visible, explicitly-chosen field on
  every add/change form; there's no ambient tenant context to silently get wrong. Every
  `TenantScopedModel` with a second tenant-scoped FK already has a DB-level composite
  FK (`core/db.py`'s `composite_tenant_fk`, 14 call sites across migrations, enforcing
  `(child.fk_id, child.salon_id) -> (parent.id, parent.salon_id)`, tested since Stage 2
  commit `fb3605e`) — a mismatched submission fails as an `IntegrityError` at the
  database, not a silent cross-tenant link. That's an ugly generic-500 failure mode for
  an internal tool, not a correctness gap. Deliberately not adding admin-form
  `clean()`-level cross-validation to turn it into a friendlier message — that's UX
  polish beyond this sub-step, revisit only if a real operator workflow needs it.

## Stage 4 decisions (catalog API)

**Summary.** Stage 4 adds the API layer over the existing (Stage 2) `ServiceCategory`/
`Service` models — no new domain apps, only `catalog/serializers.py`, `catalog/views.py`,
`catalog/urls.py`, and the model additions below. It exposes standard CRUD, id-keyed
(no slugs — see below), under `/api/v1/salons/<slug>/{categories,services}/`, list and
detail, all paginated (`core.pagination.DefaultPagination`, page size 20 / cap 100).

**Read/write split:** reads (`GET`) are public — `AllowAny`, no authentication — matching
the product requirement that any visitor can browse a salon's catalog before booking.
Writes (`POST`/`PUT`/`PATCH`/`DELETE`) require `IsSalonStaff` for the URL's resolved
tenant, computed once per request via `get_permissions()`, reusing the Stage 3 permission
class rather than new ad-hoc logic. Deactivated (`is_active=False`) rows are filtered out
of every read by default; the one privileged exception is `include_inactive=true`, which
only surfaces them to staff of *that exact* salon (§ "Catalog read semantics" below) —
and, as of the write-method fix also recorded below, is never required on a write, only
on reads.

**Soft-delete policy:** `DELETE` never removes a row — `catalog/services.py`'s
`soft_delete_category`/`soft_delete_service` just flip `is_active` to `False` (204 on
success). Categories refuse to deactivate while they still have active services
(`CategoryHasActiveServicesError`, 409) — mirroring the real FK's `on_delete=PROTECT`
in the soft-delete world, an explicit staff resolution rather than a silent cascade or
orphan. Deactivating a `Service` with existing appointments is unconditionally allowed:
`Appointment` already snapshots price/deposit at booking time, so a deactivation can't
retroactively change what's owed on a booking already made — it only stops *new* ones
(enforced later, in Stage 7's booking-creation service layer, not here).

**Two-layer uniqueness defence** for each model's `(salon, name)` constraint (added in
this stage): an explicit `validate_name` on both serializers is the primary, friendly
check (tenant-scoped manager, no access to `salon` needed, since `salon` is always
read-only). It's check-then-write, not race-proof, so `core.exceptions.exception_handler`
backstops it — any `psycopg.errors.UniqueViolation` that reaches the database anyway
(a genuine concurrent race, or any future model that forgets the serializer-level check)
is translated into the same structured 400 shape, project-wide, rather than surfacing as
an uncaught 500. Full mechanism and the DRF gotcha that made the explicit check necessary
in the first place are detailed below.

- **Catalog `slug` fields are dropped from Stage 4.** Considered and withdrawn: the
  Stage 4 API addresses `ServiceCategory`/`Service` by numeric `<id>` throughout, so a
  `slug` field would have no consumer in this stage. It's a future frontend concern
  only — pretty/SEO per-service URLs — and it isn't yet decided whether the public
  frontend will even have per-service pages rather than a single `/services` listing.
  Adding it now would drag in transliteration, a new dependency, empty-result
  fallbacks, and collision-suffix handling for a field nothing reads. Both tables are
  empty and will stay empty until real salons exist, so adding it later (when the
  frontend's URL shape is actually decided) is no more expensive than adding it now.
  Revisit when the public frontend's routing is designed (the Frontend skeleton
  stage, § Agreed stage order).
- **Catalog read semantics: reading is public, with no cross-salon surface.** Any
  visitor, on any salon's site, sees that salon's active services without
  authentication. Salons are independent — there is no cross-salon browsing surface,
  no salon directory, and salon owners have no visibility into each other. The one
  authenticated read privilege is `include_inactive=true`, which lets a salon's own
  staff see their deactivated services. This privilege is scoped to the staff member's
  own salon: applying it against another salon's endpoint returns the ordinary public
  (active-only) result, silently — not an error, not elevated access. Tested
  explicitly, because the common failure mode here is a permission check that verifies
  "is staff" without verifying "is staff of *this* salon."
- **`include_inactive=true` only gates read visibility (list/retrieve GET); it is not
  required, and has no effect, on writes.** Found while confirming the
  deactivate→reactivate round trip: `RetrieveUpdateDestroyAPIView` uses the same
  `get_queryset()` for every method, so the naive version of the is_active filter
  also hid a deactivated row from `PATCH`/`DELETE`, making a plain `PATCH
  /services/<id>/ {"is_active": true}` 404 unless the client *also* passed
  `?include_inactive=true` — a non-obvious, undiscoverable requirement on a write.
  Fixed by skipping the visibility filter entirely for non-`SAFE_METHODS`: by the time
  `get_queryset()` runs for a write, `get_permissions()` has already restricted it to
  `IsSalonStaff` for this exact salon (DRF checks permissions in `dispatch()`, ahead of
  any handler method), so there's no protective reason left to also hide a deactivated
  row from a same-salon staff member editing or reactivating it. Reads are unaffected —
  a public caller, or staff without the flag, still can't `GET` an inactive row, list or
  single-object.
- **DRF 3.18 silently skips a `UniqueTogetherValidator` built from `Meta.constraints`
  when one of the constrained fields is read-only with no default.** Determined by
  reading `rest_framework/serializers.py`'s `get_unique_together_validators()`
  directly and confirming empirically
  (`ServiceCategorySerializer().get_validators()` returned `[]`): DRF 3.18 *does*
  build a unique-together validator from a `UniqueConstraint` in `Meta.constraints`
  (not only the legacy `Meta.unique_together`), but the method only considers fields
  present in `self._writable_fields`, plus read-only fields carrying a `default`. Every
  `TenantScopedModel`'s `salon` field is read-only (it comes from the URL's tenant
  context, never the client) and carries no Django-level default, so its `issuperset`
  guard silently drops any `(salon, X)` constraint's validator, with no error raised —
  `Service`/`ServiceCategory`'s `(salon, name)` constraints (sub-step 1) hit this
  immediately. A POSTed duplicate name passed `is_valid()` cleanly and only failed at
  `.save()`, as an uncaught `IntegrityError` — a 500, not a 400. Fixed two ways: an
  explicit `validate_name` on both serializers (checks the already-tenant-scoped
  manager directly — needs no access to `salon` at all) for the ordinary case; and a
  `core.exceptions.exception_handler` branch translating any
  `psycopg.errors.UniqueViolation` into the same structured 400, as a backstop for the
  concurrent-POST race the serializer check can't close on its own — the database
  constraint is the actual guarantee, the serializer check is only for a good error
  message. That handler branch is intentionally generic (no per-field detail parsed
  from the constraint name) to keep `core` domain-agnostic, and applies project-wide —
  it can only ever turn an already-broken uncaught-`IntegrityError`-as-500 into a clean
  400, never change a currently-passing request (verified: the three existing
  `IntegrityError` tests all assert at the ORM layer directly, never through DRF's
  request cycle, so none of them exercise this code path). This will recur on every
  future `TenantScopedModel` with a `(salon, X)` constraint — see `CLAUDE.md`'s
  Architectural rules for the standing instruction.

## Content localization (deferred to Stage 11.5)

Not part of Stage 4, or any stage before it — recorded now because the scope and
design principle were settled while doing Stage 4's catalog work, and deferring them
undocumented would risk the catalog model being bolted onto later instead.

- **Deferred to its own dedicated stage** (Stage 11.5, see § Agreed stage order —
  placed after the backend model/API stages and before the frontend stages, since the
  frontend needs the real translatable-content shape to build against, not a
  catalog-only stopgap. Numbered 11.5 rather than inserted into the main sequence,
  specifically so it doesn't renumber every stage after it and invalidate existing
  `Stage N` references elsewhere).
- **Scope is platform-wide, not catalog-only.** Translated content covers service
  names/descriptions, category names, salon description, and notification templates.
  It must be designed as one mechanism across all of those models, not bolted onto
  `catalog` alone and then reinvented for the others.
- **Design principle already agreed: languages are a per-salon setting, not a
  platform constant.** A salon declares which languages it operates in. A
  single-language salon sees single-value fields and is never forced to invent a
  translation it doesn't need; a multi-language salon must supply every language it
  has declared. This removes the need for a fallback rule — there's no "missing
  translation" state to fall back from, because a declared language is a required
  field, not an optional extra.
- **Do not implement any part of this before its own stage (Stage 11.5)** — not the
  model shape, not a placeholder field, not a migration.

## Stage 5 decisions (specialists API)

Decided 2026-08-11, before implementation — recorded here as agreed-in-advance
decisions, not as notes written after the fact.

- **Reviews removed from Stage 5; read endpoints move to Stage 11, alongside
  submission.** The stage order originally bundled "Specialists + reviews —
  read-only" into one stage. Split apart because a read-only review endpoint
  built now would return an empty list — no completed appointment can exist
  yet, since booking (Stage 7) hasn't landed — and would prove nothing in a
  test. Stage 11 (review submission, completed-appointment gated) would need
  to touch the same serializer anyway once real data exists, so building it
  twice (once now, against no data, and again in Stage 11) is pure rework.
  `docs/ARCHITECTURE.md`'s app table already places `Review` in its own
  `reviews` app, unaffected by this — only the *stage* that ships its read API
  moved, not its ownership.
- **Stage 5 is full CRUD, not read-only.** The original stage-order line said
  "read-only." Changed because Stages 18–21 are frontend admin layers
  (services/specialists/working-hours management, per § Agreed stage order)
  and need a write API to already exist by the time they're built — exactly
  the same reasoning Stage 4 used to justify catalog CRUD rather than a
  read-only catalog API. Reads stay public (`AllowAny`) and writes gated by
  `IsSalonStaff`, the same split as catalog.
- **`Specialist.is_active` moves from Stage 20 to Stage 5.** `docs/ARCHITECTURE.md`
  § 5 previously assigned this field to Stage 20 ("admin services, specialists,
  working hours, days off"), reasoning that Stage 2 had no admin UI to set it.
  That reasoning no longer holds once Stage 5 ships a write API of its own — a
  specialist CRUD API with no way to deactivate a former employee without a
  hard delete would immediately violate the "never hard-delete a row an
  `Appointment` might reference" rule (`docs/ARCHITECTURE.md` § 5). Moved
  earlier so the CRUD API is complete on arrival rather than shipping a
  known-incomplete delete story now and patching it in fifteen stages. Its
  meaning is employment status only: `True` = currently employed, `False` =
  no longer employed. It is explicitly **not** a general availability or
  visibility flag — it says nothing about whether a specialist is bookable on
  a given day.
- **Temporary absence (vacation, sick leave, parental leave) is not modelled
  as a status on `Specialist` — it is `TimeOff` rows, which already exist.**
  Considered and rejected: adding an absence-related status field would
  duplicate what `TimeOff` already represents and raise a question `TimeOff`
  doesn't have to answer — how long an absence must be before it flips a
  status, and what happens automatically when it's over. No duration
  threshold is needed anywhere in code for this: the booking/availability
  window is capped at 60 days out (`docs/DECISIONS.md` § Business rules), so a
  specialist absent longer than that simply has no bookable slots inside the
  window the client can ever see — the absence is invisible on its own,
  without any code needing to know how long it is or classify it as "long"
  vs. "short."
- **No `UniqueConstraint(salon, name)` on `Specialist`, and consequently no
  `validate_name`.** The catalog pattern (`(salon, name)` uniqueness on
  `ServiceCategory`/`Service`, with the DRF 3.18 unique-together trap worked
  around via an explicit `validate_name`, `docs/DECISIONS.md` § Stage 4
  decisions) does not transfer here. Catalog uniqueness models a real business
  rule — a salon shouldn't offer two identically-named services. Two
  specialists sharing a name is not an error; salons legitimately employ two
  people with the same first name. Since there's no uniqueness constraint on
  `Specialist`, the DRF gotcha that motivated `validate_name` on the catalog
  serializers doesn't arise here — there is nothing for the automatic
  validator to silently skip, because there is no constraint for it to be
  built from.
- **Stage 6 (availability engine) must exclude `is_active=False` specialists
  from slot computation, and this does not follow automatically from
  `TimeOff`.** A terminated employee has no `TimeOff` rows — nothing marks
  their calendar as unavailable — so an engine that only subtracts
  `WorkingHours` minus `TimeOff` minus existing appointments would happily
  keep offering slots for someone who no longer works at the salon. Recorded
  here, against Stage 5, rather than left for Stage 6 to discover, because
  `is_active` is deliberately *not* an availability flag (the point directly
  above) — that framing makes it easy for a later reader to conclude the
  availability engine has no business consulting it at all, when in fact it
  must, just as a hard exclusion rather than as part of the open-windows
  computation.
- **Deactivating a specialist with future non-cancelled appointments is refused
  (409), not allowed-and-cleaned-up.** `docs/DECISIONS.md` § Business rules
  already requires that a staff-side change conflicting with an existing
  appointment be detected and explicitly resolved — never silently orphaned —
  offering the customer a rebooking with another specialist, a rebooking with
  the same specialist later, or a fully refunded cancellation. Stage 5 has no
  payment layer (Stage 8), no notifications (Stage 10), and no
  conflict-resolution flow (Stage 19); it can detect the conflict but cannot
  honor any of those three resolutions. Refusing outright is the only behavior
  consistent with the existing rule until those stages exist. Revisit at Stage
  19, when the real resolution flow can replace this refusal.
- **"Still expected," for that refusal, reuses `booking`'s own
  `ACTIVE_APPOINTMENT_STATUSES` (`PENDING_PAYMENT`, `CONFIRMED`) directly,
  imported from `booking.models` — not a second, hand-written list.** The
  double-booking exclusion constraint (`booking/models.py`,
  `appointment_no_overlapping_active_bookings`) already treats exactly this
  status set as "this appointment holds a slot," and it's already reused once
  across an app boundary this way (`booking/views.py`'s guest-cancellation
  eligibility check). "Does this appointment hold a slot" and "does this
  appointment block specialist termination" are the same underlying question;
  importing the same constant, rather than writing a second one, is what keeps
  them from silently drifting apart the day a status is added or renamed.
- **"Future," for that refusal, means `end_datetime > now()` — `blocked_until`
  was considered and rejected.** `blocked_until` (`end_datetime` plus the
  service's buffer minutes) is the more conservative-looking option, but the
  buffer exists to block the calendar for room/equipment turnaround — it is
  not the specialist's obligation to a customer. The refusal exists because a
  customer is still expected; once the visit itself ends, nobody is waiting on
  the specialist anymore, even if the room is nominally still occupied for
  cleanup. Using `end_datetime` also means an appointment already in progress
  (started, not yet ended) counts as blocking — the specialist has a live
  commitment right up until the visit ends. `now()` is
  `django.utils.timezone.now()`, compared directly against the UTC-stored
  `end_datetime` with no conversion (`docs/DECISIONS.md` § Timezone).
- **The 409 response includes both a count and the conflicting appointment
  ids:** `details={"future_appointment_count": N, "future_appointment_ids":
  [...]}`. A bare count (the original proposal) was rejected as leaving staff
  with a known blocker and no way to act on it without a manual hunt. Full
  appointment detail (customer name, time, service) was also rejected: that
  would mean designing a response shape for Stage 19's not-yet-built
  conflict-resolution UI before its actual needs are known. Ids are neither —
  they're keys the caller already has a way to resolve, cost nothing extra
  (the same query that produces the count produces them), and carry no
  customer-facing information.
- **`core/exceptions.py`'s handler is deliberately narrowed to `UniqueViolation`,
  so a `ForeignKeyViolation` from a composite tenant FK surfaces as a 500, not a
  400 — established empirically, not assumed, by Stage 5 sub-step 4's
  deliberate-break exercise.** Swapping `SpecialistSerializer`'s tenant-scoped
  queryset for `unscoped_objects` let a cross-salon service id pass validation;
  the resulting `.save()` raised a raw `psycopg.errors.ForeignKeyViolation` from
  `SpecialistService`'s composite tenant FK, and the handler's
  `UniqueViolation`-only rescue (by design — an unrelated integrity bug should
  surface loudly, not be reinterpreted as client error) does not catch it.
  Consequence, generalized beyond this one field: the composite tenant FK
  guarantees a cross-tenant row can never be written, full stop — but it is a
  last resort that fails loudly (500), never gracefully (400). The clean,
  field-specific 400 always comes entirely from the serializer-level
  tenant-scoped queryset check, never from the database constraint underneath
  it. This applies to every future `many=True` tenant-scoped relation, not only
  `services`.

## Stage 6 decisions (availability engine)

Decided 2026-08-13, before implementation — recorded here as agreed-in-advance
decisions, not as notes written after the fact.

- **Buffer at the end of a shift: the buffer must fit inside the specialist's
  working window, not spill past it.** The last bookable start time for a
  window is `window_end − service.duration_minutes − service.buffer_minutes`.
  Reasoning: the salon physically closes at the end of the shift; a buffer
  extending past window end would require the specialist to do
  equipment/room turnaround in a building that's already locked — the buffer
  is real time someone needs, not a bookkeeping convenience that can slide
  past closing. Rejected alternative: letting the buffer spill past window
  end, which gains exactly one extra slot per specialist per day but assumes
  the premises stay open past the nominal closing time — an assumption
  nothing else in this project makes. Consequence worth stating explicitly
  because it's easy to get wrong in a test: the last slot of a day is not
  necessarily on a round slot-granularity boundary from window start — e.g. a
  shift ending at 21:00, a 60-minute service with a 15-minute buffer, gives a
  last bookable start of 19:45, not 20:00 or 20:15.
- **Overlapping `WorkingHours` rows are merged into disjoint windows at
  ingestion time (window-building), not deduplicated at output.**
  Reasoning: nothing in the schema or the write path prevents two overlapping
  rows for the same specialist/day today — no uniqueness constraint, no
  `clean()`, and the rows are already admin-writable
  (`specialists/admin.py`'s `WorkingHoursAdmin`, plain `SalonScopedAdmin`, no
  overlap check) — so the engine must tolerate the data as it actually can
  exist, not as it ideally would. A duplicated slot surfacing in an API
  response because two overlapping rows both generated it independently is a
  **wrong answer** (the caller sees the same start time twice, or double-
  counts it in a merge elsewhere), not merely wasted computation. Rejected
  alternative: deduplicating identical slots at the output stage instead —
  rejected because it requires two mechanisms (a merge-shaped one at output,
  doing the same job the window-building step could do once) where one at the
  source suffices, and because dedup-at-output has to reason about slot
  *equality* after the fact instead of window *disjointness* before the fact,
  a strictly harder invariant to get right. Split shifts — two genuinely
  non-overlapping `WorkingHours` rows for the same day, with a lunch break
  represented as the absence of a row between them, per the model's own
  docstring — are unaffected: merging only ever acts on rows that actually
  overlap.
- **Salon-level closure (public holidays, planned shutdowns) is not modelled
  in Stage 6.** Recorded instead as an open question (§ Open questions,
  below). Reasoning: the real requirement doesn't decompose into one shape —
  it's at minimum one-off closures, annually-recurring holidays, partial-day
  closures, and eventually per-room rather than per-salon closures — and a
  minimal model built now to unblock Stage 6 would almost certainly be
  rewritten, with a data migration, once the real requirement is scoped
  properly (likely alongside the admin calendar work, Stage 19/20). **This
  has a consequent design requirement for Stage 6 itself, not just a
  deferral note:** the window-building step's blocking-interval subtraction
  must accept a generalised list of blocking intervals, not be hardcoded to
  read `TimeOff` specifically, so a future closure source becomes one
  additional entry in that list rather than a rewrite of the subtraction
  logic. See `docs/ARCHITECTURE.md` § 6 for the corresponding wording.
- **Interval boundaries — established fact, not a decision.** Postgres's
  `TSTZRANGE(a, b)` with the bounds argument omitted defaults to `'[)'` —
  inclusive lower bound, exclusive upper bound. The exclusion constraint
  (`booking/models.py`'s `appointment_no_overlapping_active_bookings`) uses
  exactly this two-argument form, so a candidate starting exactly at a
  previous appointment's `blocked_until` does not overlap it and is free.
  This isn't a new decision — § Business rules already states "a following
  appointment may start exactly when the buffer ends," and it's already
  covered by a passing test,
  `test_back_to_back_appointment_after_buffer_is_allowed`
  (`tests/test_booking_exclusion_constraint.py`) — recorded here only so the
  availability engine's own slot-walking arithmetic is written to agree with
  it deliberately, not by accident.
- **Multi-specialist availability: both modes are required, as two
  functions, not one.** The per-specialist function stays the primitive,
  with the signature `docs/ARCHITECTURE.md` § 6 already documents (specialist
  + service + date range). A separate, thin composition function in the same
  `scheduling` app handles "any available specialist for this service": it
  enumerates the service's `SpecialistService`-qualified, `is_active`
  specialists and unions each one's per-specialist availability. Rejected
  alternatives: (a) making `specialist` optional on the primitive itself —
  rejected because it turns one function into two behaviors gated by whether
  an argument is `None`, signature sprawl for what is really a distinct,
  higher-level operation (fan-out + union) layered on top of the primitive,
  not a variant of it; (b) leaving the fan-out to the frontend — rejected
  because it turns "show me any available time for this service" into N HTTP
  requests per page view (one per qualified specialist), which doesn't scale
  with the number of specialists a salon employs and pushes a backend
  concern onto every client. Named explicitly, these are the two booking
  entry paths: **"any specialist"** goes through the composition wrapper —
  a time is available if at least one qualifying specialist is free then;
  **"specific specialist"** calls `compute_candidate_start_times` directly,
  no composition. Both read through the same single-specialist engine and
  differ only in the wrapper above it.
- **Reversed 2026-08-15 — response shape for the multi-specialist mode: a
  slot in the availability response is a bare time, not a list of
  specialists.** This reverses the decision originally recorded here on
  2026-08-13: "each slot carries the list of specialists available at that
  time, not a bare time," reasoned on the grounds that "a specialist is not
  an interchangeable resource... the composition function already has every
  qualified specialist's name in hand while computing the union, so
  attaching it costs nothing extra." That reasoning about the *data* being
  cheap to attach was correct and still holds — the reversal is about
  *presentation*, not cost: each specialist is shown with a photo and a
  description, which does not fit stacked under every time row and needs
  its own screen. The specialist-availability mapping the composition
  function computes while unioning is therefore still computed, exactly as
  before — it is simply not serialized into this response. It is what the
  second endpoint (§ Stage 6.G decisions) serves once the client has picked
  a time; nothing here is discarded, only deferred to the next request.
- **Stage 6 scope includes a read-only `GET` endpoint, not only the internal
  `scheduling` service function.** Reasoning: where tenant context gets
  bound for `TenantScopedManager` (`core.tenancy.tenant_context`, per
  `docs/ARCHITECTURE.md` § 5) can only really be answered while an actual
  view is being written against an actual request — designing the service
  function's signature without ever exercising it through a view risks
  getting that signature wrong and discovering it only once Stage 7 (already
  the heaviest remaining stage) is underway and depending on it. The stage
  title's "read-only" was originally ambiguous between "the computation has
  no side effects" (true of the internal function regardless) and "there's a
  `GET` endpoint" — this decision resolves it to mean both.
- **DST handling belongs at the window-building step, not only at response
  formatting.** `WorkingHours.start_time`/`end_time` are plain `TimeField`s
  keyed by `day_of_week` — salon-local wall-clock time with no date attached.
  Turning "09:00–18:00 on day X" into a concrete UTC interval requires
  localizing against a specific calendar date via `zoneinfo`, and on a
  DST-transition day that localization changes the real UTC duration of the
  window (8 or 10 hours, not the naive 9) — so the DST-aware conversion has
  to happen at window construction, before subtraction and slot-walking even
  start, not only when converting the final candidate slots back to local
  time for the response.

### Stage 6.C decisions (open-window computation — design addendum)

Decided 2026-08-13, before implementation, resolving the open questions raised
by the 6.C design proposal.

- **`compute_open_windows`'s `date_from`/`date_to` are salon-local calendar
  dates (`dt.date`), not UTC instants.** Reasoning: a customer asks "what's
  free on 15 September" in salon-local terms — accepting UTC instants here
  would push a day-boundary explanation into every layer above
  `compute_open_windows` (the composition function, the eventual `GET`
  endpoint, the frontend) instead of settling it once, at the one place that
  already has to reason about salon-local wall-clock time (`WorkingHours`
  itself).
- **`date_from > date_to` raises, rather than returning an empty list.**
  Reasoning: an invalid range is a caller error, not a legitimate data state
  — an empty list is indistinguishable from "genuinely nothing available,"
  which would let a view-level bug (e.g. swapped query params) pass through
  silently instead of surfacing. The eventual `GET` endpoint translates the
  exception into a 400.
- **`_subtract_intervals` must produce a result independent of
  blocking-interval order, including when blockers overlap each other.**
  Same reasoning already applied to `WorkingHours` above: nothing in the
  schema prevents two overlapping `TimeOff` rows for the same specialist
  either — no uniqueness constraint, no `clean()` — so the generalised
  subtraction step must tolerate that shape of input, not just the shape the
  schema happens to make more common.
- **The pure window/interval functions (`_merge_intervals`,
  `_subtract_intervals`) use the same half-open `[start, end)` convention as
  the `Appointment` exclusion constraint** — a window boundary that only
  touches a blocker, without overlapping it, is not clipped. Reasoning: this
  project already has one boundary convention, established for the exclusion
  constraint (the "Interval boundaries — established fact" decision above);
  running a second, different convention through the pure scheduling
  functions would be a guaranteed off-by-one the moment the two are compared
  or composed.
- **`_merge_intervals` merges intervals that touch with zero gap (one ends
  exactly where the next begins), not only intervals that strictly
  overlap.** Two `WorkingHours` rows with no gap between them (e.g.
  `09:00–12:50` and `12:50–18:00`) describe one continuous period of work —
  how a shift happens to be split into rows is incidental data entry, not a
  fact about the day. This matters concretely once slot-walking exists
  (a later substage): with granularity 20 minutes, merging the two rows
  above into one `09:00–18:00` window walks candidates `..., 12:40, 13:00,
  ...`, while leaving them as two separate windows walks `..., 12:40 |
  12:50, 13:10, ...` — a different candidate grid for the rest of the day,
  entirely as a side effect of how the row happened to be split. An
  incidental data-entry choice must not shift the whole day's slot grid.
- **`is_active=False` specialist exclusion is guarded in two places, not
  one: an early-return empty list inside `compute_open_windows`, and the
  caller filtering its specialist list before calling in.** Mirrors this
  project's existing serializer-plus-database-constraint pattern (e.g. the
  tenant-scoped `validate_name` check backstopped by the database's own
  `UniqueViolation` translation, § Stage 4 decisions) — the cheap local
  check inside `compute_open_windows` costs one attribute access and stops a
  forgetful caller from ever reaching the ORM queries at all, while the
  caller-side filter is what actually matters for the multi-specialist
  composition function's enumeration (§ Stage 6 decisions above) and avoids
  spending a query on a specialist who can never have a window regardless.
- **A malformed `Salon.timezone` (a string `zoneinfo.ZoneInfo` can't
  resolve) is not handled inside `compute_open_windows` or
  `_localize_window`.** Reasoning: validating that `Salon.timezone` is a
  real IANA zone name is a write-time concern — it belongs wherever
  `Salon.timezone` is created or edited, not on every read of every
  availability computation. Recorded as an open question (§ Open questions)
  rather than handled defensively here.
- **Window/interval values are a `NamedTuple` (`Window(start: datetime, end:
  datetime)`), not a bare `tuple[datetime, datetime]`, from the start of
  Stage 6.C.** Reasoning: these values travel through multiple further
  layers (6.D's busy-interval subtraction, the multi-specialist composition
  function, and eventually a serializer) — `w[0]`/`w[1]` at the third or
  fourth layer of composition is unreadable and error-prone (nothing stops
  swapping start/end positionally), while `w.start`/`w.end` is
  self-documenting at every layer. Decided now, rather than deferred as a
  style choice, specifically so no downstream substage gets written against
  the bare-tuple shape and then needs a mechanical rewrite.
- **`salon_timezone` stays a `str` parameter on `compute_open_windows` and is
  converted to a `zoneinfo.ZoneInfo` once, inside the orchestrator, before
  being passed down to `_localize_window`.** Recorded here as a deliberate
  placement, not an accident to be rediscovered while debugging: it means a
  `zoneinfo.ZoneInfoNotFoundError` (an unresolvable timezone name — see the
  malformed-`salon_timezone` decision above) surfaces from
  `compute_open_windows` itself, not from a helper several calls deep.
  `compute_open_windows`'s docstring states this explicitly.
- **Stage 6.C's computation lives in `scheduling/services.py`, not a
  separately-named module.** Matches this project's existing service-layer
  convention (`catalog/services.py`, `specialists/services.py`) — naming
  this one app's equivalent module something else would leave an
  unexplained inconsistency about where business/computation logic lives
  across apps, for no benefit.
- **`date_from > date_to` raises `core.exceptions.InvalidDateRangeError`, a
  new `DomainError` subclass.** Its HTTP-status/error-envelope mapping is
  not decided now — that lands with the `GET`-endpoint substage, alongside
  `docs/ARCHITECTURE.md` § 14's `EXCEPTION_HANDLER`.

### Stage 6.D decisions (busy-interval subtraction from Appointments)

Decided 2026-08-13, before implementation, resolving the open questions
raised by the 6.D design proposal.

- **6.D extends `compute_open_windows` directly — no new orchestrator
  function, no second `_subtract_intervals` call.** Two new functions only:
  `_fetch_appointments` (mirrors `_fetch_time_off`, with the added
  `status__in=ACTIVE_APPOINTMENT_STATUSES` filter `TimeOff` has no
  equivalent of) and `_appointments_to_blocking_intervals` (mirrors
  `_time_off_to_blocking_intervals`). Their output is concatenated into the
  same blocking-interval list already built for `TimeOff`, before the
  single existing `_subtract_intervals` call. Reasoning: `_subtract_intervals`
  was built in § Stage 6.C decisions specifically to not know a blocker's
  source; a second real source is that generalisation being used for the
  second time it was built for, not a new step. `compute_open_windows`'s
  signature and return type are unchanged.
- **The interval subtracted is `(start_datetime, blocked_until)`, never
  `(start_datetime, end_datetime)`.** This is the same buffered interval
  the `Appointment` exclusion constraint itself protects
  (`docs/ARCHITECTURE.md` § 2, § 7; `booking/models.py`'s
  `appointment_no_overlapping_active_bookings`). Subtracting the bare
  service interval instead would let this engine offer a start time the
  database is guaranteed to reject — not as a race-condition "you lost the
  race" case, but deterministically, every time.
- **Blocking statuses are `booking.ACTIVE_APPOINTMENT_STATUSES`, imported
  directly, not a second hand-written list.** Same reuse principle already
  applied in § Stage 5 decisions for `specialists/services.py`'s own
  future-appointment check — `CANCELLED`, `EXPIRED`, `COMPLETED`, and
  `NO_SHOW` appointments never block.
- **`hold_expires_at` is not read by this substage.** A `PENDING_PAYMENT`
  appointment whose hold has already passed, but whose status the
  not-yet-built expiry sweep hasn't flipped to `EXPIRED` yet, still counts
  as blocking. This is the correct failure mode for a read path: it can
  only ever under-offer availability, never over-offer it — the
  double-booking guarantee comes from the database exclusion constraint,
  which doesn't consult `hold_expires_at` either. The sweep itself is Stage
  7's, not this substage's.
- **`django_assert_num_queries(3)` is a required test, not an optional
  nice-to-have.** `compute_open_windows` issues exactly three queries after
  this change (`WorkingHours`, `TimeOff`, `Appointment`) — pinned because
  this project has already lost query efficiency to an unpinned regression
  once (a missing `select_related` in Stage 4, caught only because a
  query-count assertion existed elsewhere). The risk here is identical: a
  future change silently turning one fetch into an N+1, with nothing else
  in the suite positioned to notice.
- **Blocker provenance (which source — `TimeOff` vs. `Appointment` —
  produced a given blocked span) is not carried through
  `_subtract_intervals`'s output.** Recorded as an open question (§ Open
  questions, below), not solved here: carrying it through would change
  `_subtract_intervals`'s return type and break the "knows nothing about
  its sources" contract that is the entire point of the § Stage 6.C
  decisions generalisation. The one concrete use case for it (Stage 19's
  admin calendar explaining *why* a slot is unavailable) can read
  `Appointment`/`TimeOff` rows directly rather than reconstructing them
  from derived slots, so the need is speculative, not demonstrated.

### Stage 6.E decisions (slot stepping)

Decided 2026-08-14, before implementation, resolving the open questions raised
by the 6.E design proposal.

- **Two layers, in `scheduling/services.py`, alongside the 6.C/6.D pure
  functions and `compute_open_windows`:**

  ```python
  def _step_windows(
      windows: Sequence[Window],
      granularity_minutes: int,
      occupied_minutes: int,
  ) -> list[dt.datetime]: ...

  def compute_candidate_start_times(
      specialist: Specialist,
      service: Service,
      salon: Salon,
      date_from: dt.date,
      date_to: dt.date,
  ) -> list[dt.datetime]: ...
  ```

  `_step_windows` is pure — plain numbers for `granularity_minutes`/
  `occupied_minutes`, but still the `Window`/`dt.datetime` vocabulary
  `_merge_intervals`/`_subtract_intervals` already use, not a further
  reduction to bare numbers, which would break consistency with the rest of
  the pure layer for no benefit. `compute_candidate_start_times` calls
  `compute_open_windows` itself — the same shape already established for
  the (not yet built) multi-specialist composition function, which the
  Stage 6 decision on multi-specialist availability specifies as calling
  the per-specialist primitive itself rather than receiving its results —
  and resolves `occupied_minutes = service.duration_minutes +
  service.buffer_minutes`, `granularity_minutes =
  salon.slot_granularity_minutes`, then calls `_step_windows`. Rejected
  alternative: the orchestrator receiving pre-computed `windows` as an
  argument instead of calling `compute_open_windows` itself — rejected
  because no caller in current or near-term scope (the GET endpoint, the
  composition function) has a reason to compute windows and stepped slots
  separately, and passing windows in would require every caller to keep
  `specialist`/`date_from`/`date_to`/`salon` in sync across two call sites
  instead of one, the same caller-must-remember-an-invariant shape this
  project has already avoided elsewhere (`TenantScopedManager`, the
  `is_active` double guard, § Stage 6.C decisions).
- **Grid remainder: strict grid only, no off-grid last slot.**
  `_step_windows` walks each window independently from `window.start` in
  steps of `granularity_minutes`, keeping a candidate only while `candidate
  + occupied_minutes <= window.end`; a tail shorter than one full step is
  never offered, even when `occupied_minutes` would fit inside it.
  Rejected alternative: additionally emitting `window.end -
  occupied_minutes` as an explicit final candidate whenever it lands
  off-grid. Rejected because it would make the primitive carry two rules
  instead of one, and would produce visibly uneven spacing between the last
  two candidates in what's ultimately a slot picker. The Stage 6 decision
  on buffer-at-shift-end (§ Stage 6 decisions, "Buffer at the end of a
  shift") is not evidence for the rejected alternative, despite reading
  that way out of context: its example — a window starting 09:00, a
  60-minute service with a 15-minute buffer, "last bookable start of
  19:45" — sits on the strict grid already. 19:45 is 645 minutes after
  09:00; at the salon's default `slot_granularity_minutes` of 15, 645 / 15
  = 43 exactly, so 19:45 is step 43 from window start, not an off-grid
  value the strict rule would have to special-case. That decision was
  recorded before slot-stepping was designed at all, as a statement about
  the *closed-form ceiling* on the last bookable start (`window_end -
  occupied_minutes`), not a claim that this ceiling must always be offered
  regardless of grid alignment. Recorded here explicitly so this isn't
  re-opened by a future reader skimming that example out of context.
- **`granularity_minutes <= 0` and `occupied_minutes <= 0` raise, inside
  `_step_windows`, rather than being silently trusted.** A new
  `DomainError` subclass, `InvalidSteppingParametersError` (`core/
  exceptions.py`, `code = "invalid_stepping_parameters"`, `status_code =
  500`), in the `DomainError`-subclass shape `InvalidDateRangeError` uses
  (§ Stage 6.C decisions) but deliberately mapped to a different status:
  `InvalidDateRangeError`'s `date_from > date_to` arrives in the request
  itself, so 400 correctly tells the client their input was malformed and
  fixable by sending a different request; `InvalidSteppingParametersError`
  fires because a `Salon` or `Service` row already in the database is
  misconfigured (§ Open questions, "Lower-bound validation on
  `Salon.slot_granularity_minutes`..." above) — the request that triggered
  it may be perfectly well-formed, the client has nothing to change, and
  telling them otherwise sends the frontend into a dead end. 500 is
  correct here precisely because it is server misconfiguration, not caller
  error, despite both being `DomainError` subclasses raised from the same
  service layer. This mapping is recorded now but not implemented in 6.E —
  same as `InvalidDateRangeError`'s own HTTP mapping (§ Stage 6.C
  decisions), it lands with the `GET`-endpoint substage, alongside
  `docs/ARCHITECTURE.md` § 14's `EXCEPTION_HANDLER`. Reasoning this needs a
  guard at all, rather than trusting the schema like `_subtract_intervals`
  trusts
  well-formed `Window`s: `Service.duration_minutes >= 1` is enforced only
  by `catalog/serializers.py` (`min_value=1`), not at the model/DB layer,
  and `Salon.slot_granularity_minutes` has no lower-bound enforcement
  anywhere yet — no `Salon` serializer exists, and the admin form has no
  validator. `granularity_minutes = 0` is therefore creatable through
  `/admin/` today, and the naive stepping loop (`candidate +=
  timedelta(minutes=granularity_minutes)`) would not raise or return wrong
  data on such a row — it would hang the request (or Celery task, or AI
  assistant tool call) indefinitely. That failure mode is categorically
  worse than the other three options this project already guards against
  (silent wrong data, a loud crash, or a clear domain error), so it gets
  the same treatment `InvalidDateRangeError` gets rather than being left
  for the write-side validation this project doesn't have yet (tracked
  as an open question below, and § Open questions above).
- **`occupied_minutes` larger than every open window in range returns an
  empty list, not an exception.** Already implied by
  `docs/ARCHITECTURE.md` § 6's existing edge-case note ("a service
  duration longer than any single open window never produces a slot —
  correct behavior, not a bug"); this decision only confirms
  `compute_candidate_start_times` follows that same rule rather than
  treating "nothing available" as an error condition. No `_step_windows`
  or `compute_candidate_start_times` code change is implied beyond what §
  Grid remainder above already describes — a window that can't fit even
  one candidate simply contributes none, the same as a window with a
  0-minute-wide tail.
- **`compute_candidate_start_times` takes `salon: Salon` as an explicit
  parameter, not derived from `specialist.salon`.** Rejected alternative:
  deriving `salon = specialist.salon` inside the orchestrator, which would
  save one constructor argument. Rejected because it's a lazy FK access —
  firing a fourth query, on first attribute touch, on top of the three
  `compute_open_windows` already issues and that `django_assert_num_queries
  (3)` pins (§ Stage 6.D decisions) — and because it would happen silently,
  inside a function whose entire job is translating already-resolved model
  objects into numbers; if that's the job, it should receive the objects it
  translates rather than reach for one of them itself. With `salon` passed
  in, `compute_candidate_start_times` itself issues zero queries of its own
  (pure resolution plus the `compute_open_windows` call), so the pinned
  query count is unaffected by this substage. `compute_open_windows`'s own
  signature is unchanged — the orchestrator passes `salon.timezone` into it
  as the existing `salon_timezone: str` parameter, forwarding rather than
  re-deciding that convention (§ Stage 6.C decisions).
- **`ARCHITECTURE.md` § 6 step 3 wording is unchanged.** The strict-grid
  remainder rule, the `InvalidSteppingParametersError` guard, and the exact
  function signatures above are Catalog-precedent detail (`docs/DECISIONS.md`
  § Stage 4 decisions' endpoint lists and exception classes never
  appearing in `ARCHITECTURE.md`) — they belong only here, not folded into
  `ARCHITECTURE.md`'s "what" description of step 3, which already covers
  slot-granularity stepping and the full-duration-plus-buffer requirement
  at the right altitude and needs no addition for this substage.

### Stage 6.F decisions (booking-window filtering)

Decided 2026-08-14, before implementation. No separate 6.F design proposal
preceded this section — these decisions came directly out of discussion and
are recorded here as agreed, not resolved against a prior written proposal.

- **`now` is an explicit parameter, with no default and no `None` fallback.**
  The system clock is I/O, the same category as the ORM, and this project
  already isolates I/O in the `_fetch_*` functions (§ Stage 6.C decisions). A
  default would let a test silently omit it and go green for the wrong
  reason — the same failure shape as the fixture collision found in 6.E.
  `timezone.now()` is called exactly once, in the view, landing with the
  `GET`-endpoint substage; until then, tests pass it explicitly.
- **Minimum lead time is a duration.** A candidate is kept if `candidate >=
  now + timedelta(hours=salon.min_lead_time_hours)`.
- **Maximum advance is a calendar boundary, not a duration.** Compute today's
  date in the salon's timezone, add `salon.max_advance_days`, and the
  exclusive upper bound is midnight at the start of the *following* day in
  the salon timezone, converted to UTC; a candidate is kept if `candidate <
  boundary`. Half-open `[)`, consistent with the rest of the engine and with
  `TSTZRANGE`. Rejected alternative: `now + timedelta(days=N)`. Rejected
  because a rolling boundary moves continuously — a customer refreshing the
  page at 23:00 sees a day that was not there at 01:00 — and because salon
  staff reason about "60 days ahead" in calendar days, not 1440 hours. The
  resulting asymmetry — the lower bound is a moment, the upper bound is a
  calendar edge — is deliberate: lead time protects the specialist from a
  last-minute booking, max advance is the salon's planning horizon, and
  those are different kinds of limits with different natural units. Only the
  upper bound needs the salon timezone; the lower bound does not.
- **A new pure function does the filtering, called by the orchestrator after
  `_step_windows`.** Rejected alternative: trimming the open windows
  themselves before stepping. Rejected because trimming would move the
  stepping anchor, and § Stage 6.C decisions' decision 5 pins that anchor to
  the window start — trimming a window's start to the lead-time or
  max-advance boundary would make slot times depend on when the request
  happened to arrive, the same grid-stability problem 6.C decision 5 already
  rules out for a different reason.
- **The max-advance boundary computation gets its own pure function,
  `_max_advance_boundary(now: dt.datetime, max_advance_days: int, tz:
  ZoneInfo) -> dt.datetime`, with an inlined two-line body — not a call to
  `_localize_window(...).start`.** `_localize_window` does not validate
  `start_time` against `end_time`, and `start_time == end_time` is already
  the established idiom in this module for extracting a single instant —
  `compute_open_windows` uses it twice today, for `range_start_utc` and
  `range_end_utc` — so reusing it here would have worked. It is rejected on
  concept, not on breakage: the arithmetic is identical, but
  `_localize_window` builds a working-hours `Window`, and this is a single
  cutoff instant, not a window that happens to have zero width. Routing
  through it would mean constructing a `Window` only to discard half of it,
  leaving a reader wondering why a booking-window boundary is built out of a
  working-hours type. Giving the boundary computation its own name and body
  also makes it directly unit-testable in isolation — including the § Open
  questions entry on midnight-transition zones, with a synthetic
  `ZoneInfo("America/Santiago")` on its transition day — with no ORM, no
  `tenant_context`, and no fixtures.
- **`now` must be timezone-aware; `compute_candidate_start_times` checks this
  on its very first line, before anything else runs, and raises a plain
  `ValueError` if it isn't** — via `django.utils.timezone.is_naive(now)`,
  not a hand-rolled `now.tzinfo is None`, because `is_naive` correctly
  handles a `tzinfo` object that is set but returns `None` from
  `utcoffset()`, which the hand-rolled check would miss. Placed first for
  the same reason `date_from > date_to` is the first thing
  `compute_open_windows` checks, and the same reason § Stage 6.C decisions
  resolves `ZoneInfo` at the top rather than several calls deep: a naive
  `now` should always fail the same way, in the same place, not sometimes
  loudly and sometimes silently depending on what else happens to run
  first. Without this guard, a naive `now` survives `now + timedelta`
  unchanged, then `.astimezone(tz)` silently presumes the host's local zone
  instead of raising, and `_filter_candidates_by_booking_window`'s
  comparison only raises `TypeError` once `candidates` is non-empty — with
  an empty candidate list the whole call would instead return `[]`,
  indistinguishable from "genuinely nothing available." Raises a plain
  `ValueError`, not a new `DomainError` subclass: every existing
  `DomainError` subclass exists so `core.exceptions.exception_handler` can
  turn it into a structured client-facing response, for errors a request or
  a misconfigured database row can trigger. A naive `now` can be neither —
  per the first decision above, `timezone.now()` is called exactly once, in
  the view, and is always aware — so the only way a naive value reaches
  this function is a caller's code being wrong (a test, or a future
  non-view caller such as a Celery task or the AI assistant's tool
  function), with no API boundary to cross. This follows the same
  precedent already set in this module: `compute_open_windows` lets
  `zoneinfo.ZoneInfoNotFoundError` propagate raw, unwrapped, for the same
  reason (§ Open questions, "`Salon.timezone` write-time validation") — a
  problem with the caller or the configuration, not with client input.

Verified before writing the above: `Salon.min_lead_time_hours` (default `3`)
and `Salon.max_advance_days` (default `60`) match § Business rules' stated
defaults — no mismatch found. `specialist.salon_id` is a plain column on
`TenantScopedModel` (a standard `ForeignKey`), so it is present on any
normally-fetched `Specialist` row with no additional query; only
`specialist.salon` (the lazy relation, not the `_id` column) would fire one.

### Stage 6.G decisions (multi-specialist response-shape reversal — second-lookup endpoint)

Decided 2026-08-15, before implementation. No separate 6.G design proposal
preceded this section — these decisions came directly out of discussion and
are recorded here as agreed, not resolved against a prior written proposal.
Companion to the response-shape reversal recorded in § Stage 6 decisions
above ("Reversed 2026-08-15").

- **The second endpoint (specialist lookup for a chosen time) takes a raw
  time plus `service` and the same date range already used for the
  time-grid call as parameters — no token, no server-side stored
  computation.** Consistent with this project's existing minimal-state
  preference. It always **re-computes** which qualifying specialists are
  free at that time; it never reuses a cached result from the first
  (time-grid) response — the specialist-availability mapping computed while
  building the time grid is not persisted or handed back as an opaque
  reference to be redeemed later. A **re-computed empty result is a valid
  answer, not an error**: between the two requests another booking may have
  taken the only qualifying specialist at that moment (the ordinary booking
  race already described in `docs/ARCHITECTURE.md` § 6's edge cases and
  guarded for real at booking time per § 7), so the endpoint returns an
  empty list as such, never mapped to a 4xx/5xx — the client's job is to
  tell the customer the time is no longer available and let them pick
  another, the same way it would treat an empty time grid.
- **The "specific specialist" pick list is filtered by `is_active` before
  any slot computation runs — an inactive specialist is never offered as
  pickable at all.** Distinct from the existing `is_active` guard inside
  `compute_open_windows` (§ Stage 6.C decisions, "`is_active=False`
  specialist exclusion is guarded in two places"): that guard concerns a
  specialist's own schedule, so a stray direct call for an inactive
  specialist still resolves to an empty result rather than stale data or a
  crash. This decision concerns an earlier step — which specialists a
  customer is offered to choose from in the first place, before "specific
  specialist" ever makes that direct call. The same `is_active` filter the
  composition function already applies when enumerating candidates for the
  "any specialist" path (§ Stage 6 decisions, "Multi-specialist
  availability") applies to this pick list too, so both entry paths present
  only currently-employed specialists to the customer, for the same reason:
  nobody should be offered to book someone who no longer works at the salon
  (§ Stage 5 decisions).

### Stage 6.H decisions (multi-specialist availability composition)

Decided 2026-08-15, before implementation. No separate 6.H design proposal
preceded this section — these decisions came directly out of discussion and
are recorded here as agreed, not resolved against a prior written proposal.

- **New function `compute_multi_specialist_availability(service, salon,
  date_from, date_to, now) -> dict[dt.datetime, list[Specialist]]`, calling
  the existing `compute_candidate_start_times` once per qualifying
  specialist and merging the results into `{start_time: [specialists free
  then]}`.** Consumed by both endpoints from § Stage 6.G decisions: the
  "any specialist" (time-grid) endpoint takes the sorted keys; the second
  (chosen-time) endpoint calls this same function again and reads the value
  for the chosen time — it does not call a different, narrower function.
  The mapping is not persisted between the two calls; each call recomputes
  it from scratch, per § Stage 6.G's "always re-computes... never reuses a
  cached result" decision. Recomputing per call is the deliberate cost of
  that freshness choice, not incidental waste — the alternative (persisting
  the first call's mapping and looking up the chosen time in it) is exactly
  the token/stored-computation shape § Stage 6.G already rejected.
- **Imperative shell / pure core split, matching the rest of this module.**
  `_fetch_qualifying_specialists(service: Service) -> QuerySet[Specialist]`
  is the only ORM-touching part — one query,
  `Specialist.objects.filter(services=service, is_active=True)`, already
  tenant-scoped via `TenantScopedManager`. No `.distinct()`:
  `SpecialistService`'s `UniqueConstraint(fields=["specialist", "service"])`
  already rules out more than one join row per specialist for a given
  `service`, so the filter can't produce duplicate `Specialist` rows to
  begin with. Merging is a separate pure function,
  `_merge_specialist_availability`, over in-memory `(specialist, times)`
  pairs — no DB, no tenant context — unit-testable the same way
  `_merge_intervals` is. It sorts keys explicitly, `dict(sorted(...))`:
  plain dicts preserve insertion order but do not sort themselves, so the
  ascending-key guarantee (§ Stage 6.G's return-shape expectations) has to
  be produced deliberately, not assumed from iteration order.
- **Mapping value is full `Specialist` objects, not ids — no lazy-load
  optimization now.** The second endpoint needs the full row to serialize
  photo and description (the reason for the response-shape reversal, §
  Stage 6 decisions). Optimizing the fetch (e.g. `select_related`/deferred
  fields) waits until an actual serializer shows a real cost — the same
  "correctness first, optimize once it's a demonstrated bottleneck" posture
  already applied to the availability engine as a whole
  (`docs/ARCHITECTURE.md` § 6, "Where it lives").
- **Qualifying specialists are those linked via `SpecialistService` AND
  `is_active=True`; an inactive specialist never appears in the mapping,
  under any key.** Zero qualifying specialists returns an empty mapping
  `{}` — a valid answer, not an error, not an exception (same posture as
  `compute_open_windows`/`compute_candidate_start_times` returning `[]` for
  "genuinely nothing available," as opposed to raising). This is a backend
  data answer only; presenting "no specialists available" to a customer is
  a frontend concern for a later stage, not something this function or its
  endpoint decides.
- **`now` is re-validated on this function's first line, via
  `timezone.is_naive(now)`, not left to the inner
  `compute_candidate_start_times` guard.** Reason, precisely: with zero
  qualifying specialists, the inner function is never called at all, so a
  naive `now` would otherwise silently produce `{}` — indistinguishable
  from "genuinely nothing available," the exact failure shape § Stage 6.F's
  naive-`now` guard was written to prevent for the single-specialist path.
  Raises a plain `ValueError`, same class and same reasoning as
  `compute_candidate_start_times`'s own guard (§ Stage 6.F decisions): not
  a new `DomainError` subclass, because the only way a naive value reaches
  this function is a caller's code being wrong, not a request or a
  misconfigured database row.

### Stage 6.I decisions (availability time-grid GET endpoint)

Decided 2026-08-15, before implementation. No separate design proposal
preceded this section — these decisions came directly out of a planning pass
over § Stage 6 decisions, § Stage 6.G decisions, § Stage 6.H decisions above,
`docs/ARCHITECTURE.md` § 6, `CLAUDE.md`, and the catalog/specialists `views.py`/
`serializers.py`/`urls.py` as the closest read-endpoint precedent — and are
recorded here as agreed, not resolved against a prior written proposal.

- **One `GET` endpoint, `/api/v1/salons/<slug>/availability/`, under the
  existing tenant path prefix.** Required query params `service` (int) and
  `date_from`/`date_to` (ISO dates); optional `specialist` (int). `specialist`
  present → the "specific specialist" path, a direct
  `compute_candidate_start_times` call; `specialist` absent → the "any
  specialist" path, `compute_multi_specialist_availability`, with the
  response taking its sorted keys. The response is **identical** in both
  modes — a list of start times, never a list of specialists (§ Stage 6
  decisions, "Reversed 2026-08-15"); who's free at a chosen time stays the
  second endpoint's job (§ Stage 6.G decisions).
- **Response shape: `{"available_times": [...]}`, not a bare top-level
  array.** Every other endpoint in this API returns a JSON object at the top
  level (DRF's pagination wraps list endpoints in `{"count", "next",
  "previous", "results"}`); a lone bare-array endpoint would be the only
  shape inconsistency in the API, for no benefit, and an object leaves room
  for metadata (e.g. a salon timezone name) if a future stage needs it,
  without a breaking shape change later. Each time is a salon-local ISO 8601
  string — `candidate.astimezone(ZoneInfo(salon.timezone)).isoformat()` — the
  UTC offset is embedded in `isoformat()`'s own output, so no separate
  timezone field is needed to interpret it. **This is where the
  previously-pending Stage 6 "local-time conversion" item lands**
  (`docs/ARCHITECTURE.md` § 6 step 5) — in this endpoint's serializer/
  response, not as a change to any `scheduling/services.py` function; every
  service function keeps returning UTC `dt.datetime`s, exactly as today.
- **Query-param validation is a plain `serializers.Serializer`, not a
  `ModelSerializer`.** There's no model instance to serialize — this is the
  first view in the codebase backed by a computed value rather than an ORM
  row. `service`/`specialist` are `PrimaryKeyRelatedField`s, rebound in
  `__init__` to the tenant-scoped querysets (`Service.objects.all()` /
  `Specialist.objects.all()`) — the same pattern `catalog/serializers.py`'s
  `ServiceSerializer.category_id` already uses, not the `.child_relation`
  variant, since neither field is `many=True`. **The existing query-param
  precedent, catalog's `category` list filter, does not apply here and is
  not being extended.** That filter is an optional refinement, silently
  ignored when malformed (`category_id.isdigit()`) — a reasonable degrade
  for a param that only narrows an otherwise-valid response. `service`/
  `date_from`/`date_to` here are required and load-bearing for the
  computation itself; silently degrading a malformed one would produce a
  wrong-shaped answer, not a graceful no-op — so malformed input must
  hard-fail via the serializer instead.
- **HTTP status mapping — all through the existing `core/exceptions.py`
  handler, with no view-level `try`/`except`:**
  - Missing or malformed `service`/`specialist`/`date_from`/`date_to` → 400
    (an ordinary DRF `ValidationError` from the query serializer, caught by
    the handler's existing generic branch).
  - Unknown or cross-tenant `service`/`specialist` id → 400, **not 404.**
    These are query-param references resolved against a tenant-scoped
    queryset, the same role `category_id` plays in `ServiceSerializer` — not
    the URL's own addressed resource, where a miss legitimately is a 404
    (`catalog`/`specialists`' `RetrieveUpdateDestroyAPIView`s).
    `TenantScopedManager` already makes a cross-tenant id indistinguishable
    from a nonexistent one at the query level, so both surface as the
    field's ordinary "does not exist" `ValidationError` — the same 400 as
    any other invalid id, not a cross-tenant leak, per CLAUDE.md's
    `category_id` precedent.
  - `InvalidDateRangeError` (`date_from > date_to`) → 400, already wired
    (`status_code = 400` on the class, § Stage 6.C decisions). The view does
    **not** re-check `date_from <= date_to` itself before calling the
    service — that check stays the service layer's job, so there is exactly
    one place, and one error shape, for that condition.
  - `InvalidSteppingParametersError` → 500, already wired (§ Stage 6.E
    decisions) — server/data misconfiguration, not a malformed request. The
    view does nothing special; the `DomainError` passes straight through the
    shared handler.
  - A naive `now` reaching a service function raises a plain `ValueError` →
    500, but this is **unreachable in practice**: `timezone.now()` under
    `USE_TZ = True` (`config/settings/base.py`) is always aware, and the view
    is the sole call site (last bullet below). No view-level code guards
    against it; if it ever fired, the handler's existing catch-all
    (`logger.exception` + the generic `internal_error` 500 envelope) already
    covers it correctly, as our bug rather than the client's.
- **`permission_classes = [AllowAny]`, set explicitly on the class.**
  `DEFAULT_PERMISSION_CLASSES` (`config/settings/base.py`) is
  `IsAuthenticated` globally — every existing public-read view (catalog,
  specialists) already overrides this explicitly for `SAFE_METHODS`, and
  omitting the override here would silently block the unauthenticated
  browsing this endpoint exists for (a customer checking availability before
  ever creating an account). No `get_permissions()`/`SAFE_METHODS` branching
  is needed, unlike catalog's/specialists' mixins — this endpoint is
  `GET`-only, so there is no write path to branch away from.
- **`specialist` in the direct-mode is resolved via `Specialist.objects.all()`
  (tenant-scoped), not filtered by `is_active`.** A deactivated specialist's
  id already yields `[]` from `compute_open_windows`'s own `is_active` guard
  (§ Stage 6.C decisions, "guarded in two places") — filtering it out at the
  view/serializer level would instead turn "no free times" into a 400
  "doesn't exist," the wrong failure mode for a legitimate, if empty,
  answer. The § Stage 6.G `is_active` pick-list rule is a different concern
  — which specialists a customer is offered to *choose from* on the
  pick-list screen — not what happens when a specific id is passed directly,
  which this endpoint's specific-specialist mode does. For the
  any-specialist path, `is_active` is already filtered inside
  `_fetch_qualifying_specialists` (§ Stage 6.H decisions); no additional
  view-level filtering is needed there either.
- **The view resolves the full `Salon` row, not only the tenant id.**
  `compute_candidate_start_times`/`compute_multi_specialist_availability`
  need `salon.timezone`, `salon.min_lead_time_hours`, `salon.max_advance_days`,
  and `salon.slot_granularity_minutes` — every existing view only ever
  touches `core.tenancy.get_current_salon_id()` (an int), so this is new:
  `get_object_or_404(Salon, pk=get_current_salon_id())`. `Salon.objects` is
  a plain, non-tenant-scoped manager — `Salon` is the tenant root, not a
  `TenantScopedModel` (`tenants/models.py`'s own docstring: "Not itself a
  TenantScopedModel — a Salon doesn't belong to a tenant, it is one").
  `now = timezone.now()` is called exactly once, here in the view, and
  passed explicitly into both service functions — the single I/O call site
  the no-default-`now` design was built around (§ Stage 6.F decisions).

### Specialist photos — resolved (Stage 6.J)

**Field shape (settled now):** `photo = CharField(max_length=1024, null=True, blank=True)` on the `Specialist` model.

**Field type — CharField, not ImageField:** `ImageField`/`FileField` are coupled
to Django's storage machinery (`DEFAULT_FILE_STORAGE`, `MEDIA_ROOT`/`MEDIA_URL`,
Pillow validation, `.save()` via `request.FILES`) — all of which activates at
upload time, and there is no upload at Stage 6. The DB stores only the object
key (a string like `salons/42/specialists/17.jpg`), so `CharField` states the
truth: a text identifier, not a Django-managed file.

**Storage:** photos live in S3-compatible object storage; the DB holds only the
key, never the bytes. PostgreSQL is not a file server — `bytea` bloats the table
and every backup, and serving images through Django kills performance. DB knows
*where*, object storage (via CDN) serves *what*. Files-in-DB rejected: only
justified for tiny blobs or hard transactional coupling; avatars are neither.

**max_length = 1024:** taken from the S3 limit (an object key is ≤1024 bytes),
NOT computed from our own key schema — because the real key schema doesn't exist
yet (upload deferred), and sizing from a guess would freeze that guess into the
DB. Binding to the storage limit is honest and survives whatever schema we
eventually choose.

**null=True, blank=True (not empty string):** the API returns `photo: null`, not
`""`. JSON `null` conveys "no photo" more precisely, and the frontend checks
`photo === null` cleanly. Django's "no null on string fields" convention guards
against NULL-vs-"" ambiguity — removed here by simply never writing `""`.

**Optionality:** optional; the specialist and salon owner decide together, most
will have one. Creating a specialist is never blocked by a missing photo.

**Placeholder is a frontend concern:** absence of a photo is rendered by Next.js
as a grey-silhouette avatar (social-media style). The backend returns `null`
honestly and never invents a default-image URL — otherwise it would know about
the visual, and changing the silhouette would mean a backend change instead of
CSS. Same principle as local-time and formatting: backend returns the fact,
frontend renders the look.

**Field added now, not hardcoded in the serializer:** the field goes into the
model as a small migration (first step of this sub-step), rather than serving a
constant `null` from the serializer. A `null` constant would make the serializer
expose a field the model lacks — the source of truth would be a hardcode, not
the schema — and it would be two serializer edits (add constant now, replace
with real field at upload stage) instead of one cheap `ADD COLUMN NULL`
migration. The response shape is identical either way; there is no client-side
gain to justify the desync.

**Real upload deferred:** the model field and response shape are settled now,
but actual file upload (object-storage wiring, presigned URLs, upload endpoint)
is NOT built at Stage 6 — it's infrastructure, not part of the availability
engine, and belongs to a later dedicated stage. At Stage 6 every specialist
returns `photo: null`; the second endpoint serialises it as-is.

### Stage 6.K decisions (specialist-availability endpoint — second lookup, time → specialists)

Decided 2026-08-16, before implementation. No separate design proposal
preceded this section — these decisions came directly out of a planning pass
over § Stage 6 decisions, § Stage 6.G decisions, § Stage 6.H decisions, §
Stage 6.I decisions above, and are recorded here as agreed, not resolved
against a prior written proposal.

- **One `GET` endpoint, `/api/v1/salons/<slug>/availability/specialists/`, the
  second of the two availability endpoints, under the same `/availability/`
  path as the time-grid endpoint (§ Stage 6.I decisions)** — the two are kept
  together because they are two halves of one booking flow, not two
  unrelated resources. `permission_classes = [AllowAny]`, set explicitly on
  the class, same reasoning as § Stage 6.I decisions: `DEFAULT_PERMISSION_CLASSES`
  is `IsAuthenticated` globally, so a client browsing before registering
  would be silently blocked without this override.
- **Purpose: step 2 of the "any specialist" flow.** The client already picked
  a time from the grid (§ Stage 6.I decisions' response); this endpoint
  answers *who* is free at that exact time, returning each qualifying
  specialist with the fields needed to render a chooser card. This is the
  second half of the reversal decided in § Stage 6 decisions ("Reversed
  2026-08-15"): time first, specialist second.
- **Query params: `service` (required, int) and `datetime` (required, ISO
  8601 with offset, e.g. `2026-08-20T14:00:00+03:00`) — the exact string the
  first endpoint returned, echoed back unchanged by the client.** Validated
  via a plain `serializers.Serializer` over `request.query_params`, not a
  `ModelSerializer` — there is no instance to serialize, same reasoning as §
  Stage 6.I decisions. `service` is a `PrimaryKeyRelatedField` rebound in
  `__init__` to the tenant-scoped queryset (`Service.objects.all()`), the
  same pattern as `ServiceSerializer.category_id` and § Stage 6.I decisions'
  own `service`/`specialist` fields. `datetime` is a plain DRF
  `DateTimeField`, which parses ISO-with-offset out of the box — no custom
  parsing needed.
- **Datetime contract: the client sends back the local-time ISO string it
  received, offset included; the view converts to UTC internally to match
  the mapping's keys (which are UTC).** "Echo back what you received" is the
  simplest, most error-resistant contract available — the client never
  computes timezones itself. The explicit offset also disambiguates the
  moment on a DST-transition day, where a bare local time can be ambiguous
  or non-existent (§ Open questions, "Local-time presentation across a DST
  transition"), and a client bug that sends the wrong salon's offset
  surfaces as an empty result rather than silently matching the wrong slot.
- **A naive `datetime` (no UTC offset) is rejected with 400, not silently
  defaulted.** DRF's `DateTimeField` under `USE_TZ = True` doesn't reject a
  naive input by default — it silently makes it aware using the project's
  default timezone, which would reintroduce exactly the ambiguity the
  offset requirement above exists to remove. Enforced via
  `scheduling/serializers.py`'s `_OffsetRequiredDateTimeField`, a
  `DateTimeField` subclass overriding `enforce_timezone` — the DRF hook
  that receives the parsed value before any such default is applied — to
  raise `ValidationError` when `timezone.is_aware(value)` is false, before
  delegating to the normal behavior otherwise.
- **How one `datetime` maps onto the range-based engine:** the endpoint
  takes a single moment, but `compute_multi_specialist_availability` (§
  Stage 6.H decisions) works over a date range. The view derives the date
  from the submitted `datetime`, in the salon's timezone, and calls the
  engine with `date_from == date_to ==` that date — computing the one-day
  mapping — then selects the mapping value for the exact submitted moment,
  compared in UTC. The contract stays simple on the outside; the range
  mechanics stay internal.
- **Always recomputes: no cached result from the first endpoint is reused.**
  There is no server-stored state between the two calls — they are two
  separate HTTP requests, each computing fresh from the current state of
  `Appointment`/`TimeOff`/`WorkingHours`. Freshness is chosen over
  cross-step consistency, the same trade-off § Stage 6 decisions ("Reversed
  2026-08-15") already made for the two-step flow as a whole, and the same
  "always re-computes... never reuses a stored computation" rule § Stage 6.H
  decisions restates for the first endpoint.
- **Response shape: `{"specialists": [{"photo": ..., "name": ..., "bio":
  ...}, ...]}`, not a bare top-level array.** Consistent with the time-grid
  endpoint's `{"available_times": [...]}` (§ Stage 6.I decisions) and with
  DRF's pagination convention; an object leaves room for metadata later
  without a breaking shape change. Each specialist serialises exactly three
  fields: `photo` (the S3 object key, `null` for now — no upload flow yet, §
  Stage 6.J), `name`, `bio`. Order follows the mapping's specialist order,
  which is `Specialist.Meta.ordering`, `["name", "id"]`.
- **Experience/seniority is not a field here.** It lives in free-text `bio`
  for now, not a structured field — see § Open questions, "Specialist
  experience/seniority as a structured field," for the deferred decision and
  why.
- **An empty result is valid, not an error.** If the chosen time was taken
  between the two steps (a booking race), the mapping has no entry for that
  moment, and the endpoint returns `{"specialists": []}` with HTTP 200, not
  a 4xx/5xx. The human-facing "this time was just taken, pick another" copy
  is the frontend's job; the backend returns `[]` as plain data, the same
  "genuinely nothing available" shape § Stage 6.H decisions already
  distinguishes from an error.
- **Error contract is inherited from § Stage 6.I decisions, entirely through
  `core/exceptions.py`'s handler, no view-level `try`/`except`:**
  - Missing or malformed `service`/`datetime` → 400 (ordinary DRF field
    validation).
  - Unknown or cross-tenant `service` id → 400, not 404 — the id is a query
    *parameter*, not the URL's own addressed resource, and
    `TenantScopedManager` makes a cross-tenant id indistinguishable from a
    nonexistent one at the query level, so both surface as the same
    ordinary "does not exist" 400, per CLAUDE.md's `category_id` precedent
    (also § Stage 6.I decisions).
  - `InvalidDateRangeError` cannot arise here: `date_from == date_to` by
    construction, so the condition it guards against never occurs.

## Stage 7 decisions (booking core)

- **Sweep-vs-webhook race on `PENDING_PAYMENT`.** A `PENDING_PAYMENT`
  appointment can be acted on near-simultaneously by two writers: the expiry
  sweep (`docs/ARCHITECTURE.md` § 12) moving it `PENDING_PAYMENT → EXPIRED`
  once `hold_expires_at` passes, and the payment webhook moving it
  `PENDING_PAYMENT → CONFIRMED` (§ ARCHITECTURE state machines).
  `PENDING_PAYMENT` at read time is not proof the customer didn't pay — it
  only means the webhook hasn't arrived yet (payment and the news of payment
  are separated by network flight time). Decided, as four transition rules:
  1. Both writers take a row lock (`select_for_update`) on the appointment
     and re-check the row's current status under the lock before applying
     any transition — a transition is never forced from a status the row is
     no longer in.
  2. A sweep that finds the row already `CONFIRMED` does nothing.
  3. A webhook that finds the row already `EXPIRED` does **not** resurrect
     the slot (resurrecting could collide with a slot another customer
     booked in the gap, reintroducing the double-booking § 7 prevents) —
     instead it initiates a deposit refund and the customer is notified.
  4. Both transitions are idempotent (a redelivered event is a no-op, not an
     error), behind the same `ProcessedWebhookEvent` guard as § 8.

  Stage split: the policy above is decided now, but the refund itself is
  Stage 8 (payments) machinery — Stage 7 has no payment machinery to execute
  it. Stage 7 must only leave room: the `EXPIRED` transition must not assume
  payment was absent, and the webhook handler must be able to detect
  "arrived late, appointment already EXPIRED" and not force `CONFIRMED`; the
  refund call itself is wired in at Stage 8.

### Stage 7.C decisions (appointment-creation core)

Decided 2026-08-17, following a design-analysis proposal (read against
`docs/ARCHITECTURE.md` § 2, § 7, § 14, the State machines section, and
`docs/DECISIONS.md` § Stage 6.F/6.D decisions) that was reviewed and
approved before any code was written, per CLAUDE.md's stage workflow.
Recorded here as agreed.

- **Scope: the core `create_appointment` service function only.** It takes
  an already-existing `Customer` — `Customer` get-or-create and
  `GuestAccessToken` issuance are Stage 7.C-bis, which wraps this function
  and that logic in one shared `transaction.atomic()`. The `POST` endpoint
  is Stage 7.D. This sub-step designs and implements none of that.
- **Signature: `create_appointment(*, salon, specialist, service, customer,
  start_datetime, now) -> Appointment`.** `now` is an explicit parameter
  with no default, consistent with § Stage 6.F decisions ("`now` is an
  explicit parameter, with no default and no `None` fallback"): the system
  clock is I/O, and a default would let a test silently omit it and go
  green for the wrong reason. Return type is the created `Appointment`;
  failure is communicated by a raised `DomainError`, the same contract as
  every other service function in this codebase.
- **Fields written at creation.** `status = PENDING_PAYMENT`;
  `hold_expires_at = now + SLOT_HOLD_DURATION` (the § Stage 7.B constant's
  first real use); `end_datetime = start_datetime +
  service.duration_minutes`; `blocked_until = end_datetime +
  service.buffer_minutes`; `service_price_at_booking = service.price` and
  `deposit_percentage_at_booking = salon.deposit_percentage`, both read
  from the live `Service`/`Salon` rows *at creation time* and then frozen —
  the snapshot exists precisely so a later price or deposit-percentage
  change never rewrites what an already-made booking owes
  (`docs/ARCHITECTURE.md` § 2, § 8). `salon` is passed explicitly to
  `.create()`: the tenant-scoped manager filters reads but never injects
  `salon` on write (the Stage 6 smoke-test finding).
- **Slot-validity check — the key decision.** The service re-validates that
  `start_datetime` is actually an offered slot by calling
  `compute_candidate_start_times` for the given specialist, service, and
  date, and checking `start_datetime` is among the returned candidates; if
  not, it raises `SlotNotOfferedError`. The service layer is the last line
  of truth — callable from views, Celery tasks, and the AI assistant alike
  — so trusting the caller would leave lead-time, max-advance,
  `WorkingHours`, `is_active`, and `SpecialistService` qualification
  unenforced for any non-view caller. One check covers all of those,
  because they already live inside the engine; the service does not
  re-implement any of those filters itself. This runs **before**
  `transaction.atomic()`: it is a read with no need for transactional
  isolation — validity only needs to be honest as of the request, it is
  only the slot *claim* below that needs atomicity.
- **Double-booking prevention, two layers, inside one
  `transaction.atomic()`, in this order: application-level re-check, then
  insert.** The re-check is a narrow `Appointment.objects.filter` on the
  specialist, overlapping `(start_datetime, blocked_until)`, filtered to
  `ACTIVE_APPOINTMENT_STATUSES` — mirroring the exclusion constraint's own
  condition, **not** re-running the scheduling engine (`docs/ARCHITECTURE.md`
  § 7, layer 1). A hit raises `SlotUnavailableError` before any insert is
  attempted. The exclusion constraint remains the final guard (§ 7, layer
  2): a resulting `IntegrityError` whose `__cause__` is a psycopg
  `ExclusionViolation` is caught narrowly — the same pattern
  `core/exceptions.py` already uses for `UniqueViolation` — and re-raised as
  the same `SlotUnavailableError`, so the caller can't tell which of the two
  layers actually fired.
- **Two distinct domain errors, and why they must stay separate.**
  `SlotNotOfferedError` (`code=SLOT_NOT_OFFERED`, HTTP 400) means the slot
  was never an offered candidate at all — outside the lead-time/advance
  window, wrong specialist, wrong working hours, etc. — which in a healthy
  frontend flow only fires on a frontend bug or a tampered request.
  `SlotUnavailableError` (`code=SLOT_NO_LONGER_AVAILABLE`, HTTP 409, § 7)
  means the slot was valid and offered but someone else took it first — the
  ordinary booking race. The frontend must branch differently on each
  (reload the availability grid vs. treat it as a bad request), so
  conflating them would mislead it. `SlotNotOfferedError` carries **no**
  per-filter reason in its details — deriving *which* filter rejected the
  slot would mean re-implementing each filter outside the engine, the same
  reasoning § Stage 6.D decisions' "blocker provenance" question already
  decided against for a different endpoint. Instead the service logs the
  event context server-side (`start_datetime`, specialist, candidate count)
  for debugging frontend bugs, without exposing the reason to the client.
- **Cross-tenant safety: the composite tenant FKs are the DB guarantee, not
  a friendly application-level check.** A genuine cross-salon mismatch
  (e.g. a `specialist` from a different salon than `salon`) surfaces as an
  unhandled `IntegrityError` → the generic 500 path, deliberately not given
  a handler in 7.C — a real caller resolving every object from one
  tenant-scoped request cannot produce this without a bug elsewhere, the
  same stance already recorded for `compute_candidate_start_times`'s
  unchecked `salon` argument (§ Open questions). This is pinned by a test,
  not "fixed."
- **New files this sub-step will need** (forthcoming — not created by this
  documentation-only commit): `booking/services.py`, holding
  `create_appointment` and its private overlap-check helper; and two new
  `DomainError` subclasses in `core/exceptions.py` —
  `SlotNotOfferedError` (400) and `SlotUnavailableError` (409), the latter
  the exact name `docs/ARCHITECTURE.md` § 14 already documents as planned.
- **Two implementation-detail names are pinned by the red-phase test suite
  on purpose, the same spirit as a deliberate `# noqa`.** The slot-validity
  check (above) and the double-booking re-check are redundant for the
  ordinary, non-racing case — both exclude the same occupied intervals, just
  at different times relative to `transaction.atomic()` — so a test that
  wants to exercise the double-booking layers in isolation has to defeat the
  slot-validity check first via `monkeypatch`, and (for the DB-layer test)
  the application-level re-check as well. That only works if both are
  reachable as plain module-global names on `booking.services`, not
  namespaced through an imported module object. Two names are therefore
  fixed by that test suite, not free-to-rename internals:
  `compute_candidate_start_times` must be imported into `booking/services.py`
  as a bare name (`from scheduling.services import
  compute_candidate_start_times`), and the private re-check helper must be
  named `_has_overlapping_active_appointment`. Renaming either, or importing
  the engine call via a namespaced module reference instead, silently breaks
  the double-booking tests' `monkeypatch.setattr` calls without raising an
  error of its own — the tests would start exercising the wrong code path
  instead of failing loudly. A rename of either name must update
  `tests/test_booking_create_appointment.py` in the same change.

### Stage 7.C-bis decisions (guest booking orchestrator)

Decided 2026-08-17, following a design-analysis proposal (read against
`docs/ARCHITECTURE.md` § 2, § 3, and `docs/DECISIONS.md` § Identity, §
Stage 3 sub-step 3 decisions, § Stage 7.C decisions) that was reviewed and
approved before any code was written, per CLAUDE.md's stage workflow.
Recorded here as agreed.

- **Scope: the orchestrator `create_guest_appointment`, guest path only.**
  Lives in `booking/services.py` — the end product is a booking, and
  `booking` already depends on `accounts` (via `Customer`), never the
  reverse, so the orchestrator calls into `accounts` for the `Customer`
  step rather than the other way around. It wraps guest `Customer`
  get-or-create, the § Stage 7.C core `create_appointment`, and
  `GuestAccessToken` issuance in one `transaction.atomic()`. A
  registered-user path (`Customer` already linked to a `User`, no email
  get-or-create, no guest token) is a separate later step, not designed or
  implemented here.
- **Signature: `create_guest_appointment(*, salon, specialist, service,
  start_datetime, now, customer_name, customer_email, customer_phone) ->
  tuple[Appointment, str]`.** Returns `(Appointment, raw_token)`. The raw
  token is returned because the guest has no login and the only other
  place it will ever surface is a future Stage 9 confirmation email —
  whether the § Stage 7.D `POST` endpoint puts it in the response body in
  the meantime, or holds it back until Stage 9 exists, is a 7.D decision,
  deliberately **not** decided here.
- **Calls, in order, inside one outer `transaction.atomic()`:**
  `accounts.services.get_or_create_guest_customer(...)` (new, this
  sub-step) → `create_appointment(...)` (existing § Stage 7.C core, same
  module) → `booking.guest_tokens.issue_guest_token(appointment)` (already
  exists — built in Stage 3 anticipating exactly this call).
- **Transaction: the outer `atomic()` is the entire source of the
  "no orphaned `Customer` / no appointment without a token" guarantee.**
  `ATOMIC_REQUESTS` is not set anywhere in settings, so there is no
  request-level atomicity to lean on — this function's own `atomic()`
  block is load-bearing, not a redundant layer on top of something the
  framework already provides. `create_appointment`'s own internal
  `atomic()` (§ Stage 7.C decisions, "Double-booking prevention") nests as
  a savepoint inside this outer one; Django's `atomic()` nests
  arbitrarily, and § Stage 7.C's own transaction-atomicity test already
  proved a nested `IntegrityError` doesn't poison the surrounding
  transaction one level up from pytest-django's test-wrapping transaction
  — nesting a second level here adds no new risk.
- **Customer get-or-create semantics, decided: overwrite name/phone on
  every booking, unconditionally (option A).** On a returning guest (email
  already has a `Customer` row in this salon), the newly-supplied
  `customer_name`/`customer_phone` overwrite the stored row's values every
  time, not just on first booking. This is the most direct reading of
  `docs/ARCHITECTURE.md` § 3 / `docs/DECISIONS.md` § Identity ("a returning
  guest updates their existing row") and the simplest rule to state — no
  per-field conditional logic. **The risk is real and is recorded, not
  hidden**: a typo, or a guest booking on behalf of someone else under
  their own email, silently overwrites previously-correct name/phone data.
  Chosen over never-overwrite (option B) because A has a self-correction
  path — a wrong value written today is corrected automatically by the
  guest's next booking — whereas B's stale data never refreshes, and a
  login-less guest has no other way to fix it until account features exist
  (Stage 13+). Rejected fill-only-blank-fields (option C) as introducing a
  "first write wins per field" rule that isn't stated anywhere in the docs
  and would need its own justification.
- **Get-or-create must use Django's ORM `get_or_create()`, not a
  hand-written check-then-create.** The `(salon, email)` unique constraint
  (`customer_salon_email_uniq`) plus `get_or_create()`'s own internal
  savepoint-and-retry-on-`IntegrityError` behavior closes the
  concurrent-same-email race at the DB level with no extra code from this
  sub-step. That race is pinned by Django's own implementation plus the DB
  constraint, not by a synthetic thread-based test — same category as the
  sweep-vs-webhook race (§ Stage 7 decisions) and the exclusion-constraint
  race already recorded in this file, real races a single-transaction unit
  test can't exercise without actual concurrency.
- **`GuestAccessToken` issuance needs no new machinery.**
  `booking/guest_tokens.py::issue_guest_token` already handles signing
  (`django.core.signing`), the SHA-256 `token_hash`, `expires_at =
  appointment.end_datetime + GUEST_TOKEN_VALIDITY` (30 days), and passes
  `salon` explicitly. The orchestrator only calls it with the just-created
  `Appointment` and propagates both of its return values.
- **New code this sub-step will need** (forthcoming — not created by this
  documentation-only commit): `accounts/services.py`'s
  `get_or_create_guest_customer(*, salon, name, email, phone) -> Customer`;
  `booking/services.py`'s `create_guest_appointment`. No new `DomainError`
  subclasses are anticipated — every failure path funnels through
  `create_appointment`'s existing `SlotNotOfferedError`/
  `SlotUnavailableError`, or an unexpected `IntegrityError`.

### Stage 7.D decisions (guest booking POST endpoint)

Decided 2026-08-18, agreed directly in discussion (no separate written
design proposal for this sub-step). Recorded here as agreed.

- **Raw guest token placement in the 201 response body — TEMPORARY.**
  Closes the deferral recorded in § Stage 7.C-bis decisions ("whether the §
  Stage 7.D `POST` endpoint puts it in the response body in the meantime, or
  holds it back until Stage 9 exists, is a 7.D decision, deliberately **not**
  decided here"). Resolved: the endpoint returns the raw guest token in the
  201 response body, **for now**. This is explicitly temporary and will be
  removed once Stage 9 (email) lands, at which point the token moves into an
  email link instead. Reasoning: email does not physically exist until Stage
  9, so an email-only rule today would make the token a dead artifact —
  generated, stored, but delivered through no channel, leaving the guest
  unable to either view or cancel the booking they just made. There are no
  live users yet, so returning the token in the body now carries no leak
  risk into anyone's browser history or logs, and it is what makes manual
  end-to-end testing of the create → view → cancel flow possible before
  email exists. This is a sequence-in-time decision (response body now,
  email later) — not an email-vs-response-body tradeoff. When Stage 9
  reverses this, the reversal must be recorded as its own dated entry that
  preserves this original reasoning, not a backdated edit to this entry.
- **Error mapping: no `try`/`except` in the view.** `SlotNotOfferedError`
  (400, `SLOT_NOT_OFFERED`) and `SlotUnavailableError` (409,
  `SLOT_NO_LONGER_AVAILABLE`) are both already `DomainError` subclasses
  carrying their own `status_code`, raised by the § Stage 7.C core the
  orchestrator wraps. The existing `core/exceptions.exception_handler`
  translates any raised `DomainError` into its structured response, the same
  mechanism already used for every other domain error in this codebase — the
  view calls the orchestrator directly and lets exceptions propagate.
- **201 response envelope: the created appointment nests under an
  `"appointment"` key, with the raw guest token as a sibling top-level
  `"token"` key** — `{"appointment": {<serialized appointment>}, "token":
  "<raw token>"}`. This is the first endpoint in the codebase returning a
  created resource plus an out-of-band value in the same body: the existing
  `ListCreateAPIView` create endpoints (`catalog/views.py`,
  `specialists/views.py`) return the bare serialized object with no sibling
  keys, and no other endpoint combines a resource with a token in its
  response — so this sets the convention here rather than inheriting one.
  Nesting keeps the two things honestly separated: `"appointment"` is the
  resource, `"token"` is a temporary access credential, not a property of
  the appointment — a flat shape would present the token as if it were an
  appointment field. It also makes the Stage 9 reversal (§ Stage 7.D
  decisions above, "Raw guest token placement") clean: deleting the
  `"token"` key at that point leaves the `"appointment"` object's own shape
  untouched, where a flat shape would leave a gap among the resource's
  fields. Consistent with the project's existing preference for a
  top-level-object body over a bare payload (e.g. `{"available_times":
  [...]}` on the availability endpoints, § Stage 6.I decisions) — the body
  names what each part is and leaves room for metadata. This entry fixes
  only the envelope shape; which fields the appointment serializer exposes
  is undecided here and belongs to the serializer step.

### Stage 7.E decisions (cancellation service layer)

Decided 2026-08-18 in discussion, before implementation, per CLAUDE.md's
design-first workflow — read against the current on-disk state of
`booking/services.py`, `booking/views.py`, `booking/models.py`, and
`core/exceptions.py`, verified fresh rather than from memory. Recorded here
as agreed.

- **A general, role-agnostic cancellation service, `cancel_appointment`, is
  introduced.** Signature: `cancel_appointment(*, appointment_id, salon,
  cancelled_by, now, reason="") -> Appointment`. Keyword-only, `now`
  explicit with no default — matching `create_appointment`'s (§ Stage 7.C
  decisions) own convention. It serves all cancel paths (guest, logged-in
  customer, staff); the caller passes **who** is cancelling via
  `cancelled_by`, one of the existing `CancelledBy` choices. This step
  rewrites the existing inline `GuestAppointmentCancelView.post` logic as a
  thin call to this service. Customer/staff views are **not** written in
  this step — there is no UI to attach them to yet — the service is simply
  ready for them when those stages land.
- **The service fetches the row itself, under the lock — it takes
  `appointment_id` and `salon`, not a pre-fetched `Appointment`.**
  `select_for_update()` takes the lock at read time; a row fetched earlier
  by the view and handed in would be a stale, unlocked snapshot, and
  passing that object into the service does not retroactively lock the row
  it came from. So `Appointment.objects.select_for_update().get(salon=salon,
  pk=appointment_id)` is the first statement inside the service's own
  `atomic()` block. Consequence, accepted deliberately: the guest row is
  read twice per request — once by the view for token authorization (via
  `_GuestTokenAppointmentMixin.get_object()`, unlocked, § Stage 3 sub-step 3
  decisions), once by the service under the lock for the actual mutation.
  Redundant but correct; threading the lock through the authorization fetch
  would entangle authz with the transactional mutation, and the
  authorization fetch has nothing to protect against a concurrent writer —
  only the mutation does.
- **The critical section, inside one `atomic()`:** fetch under lock →
  recheck `status` is still in `ACTIVE_APPOINTMENT_STATUSES` **under the
  lock** → if not, raise `InvalidStateTransitionError` → else set
  `status=CANCELLED`, `cancelled_at=now`, `cancelled_by`,
  `cancellation_reason=reason` → `save(update_fields=[...])`. The recheck
  must happen under the lock, not before it: without the lock serializing
  the two writers, a window exists between an early unlocked read and the
  write where the not-yet-built § 7.F sweep could flip `PENDING_PAYMENT →
  EXPIRED` on the same row, and cancellation would then blindly overwrite
  `EXPIRED` back to `CANCELLED` — a lost-update race erasing a legitimate
  concurrent transition. This is rule 1 of the sweep-vs-webhook policy (§
  Stage 7 decisions: "both writers take a row lock … and re-check the
  row's current status under the lock before applying any transition")
  applied to a new writer pair — cancel vs. sweep — that the original rule
  didn't name but whose reasoning transfers directly.
- **Refund handling: approach B.** The service stores raw facts only
  (`cancelled_by`, `cancelled_at`, optional `reason`). It does **not**
  compute refund eligibility and does **not** add any refund field to
  `Appointment`. Stage 8 computes eligibility from `cancelled_by` plus
  timing, per § Business rules ("`Appointment.cancelled_by` is what refund
  eligibility is computed from; it is not a bare time comparison").
  Reasoning: raw facts don't go stale; a stored computed flag would
  duplicate the source of truth and freeze under today's 24-hour cutoff
  rule even if that rule is later changed — the facts remain correct
  inputs to whatever rule is current at refund-processing time, a computed
  flag would not.
- **`cancellation_reason` is optional for every role, including staff —
  never required anywhere.** Staff/owner know the reason personally from
  context; forcing entry on top of that is bureaucracy with no consumer
  recorded anywhere in the docs.
- **The guest-token-specific update
  (`GuestAccessToken.cancelled_via_token_at`) stays in the guest view, not
  the service.** The service is role-agnostic; staff and logged-in
  customers have no guest token to update. The guest view already has
  `request.guest_access_token` on hand (set by `HasValidGuestToken`) and
  performs this one-line update itself, immediately after calling the
  service, using the same `now` passed to both. This mirrors how
  `create_guest_appointment` (§ Stage 7.C-bis decisions) keeps guest-token
  issuance in the guest-specific orchestrator rather than the shared core.
- **Error on cancelling a non-active appointment: a bare
  `InvalidStateTransitionError`, with the blocking status carried in
  structured `details`** — `InvalidStateTransitionError(details={
  "current_status": appointment.status})`, message left at the class
  `default_message` ("This action isn't valid for the current state.").
  Verified before deciding this: `DomainError.__init__` (`core/
  exceptions.py`) already accepts a keyword-only `details: dict | None`
  parameter, and `exception_handler` already threads it straight into the
  response envelope's `error.details` — no exception-machinery change
  needed. `specialists/services.py::soft_delete_specialist`'s
  `SpecialistHasFutureAppointmentsError(details={...})` is the one existing
  precedent for exactly this shape (a generic message plus a
  machine-readable fact in `details`), so this follows established
  practice rather than introducing a new one. Chosen over the current
  inline view's f-string message
  (`f"Cannot cancel an appointment with status '{appointment.status}'."`):
  a bare raise matches `create_appointment`'s own style (§ Stage 7.C
  decisions raises `SlotNotOfferedError()`/`SlotUnavailableError()` with no
  message), and `details` surfaces the same information machine-readably —
  more useful to a frontend than parsing a sentence, and consistent with
  the one other place in this codebase that already does this. **Repeat
  cancellation of an already-`CANCELLED` appointment is an error, not a
  silent no-op** — a second cancel could arrive from a different role at a
  different moment than the first, and silently swallowing it would hide
  something worth surfacing rather than treating it as an unremarkable
  repeat request.
- **`Appointment.DoesNotExist` inside the locked fetch propagates as a bare
  exception from the service; the view translates it to 404 or a
  token-mismatch response, not the service.** The service doesn't know
  HTTP and shouldn't decide what a missing row means to an HTTP caller. In
  the guest path this only fires on a genuine TOCTOU (the row deleted
  between the view's own earlier resolving read and the service's locked
  one) — the ordinary "wrong id" case is already caught earlier, by
  `_GuestTokenAppointmentMixin`'s own resolution before the service is ever
  called.
- **Boundary — what 7.E does not touch:** no refund computation, no
  `Payment` model or provider call, no notification dispatch, no § 7.F
  sweep task itself — 7.E only establishes the row-lock pattern the sweep
  will later need to coexist with on the same row, per the critical-section
  entry above. No shared lock helper is factored out with § 7.F yet — 7.E
  builds its own clear pattern here; whether it's worth abstracting is a §
  7.F decision, made by fact once that task exists, not speculatively now.

### Stage 7.F decisions (appointment-expiry sweep)

Decided 2026-08-18 in discussion, before implementation, per CLAUDE.md's
design-first workflow — read against the current on-disk state of
`booking/services.py`, `config/celery.py`, `config/settings/base.py`,
`accounts/tasks.py`, `core/tenancy.py`, and `booking/models.py`, verified
fresh rather than from memory. Recorded here as agreed.

- **A periodic Celery task expires overdue `PENDING_PAYMENT` appointments.**
  This is the first live Celery task in the project bound to tenant context
  at all, and the first periodic (Celery Beat) task of any kind —
  `accounts/tasks.py`'s three existing tasks are platform-wide User events
  fired by `.delay()` and bind no tenant context. It is also the first live
  application of the sweep-vs-webhook race policy (§ Stage 7 decisions).
- **Structure: a service plus a thin task, same split as 7.E.**
  `expire_overdue_appointments(*, salon, now) -> int` (row count expired, for
  logging) lives in `booking/services.py`, alongside `cancel_appointment`. A
  thin `@shared_task`, `expire_pending_payment_appointments`, lives in a new
  `booking/tasks.py`, mirroring `accounts/tasks.py`'s app-level placement.
  Keyword-only, `now` explicit with no default — the same convention as
  `create_appointment` and `cancel_appointment`.
- **The service's unit is one salon's batch, not one row by id — a
  deliberate asymmetry with `cancel_appointment`.** If the service instead
  took a single `appointment_id`, the "what counts as overdue" query would
  have to live in the task, putting a business rule in the orchestration
  layer — the same layering mistake the "business logic lives in a service
  layer, not views" rule already forbids elsewhere in this codebase. So the
  service owns the overdue query itself; the task's only job is the
  per-salon loop and tenant binding around it.
- **Per-row locking, not one lock over the whole batch.** The service first
  runs an *unlocked* query for candidate ids —
  `Appointment.objects.filter(salon=salon, status=PENDING_PAYMENT,
  hold_expires_at__lte=now)` — then, for **each** id, opens its own
  `transaction.atomic()` and does `Appointment.objects.select_for_update()
  .get(salon=salon, pk=id)`, rechecks `status` under that lock, and only
  then transitions and saves. Reasoning: locking all N candidate rows for
  the duration of a single transaction would (a) widen the blast radius —
  every one of those rows stays locked for as long as the whole batch takes,
  contending with a webhook or a cancel request on any of them the entire
  time; and (b) mean one unexpected failure partway through the batch rolls
  back the *entire* batch's `atomic()`, undoing expirations already decided
  earlier in the loop. Independent per-row transactions mirror
  `cancel_appointment`'s own atomicity granularity instead.
- **A row whose recheck finds it's no longer `PENDING_PAYMENT` (already
  `CONFIRMED` or already `EXPIRED`) is silently skipped — no raise.** This
  is the deliberate contrast with `cancel_appointment`, which raises
  `InvalidStateTransitionError` on the same kind of recheck failure: a
  sweep is unattended background cleanup with no user to hand an error to.
  This silent-skip-on-recheck is also what makes the task idempotent (rule
  4 of the sweep-vs-webhook policy, § Stage 7 decisions) as a *consequence*
  of the lock-and-recheck mechanism already required by rule 1, not a
  separate mechanism bolted on — a redelivered or overlapping run just
  re-skips rows that already transitioned.
- **The task loops over `Salon.objects.all()` and binds
  `tenant_context(salon.id)` per salon, calling the service inside that
  binding.** `Salon` is the tenant root, not itself tenant-scoped, so this
  is its plain default manager — matching `ARCHITECTURE.md` § 5's own
  description of the sweep as the example of a cross-tenant, loop-over-
  `Salon.objects.all()` task. `now = timezone.now()` is read exactly once
  in the task, before the loop starts, so every salon processed in one run
  is judged against the same instant rather than a slightly later one for
  salons later in the iteration order.
- **Three safety requirements on the cross-tenant loop**, spelled out
  explicitly because a loop over every tenant in one process is exactly
  where a tenant-isolation bug would leak data between salons:
  1. `tenant_context` is a real context manager whose `finally` resets the
     context variable on every iteration regardless of outcome, so salon
     N's context cannot bleed into salon N+1's.
  2. Each salon's processing (the `tenant_context` binding plus the service
     call) is wrapped in its own `try`/`except Exception`, logged via
     `logger.exception` with the salon id, then skipped — one failing salon
     must not abort the run for every other salon.
  3. Inside the loop, all reads go through `Appointment.objects` (the
     tenant-scoped manager) — never `unscoped_objects`. If `tenant_context`
     were ever left unbound by a future change, `objects` raises
     `TenantContextMissingError` (`core/tenancy.py`), which is just another
     exception the per-salon `try`/`except` catches and logs loudly — a
     crash, never a silent all-salons scan. This is `ARCHITECTURE.md` §
     5's "unscoped query is the hard path" guarantee holding under a
     background job specifically, not only under request/response.
  Deliberately **no** per-row `try`/`except` inside a salon's batch — only
  per-salon. An unexpected (not merely "already transitioned") error on one
  row propagates and aborts the rest of that salon's batch for this run,
  caught only at the per-salon boundary. Swallowing errors row-by-row would
  let a recurring bug hide behind endless silent skips forever; letting it
  propagate to the per-salon log is louder, and safe to do because the
  transition is idempotent — an unprocessed row is still `PENDING_PAYMENT`
  and is picked up again next run.
- **`beat_schedule` entry: every 60 seconds, the first entry in the
  currently-empty `beat_schedule` in `config/celery.py`.** The 15-minute
  hold (`SLOT_HOLD_DURATION`, § Business rules) is a payment-UX parameter
  tuned specifically for a 3-D-Secure challenge window — a sweep interval
  should stay a small fraction of it, not erode the number it was tuned to.
  At 60 seconds, the worst-case extra hold past `hold_expires_at` is about
  one minute, under 7% of the 15-minute hold. A 5-minute interval would
  risk up to 5 minutes of unnecessary extra hold — roughly a third of the
  hold duration — which would undercut the reasoning the 15-minute figure
  was chosen under in the first place.
- **The service returns `int` (a count), not a list of the expired rows or
  their ids.** Nothing downstream needs the rows themselves right now:
  there is no API surface for `EXPIRED` (terminal, no client-facing
  response shape to decide), and Stage 8's refund wiring will do its own
  lookup of affected rows at refund-processing time rather than receiving
  them synchronously from this sweep. Consistent with not returning data no
  caller asks for yet.
- **Naming: `expire_overdue_appointments` (service) vs.
  `expire_pending_payment_appointments` (task) — deliberately different
  names across the two layers**, so a log line or a stack trace makes it
  unambiguous which layer is talking.
- **Boundary — what 7.F does not touch:** no refund (an `EXPIRED`
  appointment's deposit refund is Stage 8, per § Stage 7 decisions' sweep-
  vs-webhook policy stage split), no `Payment` model or provider call, no
  notification dispatch. No shared lock helper is factored out with 7.E's
  `cancel_appointment`, despite both being row-lock-and-recheck patterns on
  the same model: the two are different enough — one row vs. many, raise vs.
  silent skip — that abstracting now would be premature; visible
  duplication of the one-line `select_for_update()` is the honest choice
  over a shared helper built to fit two call sites that don't actually
  share a shape yet.

## Stage 8 decisions (payments)

Decided 2026-08-19 in discussion, before implementation, per CLAUDE.md's
design-first workflow. Recorded here as agreed.

- **Currency lives on `Salon` as `Salon.currency`, and is frozen onto each
  `Payment` as `Payment.currency` at creation time** — copied from the salon
  and never mutated afterward, even if the salon later changes its currency.
  Same snapshot pattern as `Appointment.deposit_percentage_at_booking`: a
  later change to the live setting must never rewrite what an existing
  payment already committed to.
- **Format: ISO 4217 three-letter uppercase code** (e.g. `UAH`, `USD`,
  `EUR`), enforced by a validator (exactly three uppercase letters) —
  **not** a free-text field and **not** a hardcoded `TextChoices` of
  specific currencies; any valid ISO code is allowed. Free text produces
  inconsistent values (`грн` / `UAH` / `uah`) that Stripe would reject; a
  hardcoded enum would block adding currencies later.
- **The platform operates each `Payment` in a single currency (the salon's)
  only.** Multi-currency at checkout — a client paying with a card in a
  different currency — is handled entirely by the payment provider/bank via
  conversion; the platform never computes, stores, or displays an exchange
  rate or a converted amount. `Payment.amount` and `Payment.currency` always
  reflect the salon's currency only.
- **Deposit rounding: `deposit_amount = round_half_up
  (service_price_at_booking × deposit_percentage_at_booking)`, to a whole
  currency unit (0 decimal places in value).** Read from the booking-time
  snapshots on `Appointment`, never from live `Service.price` or
  `Salon.deposit_percentage`, per § 8's existing deposit-calculation rule.
  `Payment.amount` remains `DecimalField(decimal_places=2)` physically, but
  the stored value is always whole (e.g. `83.00`) — rounding is a write-time
  rule, not a field constraint.
- **The in-person remainder (80%) is not stored.** It is derived when needed
  as `service_price_at_booking − deposit_amount`. Because the remainder is
  derived from the full price and the already-rounded deposit, rounding the
  deposit up is automatically consistent — the two always sum to the full
  price, with no separate reconciliation needed.
- **Stuck refunds: a background Celery sweep flags aged `REFUND_PENDING`
  rows for human review — it does not re-contact the provider or attempt an
  automatic retry.** Automatic retry risks a double refund (irreversible
  loss for the salon; a dishonest client won't report it), and the mock
  provider cannot answer a re-query honestly. The risk is asymmetric: a
  delayed refund is recoverable, a double refund is not. Automatic retry may
  be revisited once the real Stripe adapter — which supports idempotent
  refund re-queries — exists.
- **The stuck threshold is a platform constant (default 72 hours), not a
  per-salon field.** Refund settlement time is a property of the payment
  system, uniform across salons, not a per-salon business decision.
- **The stuck state is a boolean flag on `Payment` (e.g.
  `flagged_for_review`) plus a warning-level log — not a new status in the
  payment state machine.** `REFUND_PENDING` already describes the state;
  "stuck" is an alert marker on top of it, not a new machine state.
- **Notification to a human (email/admin alert) is deferred to Stage 9.**
  Stage 8 produces only the raw signal — the flag and the log. Stage 9
  turns the flag into a notification, alongside the existing
  `PAYMENT_SUCCEEDED` / `PAYMENT_FAILED` trigger types.
- **`PaymentProvider` is an interface with exactly two methods —
  `start_payment` and `refund` — the two actions the platform initiates
  toward the provider.** A webhook is not a method on this interface: it is
  the provider calling *us* back, the opposite direction, handled by a
  separate inbound endpoint rather than folded into this contract.
- **`start_payment` accepts the amount, the currency, and our own reference
  (the `Appointment` id — revised under Stage 8.C, see § Stage 8.C
  decisions; originally planned as the `Payment` id)** so the later webhook
  can locate which `Payment` to update. It returns an intent object (a
  typed dataclass), not a success/failure result — at call time no money
  has moved yet. The returned object carries the provider's
  `provider_reference_id`, stored on
  `Payment.provider_reference_id` (the field already exists from Stage 2).
  The actual outcome (`PENDING → SUCCEEDED`) arrives asynchronously via
  webhook.
- **`refund` accepts the original payment's `provider_reference_id` (plus
  our reference for webhook correlation) and returns an intent object (a
  typed dataclass) — it does not accept an amount.** A refund reverses the
  specific existing transaction identified by its provider id; it does not
  create a new money movement of an arbitrary sum. Business rules always
  refund the full deposit or nothing (no partial refunds), so the amount is
  implied by the original payment — taking an amount argument would open a
  class of errors (refunding the wrong sum) for no benefit. The outcome
  (`REFUND_PENDING → REFUNDED`) arrives asynchronously via webhook.
- **The return type of both methods is a typed dataclass, not a dict**, so
  the contract is statically checkable: mypy guarantees the mock and the
  real adapter return the same shape, and a mistyped field is caught at
  check time rather than failing at runtime.
- **The mock provider (built first, before the real Stripe adapter) must
  never touch the network.** Its `start_payment` returns a fake
  `provider_reference_id` and moves no money; it does not synchronously
  simulate success — `Payment` stays `PENDING` after the call. To drive it
  to `SUCCEEDED`, a test sends a fake webhook to the inbound endpoint,
  exactly as the real provider would. This keeps the mock honest about the
  asynchronous shape of the real flow: a mock that self-completes would
  produce green tests for a path that does not exist in production and
  leave the webhook half of the flow untested.
- **Booking and payment are two separate requests, not one.** `POST
  /bookings/` creates the `Appointment` (a DB write, atomic, lock released
  fast). Starting the payment is a separate request. `start_payment` is a
  network call to the provider — slow, can hang or fail — and wrapping it
  inside the same DB transaction as the appointment write would hold a row
  lock and an open transaction open for the provider's latency, coupling
  database health to an external server. DB transactions must stay short;
  network calls must live outside them.
- **Payment endpoint: `POST guest/appointments/<id>/pay/`**, placed
  alongside `guest/appointments/<id>/cancel/`, using the same guest-token
  authorization (`HasValidGuestToken`, `_GuestTokenAppointmentMixin`).
  Paying is another action on one specific appointment authorized by the
  same token, exactly like cancel — a separate `/payments/` endpoint would
  have to re-invent which appointment is being paid and who is allowed to
  pay for it, when that authorization already exists in the
  guest-appointment group. Segment name is `pay`; method is POST, since it
  creates a `Payment` and triggers a state-changing action, not a
  GET/PUT/PATCH/DELETE.
- **The underlying service is role-agnostic, like `cancel_appointment`: one
  service, multiple thin views under different URLs over time.** Stage 8
  builds only the guest view; logged-in-customer and staff payment views are
  deferred until there is a UI to attach them to.
- **Response of `POST .../pay/` returns the provider-neutral envelope
  `{"payment": {...}, "provider_data": <provider-specific or null>}`**
  (revised under Stage 8.C — see § Stage 8.C decisions; superseded the
  originally-planned `client_secret` field). `provider_reference_id` is
  stored on `Payment.provider_reference_id` and is not returned to the
  client: it is the backend's key for correlating the webhook, and the
  client does nothing with it. Principle: return exactly what the client
  needs for its next action, nothing more.
- **Payment may only be initiated from `PENDING_PAYMENT`.** Any other state
  (`EXPIRED`, `CONFIRMED`, `CANCELLED`) raises `InvalidStateTransitionError`
  (409) with the current status in `details` (`current_status`), mirroring
  `cancel_appointment`. This is a single error class on the backend
  ("appointment not in a payable state"), while `details.current_status`
  lets the frontend render a distinct human message per state — presentation
  on the frontend, fact on the backend.
- **Idempotency: at most one live `PENDING` `Payment` per appointment.** A
  repeated `POST .../pay/` (double-click, network retry) must not create a
  second provider intent — it returns the existing payment's provider-neutral
  response (`{"payment": ..., "provider_data": ...}`, per § Stage 8.C
  decisions). Two live intents on one appointment risk a double charge if
  both complete (irreversible loss), and `Payment` is one-to-one with
  `Appointment`, so a
  second succeeded payment has nowhere to land — same asymmetric-risk logic
  as the double-refund decision above. A *different* appointment (e.g. the
  client books a second service) correctly gets its own new intent — the
  rule is keyed to the specific appointment, not "never create a new one."
- **The webhook is a top-level `POST /api/v1/webhooks/payments/`, outside
  the tenant `/salons/<slug>/` prefix.** The caller is the provider, not a
  salon-scoped client — the provider does not know salon slugs and posts to
  one fixed URL. The salon/tenant is resolved from the event itself
  (`provider_reference_id` → `Payment` → `salon`), never from the URL.
- **Authentication is by provider signature, not by guest token or
  session** — the caller is the provider, not our client. Every event is
  signature-verified (provider webhook signing secret) before any
  processing; an invalid signature is rejected. The mock cannot verify a
  real signature, but the verification step must exist in the code so the
  real Stripe adapter fills it in — without it the webhook URL is an open
  door: anyone could POST `payment_succeeded` and confirm a booking for
  free.
- **Event types handled: `payment_succeeded`, `payment_failed`,
  `refund_succeeded`.** All other event types are ignored, with a 200.
- **`payment_succeeded`: locate `Payment` by `provider_reference_id`, take a
  row lock, and re-read status under the lock, then in one transaction
  transition `Payment PENDING → SUCCEEDED` and `Appointment
  PENDING_PAYMENT → CONFIRMED`** (the two coupled machines per
  `ARCHITECTURE.md` § 8). Re-reading status under the lock, not before it,
  is sweep-vs-webhook rule 1 — the same pattern as Stage 7.E/7.F, applied to
  a new writer pair (webhook vs. expiry sweep).
- **A webhook finding the appointment already `EXPIRED` (the 7.F sweep won
  the race) does not resurrect it to `CONFIRMED`** — that would double-book
  a released slot. Money arrived for something no longer held, so it
  initiates an automatic refund (`Payment → REFUND_PENDING`) instead. This
  is sweep-vs-webhook rule 3.
- **`payment_failed`: `Payment → FAILED`; `Appointment` stays
  `PENDING_PAYMENT`** — the client may retry while the hold is still live.
- **`refund_succeeded`: `Payment REFUND_PENDING → REFUNDED`.**
- **Idempotency via `ProcessedWebhookEvent`** (model already exists from
  Stage 2, unique on `provider_event_id`): the first step of handling checks
  whether `provider_event_id` is already recorded; if so, return 200 and do
  nothing. Otherwise process and record the event id. The provider may
  redeliver the same event; the effect must apply exactly once.
  State-transition handlers are additionally idempotent on their own
  (`PENDING → SUCCEEDED` applied twice is a no-op, not an error).
- **Response contract: return 200 OK quickly for everything accepted —
  including ignored event types and duplicate events.** Return a 4xx/5xx
  only for an invalid signature or a malformed body. The provider retries on
  any non-200, so a non-200 on a deliberately-ignored event would cause
  pointless redelivery.
- **A `CheckConstraint` enforces the ISO 4217 format (`^[A-Z]{3}$`) at the
  database level on both `Salon.currency` and `Payment.currency`.** The
  `RegexValidator` on the field only fires on `full_clean()` (forms,
  serializers, admin), not on `.objects.create()`/`.save()`, and a
  non-nullable `CharField` with no default silently persists `""` when the
  value is omitted. Without a DB constraint, raw ORM writes can persist
  `""` or any malformed value (e.g. `"uah"`, `"грн"`) the validator would
  have rejected. The constraint makes an invalid currency a
  database-level `IntegrityError` regardless of the write path —
  enforcement, not reliance on every caller remembering to pass a valid
  value.
- **The constraint duplicates the same invariant as the validator (the full
  `^[A-Z]{3}$` format, not merely "non-empty")** — a constraint that only
  forbade `""` would still let `"грн"` through, half a guard. To avoid the
  two enforcement points drifting, the regex pattern string is extracted to
  one shared module-level constant, used by both the `RegexValidator` and
  the `CheckConstraint`. One source of truth for the format; the two
  enforcement layers — Python-level validator, DB-level constraint — share
  it.
- **The constraint is applied to both fields, not just `Salon` (the
  source).** `Payment.currency` is copied from `Salon.currency` by the
  8.C payment-initiation service, not yet written; a constraint on
  `Payment` catches an 8.C copy bug rather than trusting it to copy a valid
  value. Cheap, and it removes a "trust the caller" dependency.
- **Consequence for tests: a `Salon` or `Payment` created with an
  invalid/empty currency now raises `IntegrityError`.** The regression test
  that "creating a row without a valid currency raises" — previously
  meaningless, since the row silently got `""` — becomes meaningful under
  this constraint and belongs to 8.B-bis.

## Stage 8.C decisions (payment initiation)

Decided 2026-08-20, in discussion.

- **Payment-initiation response envelope is provider-neutral:
  `{"payment": {...}, "provider_data": <provider-specific or null>}`.**
  `provider_data` holds whatever the frontend needs from the provider to
  continue payment (redirect URL, token, etc.); the mock returns `null`. The
  shape stays stable across mock and real providers. We deliberately do not
  put a Stripe-specific field (`client_secret`) in the contract: Stripe does
  not permit merchant accounts for Ukraine/Georgia residents, so the real
  provider will be a local acquirer whose frontend hand-off differs — binding
  the envelope to one provider family would be premature. The concrete
  provider choice is deferred (a business decision); the mock unblocks all
  Stage 8 code regardless.
- **Initiation is idempotent per appointment.** If a `Payment` in `PENDING`
  status already exists for the appointment, return the existing one (no
  second intent). A previous `FAILED` payment is retried *in place*, not by
  creating a second row: `Payment.appointment` is a `OneToOneField`, so at
  most one `Payment` row can exist per appointment. The same row (same pk)
  transitions `FAILED` → `PENDING` and gets a new `provider_reference_id` (a
  retry is a new provider transaction).
- **Paying an appointment not in `PENDING_PAYMENT` raises
  `InvalidStateTransitionError` (HTTP 409), reusing the existing exception +
  handler already used by `cancel_appointment`.** No new error class. 409
  Conflict = valid request conflicting with resource state.
- **Idempotency on duplicate submit (double-click / network retry): a
  repeated `POST` while a `PENDING` `Payment` already exists for the
  appointment does not call the provider again and does not create a second
  intent.** It returns the existing payment with `provider_data = null`.
  Rationale: idempotency here means "do not create a second payment," not
  "re-issue the provider instruction." The frontend is responsible for
  retaining the `provider_data` it received on the first response.
- **Provider-call failure: if `provider.start_payment()` raises, the
  service raises a domain `PaymentProviderError` that maps to HTTP 502 Bad
  Gateway** (the external provider failed, not our code). No `Payment` row
  is written in that case.
- **`start_payment`'s `reference` argument is `str(appointment.id)`, not the
  `Payment` id as originally planned in § Stage 8 decisions.** On a fresh
  attempt no `Payment` row exists yet at call time — § Stage 8.C's
  write-the-row-only-after-a-successful-provider-response ordering means the
  provider is called before any `Payment` id exists to pass. `appointment.id`
  is available at call time and works equally well for webhook correlation:
  `Payment.appointment_id` is unique (`OneToOneField`), so resolving
  `provider_reference_id` → `Payment` → `appointment` still identifies a
  single `Payment` unambiguously, on both the fresh-creation and the
  retry-in-place path.

## Stage 8.D decisions (guest pay endpoint)

Decided 2026-08-21, in discussion.

- **URL is `POST /api/v1/salons/<slug>/guest/appointments/<int:appointment_id>/pay/`,
  name `guest-appointment-pay`.** Symmetric sibling of the existing
  `guest-appointment-cancel` route. `appointment_id` is a URL path segment, but
  because the pay view resolves the appointment from the token (via
  `_GuestTokenAppointmentMixin`), not the URL, a mismatched URL id is a token
  error → 400, not 404.
- **Auth is the same as `cancel`: `HasValidGuestToken` permission +
  `ScopedRateThrottle` `"guest_token"`.** Not `AllowAny` — access is by token,
  not public. **This requires extending `HasValidGuestToken` itself, a shared
  permission class in `core/permissions.py` also used by the detail and
  cancel views**: its allowed-action set changes from `("view", "cancel")` to
  `("view", "cancel", "pay")`, and `"pay"` uses `for_cancel=False` — paying
  does not spend the cancel capability; payability is gated by
  `initiate_payment`'s own `PENDING_PAYMENT` check, not by token state.
  Recorded here because it alters a shared contract, not something local to
  the new view.
- **Request body is empty.** Everything `initiate_payment` needs is
  server-side or in the URL (`appointment_id` from the path, `salon` from the
  slug, provider chosen by the server, `now` from the server clock,
  amount/currency from the frozen snapshot). The client dictates nothing
  about the payment — narrower attack surface.
- **Response envelope is `{"payment": {id, status, amount, currency},
  "provider_data": null}`**, the neutral envelope mirroring the Stage 8.C
  decision; `provider_data` is `null` for the mock, not a Stripe
  `client_secret`. `Payment` fields are deliberately narrow (`id`, `status`,
  `amount`, `currency`) — service name is intentionally excluded because it
  is a property of the appointment, not the payment; the client already has
  appointment data from the `POST /bookings/` response, and the frontend
  composes both.
- **Success status is 201 vs 200: 201 Created when a new `Payment` row is
  created** (fresh path, or a prior `FAILED` payment reused as
  `FAILED` → `PENDING`); **200 OK when an existing `PENDING` `Payment` is
  returned unchanged** (the idempotency path). The HTTP status must reflect
  what actually happened to the resource, so the two are distinguished.
- **Internal service change enabling the above: `initiate_payment` gains a
  third return element `created: bool`**, so the return type becomes
  `tuple[Payment, object | None, bool]`. The idempotency branch returns
  `created=False`; the new-payment branch (including the `FAILED` →
  `PENDING` retry-in-place case) returns `created=True`. The view maps
  `created` → 201, not `created` → 200. The service reports the domain fact
  ("created or not"); the view translates it to an HTTP status — the service
  never knows about HTTP status codes. Blast radius of this signature change
  (verified by grep, 2026-08-21): 1 production site (the definition itself),
  11 two-tuple unpack sites in `test_payments_initiate_payment.py` that must
  be updated, 0 tests asserting the tuple's arity/shape. No production caller
  exists outside `services.py` — the 8.D view does not exist yet.
- **Error mapping, all via the existing `exception_handler`, no `try`/`except`
  in the view:** appointment not in `PENDING_PAYMENT` → `InvalidStateTransitionError`
  → 409; provider failure → `PaymentProviderError` → 502; foreign/mismatched
  `appointment_id` → 400 (`invalid_or_expired_token`) — the pay view reuses
  `_GuestTokenAppointmentMixin`, which resolves the appointment from the
  token, so a mismatched URL id is a token error, not a DB lookup; a real 404
  only if the token's own appointment row were deleted.

## Stage 8.E decisions (payment webhook handler)

Decided 2026-08-21, in discussion.

- **URL is `POST /api/v1/webhooks/payments/`, a top-level path outside the
  `/api/v1/salons/<slug>/` tenant prefix.** The provider does not know the
  salon's slug; precedent is `api/v1/auth/`, already top-level in
  `config/urls.py`. Permission is `AllowAny` — the endpoint is not behind
  tenant or user auth at all, only behind signature verification (below).
- **Request body is `{event_id, event_type, provider_reference_id}`.** The
  signature is carried in a request header, `X-Signature` (a name
  established here — no prior convention existed), not in the body: the
  signature is computed over the raw request body, so it must live outside
  the data it signs.
- **Payment lookup is `Payment.unscoped_objects.get(provider_reference_id=...)`,
  not the tenant-scoped `.objects` manager** — no tenant is bound yet at
  lookup time, since the webhook arrives before the salon is known;
  `salon` is then derived from the resolved `Payment` and a `tenant_context`
  is entered for the transition. This differs from the 8.C
  outbound call, where `provider_reference_id` did not exist yet and
  `str(appointment.id)` was sent as `reference` instead — here, an inbound
  webhook carries the provider's own key, which by this point already sits
  in `Payment.provider_reference_id`.
  `provider_reference_id` is indexed but not unique; each `start_payment`
  generates a fresh `uuid4` hex, so a collision cannot occur in practice.
  Revisit making it unique at the database level later.
  A lookup miss (no `Payment` with that `provider_reference_id`) logs a
  `logger.warning` (including the `provider_reference_id`) and returns 404.
  Open decision: revisit to 200 if a real acquirer retry-storms on 4xx — a
  404 is chosen now to surface the desync loudly (valid signature but
  unknown payment = us/provider out of sync), the same "desync = loud"
  stance as the impossible-transition warning below; a real provider's
  retry policy may later make 200 the safer choice.
- **Event types handled: `payment_succeeded`, `payment_failed`,
  `refund_succeeded`.** Any other `event_type` → 200, ignored, no action.
- **Status codes: 200 for everything accepted and processed, including
  ignored event types, duplicate events (idempotency), and events landing
  on an already-`EXPIRED` appointment.** Specific codes are pinned, not a
  generic "4xx" bucket: `401` for an invalid signature (sender
  authentication, not a format problem — distinct from a malformed body);
  `400` for a malformed/unparseable body or a missing required field;
  `404` for an unknown `provider_reference_id` (see the Payment lookup
  bullet above); `5xx` only for our own failure. Duplicates and ignored
  types return 200 rather than an error because providers retry on
  non-200 — erroring on an already-processed duplicate would make the
  provider hammer the endpoint again.
- **Signature verification is the first step in the view, run against the
  raw `request.body` bytes, not `request.data`, before the serializer
  parses anything.** The mock verification stub returns `True`
  unconditionally rather than checking a real signature, but the method
  and its call site must exist regardless — without them the URL is an
  open door, since anyone could `POST` a `payment_succeeded` event. A real
  provider adapter only fills in the method body later.
- **Idempotency is enforced by two independent layers, both kept:**
  - `ProcessedWebhookEvent.provider_event_id` (`unique=True`; the model is
    global, not tenant-scoped, because the event arrives before the salon
    is known). The first processing step checks whether `event_id` is
    already recorded; if so, return 200 with no further action.
  - The `ProcessedWebhookEvent` insert happens inside the same `atomic()`
    block as the state transitions it guards, so "processed" and "marked
    processed" commit together: a crash between them rolls back both, and
    a retry sees a clean, unprocessed state and processes the event
    exactly once.
  - This sits on top of the under-lock status recheck the transitions
    already perform (below) — two independent layers guarding the single
    invariant "process each event exactly once," so that weakening either
    one still leaves the other holding.
- **State transitions, each row fetched under `select_for_update()` with
  its status rechecked under the lock before transitioning:**
  - `payment_succeeded`: `Payment` `PENDING` → `SUCCEEDED` and
    `Appointment` `PENDING_PAYMENT` → `CONFIRMED`, both in the one
    transaction — the two state machines are coupled, since a `CONFIRMED`
    appointment without a `SUCCEEDED` payment would be a lying state. If
    the appointment is already `EXPIRED` (the § Stage 7.F sweep won the
    race), do not resurrect it to `CONFIRMED` — that would double-book a
    slot already released. Instead call `initiate_refund` (sweep-vs-webhook
    rule 3, § Stage 8 decisions) — the first real caller of the § Stage 8.F
    service.
  - `payment_failed`: `Payment` → `FAILED`; `Appointment` stays
    `PENDING_PAYMENT` — the client may still pay again while the hold is
    still alive.
  - `refund_succeeded`: `Payment` `REFUND_PENDING` → `REFUNDED`. This
    branch lives in the handler itself, not a separate service, the same
    as `payment_succeeded` above; also written inside the same `atomic()`
    plus ledger write.
- **An impossible transition still returns 200, but logs a
  `logger.warning`.** Under the lock, a status already at the event's
  target or further along the chain (e.g. `payment_succeeded` arriving
  when the `Payment` is already `SUCCEEDED`, `REFUND_PENDING`, or
  `REFUNDED`) is an expected duplicate/race and is a silent 200. A status
  the transition can never legally come from (e.g. `refund_succeeded` on
  a `SUCCEEDED` row, `payment_failed` on a `SUCCEEDED`/`REFUNDED` row)
  logs a warning naming the event, the `provider_reference_id`, and the
  current status — but still returns 200 and still writes the
  `ProcessedWebhookEvent` row, since a webhook must not 4xx a duplicate.
  This mirrors the 8.F allowlist-vs-duplicate split, except the anomaly
  logs instead of raising 409.
- **Scope of 8.E is reactive only.** The handler responds to provider
  events; it does not initiate payments (§ Stage 8.C/8.D) and does not
  compute refund eligibility itself. The only thing it initiates is the
  `initiate_refund` call on the `EXPIRED`-appointment case above, which
  invokes the already-written § Stage 8.F service rather than adding new
  refund logic.

## Stage 8.F decisions (refund initiation)

Decided 2026-08-21, in discussion.

- **No client-visible surface.** `initiate_refund` is a purely internal
  service — the guest never initiates a refund. There is no guest-facing
  `/refund/` endpoint, hence no external URL, request/response body, or HTTP
  status/error code to define for it. Its two callers are both system-side:
  (a) cancellation logic, for the cases where the deposit is refundable; (b)
  the webhook handler, when a late `payment_succeeded` webhook lands on an
  already-`EXPIRED` appointment — it initiates a refund rather than
  resurrecting the slot (sweep-vs-webhook rule 3, § Stage 8 decisions).
- **What is refunded: always the full deposit, never a partial amount.**
  Only the deposit is paid online through the provider; the remaining
  balance is paid in cash at the salon, never enters the system, and has
  nothing to return. `refund` therefore takes no amount argument — accepting
  one would open a class of bugs (refunding the wrong sum) for a value that
  is always implied.
- **Service signature: `initiate_refund(*, payment_id, salon, provider) ->
  Payment`.**
  - Takes `payment_id` and fetches the row itself under
    `select_for_update()`, so the row lock is held in the same transaction
    that writes the status — the same reason `cancel_appointment` takes
    `appointment_id`, not a ready object.
  - `salon` is passed explicitly for tenant scope: `.get(salon=salon,
    pk=payment_id)` makes a foreign `payment_id` a `DoesNotExist`, not a
    cross-tenant leak.
  - `provider` is injected, as in 8.C, never constructed inside the service.
  - No `now` parameter: `Payment` has no refund-timestamp field (verified
    directly against the model — only the inherited `created_at`/`updated_at`
    from `TimeStamped` exist); refund state is carried entirely by `status`
    (`REFUND_PENDING` / `REFUNDED`), so there is nothing for a timestamp
    argument to write.
- **Operation order is the mirror-opposite of 8.C, deliberately.** Write
  `SUCCEEDED → REFUND_PENDING` under the lock first, then call
  `provider.refund()` outside the transaction — a network call must not hold
  a row lock. Reason: risk asymmetry. If the provider call fails, the row is
  left `REFUND_PENDING`, which the Stage 8.G sweep flags after 72h —
  reversible. If the status were instead written only after a successful
  provider call, a crash between the provider call succeeding and the DB
  write would leave `SUCCEEDED` with the money already gone, and a retry
  would call the provider a second time — a double refund, irreversible. The
  order is chosen for the reversible failure mode. This is the opposite of
  8.C's order (provider call first, row written after), because in 8.C the
  row does not exist yet at call time; here it already does — different
  situations, different order.
- **Status gate, rechecked under the lock:**
  - `SUCCEEDED` → transition to `REFUND_PENDING`, then call the provider.
  - `REFUND_PENDING` / `REFUNDED` → a refund is already in flight or already
    done; return the existing `Payment` unchanged and do not call the
    provider again (idempotency, same shape as the existing-`PENDING` branch
    in 8.C).
  - Any other status (`PENDING`, `FAILED`, `EXPIRED`, `CANCELLED`) →
    `InvalidStateTransitionError` (409, `current_status` in `details`). None
    of these ever received money, so there is nothing to return; a refund
    requested against one of them means the caller decided to refund
    something it should not have — a bug in the caller, and the loud error
    is the detector.
  - `PROCESSING` is currently an unused `PaymentStatus` enum member — never
    assigned anywhere in the code (the mock does not model it; verified by
    grep). It falls into the 409 branch with the other non-`SUCCEEDED`
    statuses, which is correct for now: a not-yet-successful payment cannot
    be refunded. Revisit when a real acquirer with a synchronous
    `PROCESSING` state is added — a refund request arriving during
    `PROCESSING` would then be a timing race (sweep-vs-webhook category),
    not a caller bug.
- **Provider call: `provider.refund(provider_reference_id=payment.provider_reference_id,
  reference=str(payment.appointment_id))`.** `provider_reference_id`
  identifies the transaction being reversed; `reference` mirrors
  `start_payment`'s correlation key.
- **Scope of 8.F is initiation only:** `SUCCEEDED → REFUND_PENDING` plus the
  provider call. The confirming transition (`REFUND_PENDING → REFUNDED`) is
  a branch of the webhook handler on `refund_succeeded`, not a separate
  service — same as `payment_succeeded` lives in the handler, not a service.
  Refund eligibility ("does this case qualify for a refund") is also out of
  scope here: it lives in the caller — the cancellation branch computes it
  from `cancelled_by` and timing; the EXPIRED-webhook case decides it itself
  (rule 3). `initiate_refund` receives an already-made refund decision and
  executes it.

## Stage 8.G decisions (stuck-refund flagging sweep)

Decided 2026-08-24, in discussion.

Implementation specifics for the stuck-refund sweep whose policy (Variant B:
flag-not-retry; 72h platform constant; the delayed-vs-double-refund risk
asymmetry) was already decided in § Stage 8 decisions — this section does
not restate that policy, only how it's built.

- **The staleness clock is a dedicated `Payment.refund_initiated_at` field,
  not `updated_at`.** `updated_at` (`auto_now`) is rewritten on every
  `save()` — an untrustworthy clock for "how long has this been stuck", since
  any unrelated field write on the row would silently reset it.
  `refund_initiated_at` is stamped once, only at the real `SUCCEEDED` ->
  `REFUND_PENDING` transition inside `initiate_refund`, and frozen
  thereafter. This reverses § Stage 8.F's original "no `now` parameter"
  decision — `now` was restored to `initiate_refund` because there is
  finally a field to write it to.
- **The stamp is written only on the real transition, never on the
  idempotent no-op paths** (`initiate_refund` already `REFUND_PENDING` or
  already `REFUNDED`) — re-stamping on a duplicate call would move the
  clock every time a retry lands, defeating the field's purpose.
- **Sweep mechanics mirror the § Stage 7.F expiry sweep:** an unlocked
  candidate query (`status=REFUND_PENDING`,
  `refund_initiated_at__lte=now - STUCK_REFUND_THRESHOLD`,
  `flagged_for_review=False`), then each candidate is locked and rechecked
  independently in its own `transaction.atomic()` + `select_for_update()` —
  not one lock over the whole batch, so a single stuck row can't hold up or
  roll back the rest of the sweep.
- **Both conditions are rechecked under the lock — status still
  `REFUND_PENDING` AND not already flagged — not just one.**
  `status != REFUND_PENDING` catches a `refund_succeeded` webhook winning
  the race between the unlocked query and the lock (the refund completed in
  the meantime, so it was never actually stuck); `flagged_for_review`
  catches an overlapping sweep run. The query filter and the under-lock
  recheck are two independent layers: the recheck is what catches a change
  landing *after* the unlocked query already ran, which the filter itself
  cannot.
- **A `NULL` `refund_initiated_at` is never swept.** `NULL` for a payment
  that never entered a refund, and for any `REFUND_PENDING` row predating
  this field's migration — `__lte` on `NULL` is never true in SQL. This is
  correct, not a gap: there is no recorded stuck-since time to measure
  those rows against.
- **`STUCK_REFUND_THRESHOLD = 72h` is a platform constant in
  `payments/constants.py`**, per the § Stage 8 decision that refund
  settlement time is uniform across salons, not a per-salon lever. The
  `@shared_task` runs every 30 minutes via `beat_schedule`: a 72h threshold
  needs no finer granularity, and 30 minutes still catches a newly-stuck
  refund within the same hour it crosses the threshold.
- **Deviation from the original § Stage 8 wording, recorded deliberately:**
  that entry said the stuck flag comes "plus a warning-level log." The
  Stage 8.G implementation writes only the flag plus a per-salon info-level
  *summary* log (count flagged per salon) — it does not emit a per-row
  warning for each individual stuck payment. Reason: the flag itself is
  the persistent, queryable Stage 8 signal
  (`Payment.objects.filter(flagged_for_review=True)` is the exact list);
  any *active* per-row signal — a warning log or a human notification — is
  deferred to Stage 9, consistent with the already-established split that
  Stage 8 emits the raw signal and Stage 9 turns it into a notification.
  The "plus a log" from the original entry is therefore an intentional
  Stage 9 concern, not a dropped requirement.

## Stage 3-R decisions (per-salon identity revision — client AND staff)

Decided 2026-08-25, in discussion, before implementation, per CLAUDE.md's
design-first workflow. This is a revision block reopening closed Stage 3
identity decisions — not a new numbered stage in the main sequence.
Inserted here, out of chronological order, the same way Stage 11.5 was
inserted without renumbering every existing `Stage N` reference elsewhere
in this file, `docs/ARCHITECTURE.md`, and code comments (§ Agreed stage
order). No model, migration, or view code exists yet for this revision;
this entry and the paired reversal entries below (§ Stage 3-R reversals)
are the paper record the implementation stage is built against. Nothing
in `docs/ARCHITECTURE.md` is rewritten by this entry — see the follow-up
list at the end of this section for what needs updating once
implementation lands.

- **Principle: one site = one salon, full product-level isolation, no
  exceptions at the product surface.** Every human *login* — client,
  staff, admin, owner — is scoped to exactly one salon. The same person
  operating at two salons on this platform holds two entirely separate
  accounts, with two separate logins, and neither account can observe
  that the other exists or that both run on the same platform. No
  endpoint anywhere may check or reveal existence across salons, even
  incidentally (see "No-enumeration," below, and its paired reversal
  entry). This supersedes every prior identity decision that assumed a
  platform-wide `User` — see § Stage 3-R reversals below for each one,
  individually.

- **Model shape: two models, not one — `Customer` survives unchanged in
  kind; `Account` is new.** `Customer` is **not** merged into, replaced
  by, or dropped in favor of a new unified identity model.
  - **`Customer` remains the per-salon record of "who visited this
    salon"** — guest or registered, exactly as it is today: `salon`,
    `name`, `email`, `phone`, scoped by the existing
    `unique(salon, email)` constraint, unchanged. A guest with no account
    at all stays fully representable as a bare `Customer` row, with
    nothing else attached — this is not new, it is today's shape,
    preserved.
  - **`Account` is a new, separate, per-salon model holding login
    credentials** (password, verification state, `role`), also scoped by
    its own `unique(salon, email)`. `Account` is created only when a
    person actually registers — a guest never gets one. When a `Customer`
    registers, their `Account` gains a link to their existing `Customer`
    row at that same salon (mirroring today's `Customer.user` FK, just
    inverted in which model does the linking — the exact FK direction is
    implementation-stage detail, not fixed here).
  - In short: **`Customer` = who visited. `Account` = who has a login.**
    A `Customer` row can exist with no `Account` (guest) or with a linked
    `Account` (registered). An `Account` always has exactly one linked
    `Customer` at its salon once that link is made — `Appointment`
    keeps referencing `Customer`, never `Account`, unchanged from the
    standing rule that `Appointment` always references `Customer`, never
    `User`, directly.
  - `role` (see below) lives on `Account`, not on `Customer` — a `role`
    is meaningless for a bare guest `Customer` with no login at all.

- **Guest booking flow is unchanged by this revision.** The existing
  signed-token flow — book, pay the deposit, cancel, and (per the
  Reviews rule below) leave a review, all via the emailed
  `GuestAccessToken` link, with **no account of any kind required** — is
  untouched. That flow was always built entirely on `Customer` +
  `GuestAccessToken` and never touched `User` (`docs/ARCHITECTURE.md`
  § 3, § Stage 3 sub-step 3 decisions). Since `Customer` survives exactly
  as-is (previous bullet) and `Account` is purely additive — created only
  on registration, never required for booking — nothing in this
  revision changes any part of that flow. Recorded explicitly so it is
  not mistaken for in-scope of the identity redesign.

- **Email uniqueness: `unique(salon, email)` on `Account`.** This is new
  for `Account` (the login/credential model) and is what the isolation
  principle requires for it — `Customer`'s own `unique(salon, email)`
  already exists today and is **not** being changed by this entry; it is
  a separate, pre-existing constraint on a separate model, left as-is.

- **The existing `accounts.User` / `AUTH_USER_MODEL` is reversed as the
  client/staff/admin login identity — it is not deleted.** It continues
  to exist exactly as today and continues to serve Django's own
  `/admin/` login for platform operators (`is_staff`/`is_superuser`).
  What changes is only that it stops being used to create *login*
  identities for product accounts going forward — no future
  registration flow creates `Account` rows on it. `Customer` was never
  on `User` in the first place except via the now-superseded nullable
  `Customer.user` FK (see § Stage 3-R reversals). See "deliberate
  exception," below, for why this one cross-tenant identity is kept.

- **Roles for v1, on `Account`: `client` and `admin`.** `admin` is the
  single staff/back-office access level for v1 — finer-grained staff
  roles (e.g. owner vs. manager) are explicitly deferred, not designed
  now (see Deferred, below). An `admin` has back-office access for their
  one salon **and** the exclusive right to assign or change roles for
  other `Account`s within that same salon. `client` role on an `Account`
  means "a registered login, linked to a `Customer` at this salon, able
  to view their own booking history" — no back-office access.

- **Roles are never self-assigned.** A newly created `Account` gets
  exactly the `client` role, unconditionally, with no request parameter
  or self-service path capable of granting anything more. Elevating an
  `Account` to `admin` can only be performed by an existing `admin` of
  that same salon. This is an authorization-boundary rule, not just a
  default value — permission logic must enforce it as a hard boundary
  (no code path may grant `admin` except "an existing admin acted"), not
  merely default new rows to `client` and hope nothing else sets the
  field.

- **Role switching is not role acquisition.** An `Account` holding both
  roles (see Dual role, below) moves between an admin mode and a client
  mode at will — that is a UI/session-context distinction, not a
  privilege grant. An `Account` that has never been granted `admin` by
  an existing admin has no admin mode to switch into; there is no
  default, implicit, or inferred admin capability. A bare `Customer` with
  no `Account` at all (a guest) has no mode of any kind to switch
  into — nothing to switch from. Permission logic must keep "which roles
  does this `Account` actually hold" and "which mode is this session
  currently acting in" as two separate checks — the first is the
  security boundary, the second is presentation.

- **Dual role within one salon is one `Account`, not two.** A person who
  is both `admin` and `client` of the *same* salon (the textbook case: a
  stylist who books her own treatment at her own salon) holds a single
  `Account` row with both roles. Concretely, under the two-model shape
  above: her `Account` carries `admin`; her booking activity uses her
  linked `Customer` row at that same salon, exactly like any other
  registered client's booking does. This does **not** require a second
  `Account` and does **not** require relaxing `unique(salon, email)` on
  either `Account` or `Customer` — the uniqueness constraints govern the
  *login* and the *visitor record* respectively, not the *role*, so one
  `Account` legitimately carrying two roles is not a uniqueness
  violation on either model. Chosen over a two-account shape (a separate
  login for her admin work and her own bookings) because one `Account`
  naturally carries more capability without inventing a bridge between
  two otherwise-separate login tables for what is, in the real world,
  one person.

- **`SalonStaff` is removed outright, not kept as a thin wrapper.** Its
  only structural purpose — bridging one `User` to many salons via
  `unique(user, salon)` — is exactly the capability the isolation
  principle removes: once a login is intrinsically scoped to one salon,
  a join table has nothing left to bridge. `role` moves onto `Account`
  directly as a plain field, carrying forward `SalonStaff`'s original
  "keep the value space open for a second role later, no shape migration
  needed" reasoning unchanged (see § Stage 3-R reversals, "SalonStaff
  join + `unique(user, salon)`," for the full account of what's
  preserved vs. what's superseded).

- **Login contract: client and admin login move under the salon URL
  prefix, `/api/v1/salons/<slug>/...`**, reusing the existing, already
  tested path-prefix tenant-resolution middleware rather than inventing a
  new resolution mechanism. Login authenticates against `Account`; a
  successful client login's session reaches that person's booking
  history through their linked `Customer` row, not through any data
  stored on `Account` itself. This reverses the prior "auth is
  platform-level, outside the salon prefix" placement (§ Stage 3-R
  reversals, "ARCHITECTURE §13") for the product login surface
  specifically — Django's own `/admin/` is untouched (see "deliberate
  exception," below).
  - **RECOMMENDED, pending confirmation — not yet a settled part of this
    entry:** one shared per-salon login endpoint (e.g.
    `/api/v1/salons/<slug>/auth/login/`) serving both `client` and
    `admin`, with role read from the single matched `Account` row and
    reflected in the resulting session/JWT — rather than sibling
    endpoints split by role. Reasoning: under the two-model shape above,
    `client` and `admin` are both rows in the *same* `Account` table,
    not two different tables — splitting the login endpoint by role
    would require deciding which table to check first (or checking
    both) for no benefit. A shared endpoint also avoids a response-shape
    difference between a "client login" and an "admin login" endpoint
    that could itself leak whether a given email belongs to an admin at
    that salon — a narrower instance of the enumeration problem the
    no-enumeration rule guards against below. Flag any disagreement
    before this is implemented.

- **Completing the Login-contract entry: email verification and password
  reset endpoints also move under the salon prefix,
  `/api/v1/salons/<slug>/...`** — not just login and registration. This
  fills the gap the original Login-contract entry (above) explicitly left
  open ("did not name verification/reset"); it supersedes nothing already
  written and is a completing decision, not a reversal.
  - **Reasoning:** consistency — one tenant-resolution mechanism for the
    entire product surface, using the existing, already-tested
    `TenantResolutionMiddleware` (salon resolved from the URL path), rather
    than a second, parallel mechanism where salon travels inside the token
    payload and the view resolves it via `unscoped_objects`. This is the
    same "salon comes from the URL, not from an alternate channel"
    reasoning the Login-contract entry already used for login/registration,
    applied uniformly rather than carved out as an exception for these two
    endpoints.
  - **Consequence:** the verification-token payload only needs
    `{account_id, email}` — salon is known from the URL the emailed link
    points at, not embedded in the token itself (this resolves open
    question 2(b) from the Stage 3-R implementation recon in the direction
    of the URL-carries-salon shape, not the token-carries-salon shape).
    `accounts/tasks.py`'s `send_verification_email` and
    `send_password_reset_email` must build salon-aware links (e.g.
    `{FRONTEND_URL}/salons/<slug>/verify-email#token=...`) instead of
    today's salon-less links. This ripples into the not-yet-built
    frontend's route shape, but nothing exists yet on that side to break.

- **Known risk, recorded now so it isn't lost before 3-R.E implements JWT
  wiring — `USER_ID_FIELD` / cross-model token collision.** SimpleJWT's
  `USER_ID_FIELD` setting defaults to plain `"id"` — a bare, model-agnostic
  integer PK. `User` and `Account` are separate tables with independent
  auto-increment sequences, both starting at 1, so a `User` row and an
  `Account` row can legitimately share the same `id`. An `Account`-issued
  token carries `user_id = <that id>`. If an `Account`-aware authentication
  class and the stock `User`-aware `JWTAuthentication` were ever *both*
  active on the same endpoint's authentication stack, an `Account` token's
  `user_id` claim could numerically collide with an unrelated `User`'s
  `pk`, and whichever authentication class runs first would **silently
  authenticate as the wrong identity** — a real security hazard, not a
  hypothetical edge case, given both sequences start at 1.
  - **Mitigation requirement for 3-R.E (not resolved now, recorded as a
    hard requirement on that sub-step):** either (a) strict
    endpoint/authentication-class separation — `Account`-token views never
    share an authentication stack with `User`-token views — or (b) an
    explicit token-type claim, checked before the `pk` lookup, so a
    mismatched token type is rejected before it ever reaches a model
    lookup. **3-R.E must not ship without one of these.**

- **Confirmation, not a decision needing debate: the Stage 3-R
  implementation recon read the actual installed source in this
  environment (Django 5.2.17, `djangorestframework-simplejwt==5.5.1`,
  confirmed via the running `backend` container, not the host `.venv`) to
  resolve open questions 2(c) and 2(d).** Findings:
  - **SimpleJWT is retargetable to `Account`, not hand-built from
    scratch.** The core `Token`/`AccessToken`/`RefreshToken` classes and
    `Token.for_user()` are pure duck-typing (`getattr(user,
    api_settings.USER_ID_FIELD)`, no `isinstance` check, no
    `get_user_model()` call). Three integration points *are* hardcoded to
    `get_user_model()` and need explicit, sanctioned overrides rather than
    reuse-as-is: a custom `TokenObtainSerializer.validate()` (login —
    the stock version calls Django's `authenticate()`, tied to
    `AUTH_USER_MODEL`), a custom `JWTAuthentication.get_user()`
    (request-time auth — the stock version does `self.user_model =
    get_user_model()` in `__init__`; overriding `get_user()` is already
    the library's own sanctioned extension point, demonstrated by its own
    shipped `JWTStatelessUserAuthentication` subclass), and a custom
    `TokenRefreshSerializer.validate()` (refresh — same
    `get_user_model()` pattern). Logout/blacklist
    (`BlacklistMixin.blacklist()`) degrades gracefully rather than
    failing hard: an unresolvable `user_id` just stores `user=None` on
    the blacklist/outstanding-token row.
  - **`PasswordResetTokenGenerator` works duck-typed against `Account`
    with no replacement needed.** Its `_make_hash_value()` reads
    `user.pk`, `user.password`, `user.last_login`, and
    `user.get_email_field_name()` — the file imports neither
    `AbstractBaseUser` nor `get_user_model()` anywhere, so there is no
    `AUTH_USER_MODEL` coupling to work around, contrary to what was
    flagged as an open, unverified risk in the earlier design-discussion
    turn.
  - **Field-shape basis for 3-R.A, established by this recon:** `Account`
    must carry `id` (free), `password`, `email`, `is_active`, and a
    nullable `last_login`, plus a trivial `get_email_field_name()` method
    returning `"email"`. `last_login` in particular is a requirement
    surfaced *by this recon* — not something that would have been
    obviously included otherwise.

- **No-enumeration, tightened to isolation-driven wording: a per-salon
  registration or password-reset-request endpoint must never reveal that
  a given email exists in *any* salon — not only "this one."** This is
  not merely "scope the existing anti-enumeration check to one salon" —
  it is a stronger requirement the isolation principle itself demands:
  no code path may check a submitted email against any salon other than
  the one the request is scoped to, for *any* purpose, including a
  well-intentioned one (e.g., a "friendlier" error suggesting the email
  is already registered elsewhere on the platform). Doing so would leak
  cross-salon existence even while technically returning an identical
  response shape. The check itself — not just the response — must never
  reach outside the requesting salon. See § Stage 3-R reversals,
  "no-enumeration response shape," for how this supersedes the original
  entry's global-namespace framing.

- **Deliberate exception (the only one): the platform-operator Django
  admin (`/admin/`, `is_superuser`) stays cross-tenant.** The platform
  owner sees all salons through it, for support and billing — unchanged
  from today's `core.admin.SalonScopedAdmin`, already on record as *"a
  deliberate, blanket cross-tenant tool for platform operators, not a
  per-salon back office"* (§ Stage 3 sub-step 4 decisions). "Full
  isolation, no exceptions" in the principle above governs the **product
  surface** — what one salon's `Account`/`Customer` data and API
  responses can observe about another salon — and does not reach the
  ops/superuser layer. Recorded explicitly, in the same entry as the
  principle itself, so "no exceptions" is never later misread as
  abolishing platform-operator access.

- **First-admin provisioning: platform-owner-initiated, direction only.**
  The chicken-and-egg problem (a brand-new salon's first admin has no
  existing admin at that salon to grant their role) is resolved by the
  platform owner, via the superuser Django admin: the owner creates the
  salon and its first `Account` with `admin` role directly. That first
  admin then assigns roles within their own salon going forward, same as
  any other admin. This is the intended direction, not a mechanism — see
  Deferred, below, for what's explicitly not decided yet.

- **Account model, settled shape (2026-08-26) — supersedes the "Deferred"
  items on FK direction/shape below; this entry decides them.**

  Link to `Customer`: `Account` holds the FK, as a nullable
  `OneToOneField`, `on_delete=SET_NULL`.
  ```python
  customer = models.OneToOneField(
      Customer, null=True, blank=True, on_delete=models.SET_NULL, related_name="account"
  )
  ```
  - Direction (on `Account`, not `Customer`): a guest is a bare
    `Customer` with no `Account` row at all — not a `Customer` carrying
    a null "account" field. The common case (guests, the majority of
    `Customer` rows) stores nothing extra; the link exists only on the
    rarer `Account` rows that actually have one. This also lets an
    admin-only `Account` express "I may have no `Customer`" as an
    explicit nullable field on the table that actually needs it, rather
    than as an absence on the other table.
  - `OneToOneField`, not a plain `ForeignKey`: per the per-salon design
    and the already-decided "dual role = one `Account`, not two," a
    `Customer` can never legitimately have more than one `Account` at
    its salon. `OneToOneField` makes that invariant correct-by-
    construction — DB-enforced — rather than a convention nothing
    checks.
  - `null=True`: required, not a bootstrapping convenience. An admin
    provisioned with no prior visit history has `customer=null` (see
    admin-provisioning below) — this is a standing shape, not a
    transient state during setup.
  - `on_delete=models.SET_NULL`, not `PROTECT`: a `Customer` is a visit
    record; an `Account` is a login the person created deliberately and
    owns. Deleting a visit record must never delete the person's login
    — the `Account` survives with `customer` set to null. There is
    currently no delete path for `Customer` anywhere in the codebase, so
    this is defensive/forward-looking, not enabling visit deletion now.
    `SET_NULL` over `PROTECT` because the login belongs to the person,
    not to the visit record — unlike `Review.appointment` (`PROTECT`),
    where protecting the historical record itself is the point.

  Base class and field shape (feeds 3-R.A):
  - `Account` subclasses `AbstractBaseUser`, `TenantScopedModel`,
    `TimeStamped` — **not** full `AbstractUser`. `AbstractBaseUser`
    gives Django's proven password hashing (`set_password`/
    `check_password`) and `last_login` without pulling in
    `is_staff`/`is_superuser`/`groups`/permissions — that admin/
    superuser machinery stays on `User`, the deliberate `/admin/`
    exception recorded above. Hand-rolling password hashing on a plain
    `models.Model` would be strictly less safe than reusing Django's
    vetted machinery; the full `AbstractUser` permission surface is
    machinery `Account` doesn't need and shouldn't carry.
  - From `TenantScopedModel`/`TimeStamped`, `Account` inherits and must
    **not** redeclare: `id`, the `salon` FK (`CASCADE` — a deleted salon
    takes its `Account` rows with it, matching `SalonStaff`'s existing
    behavior today), `created_at`/`updated_at`, the
    `objects`/`unscoped_objects` manager pair, and the `(id, salon)`
    uniqueness constraint.
  - New fields `Account` declares:
    - `email` (`EmailField`, no own `unique=True`) — uniqueness is the
      `(salon, email)` constraint, same pattern as `Customer.email`
      today.
    - `password` — via `AbstractBaseUser` (hashed, not redeclared).
    - `role` (`CharField`, choices `client`/`admin`, default `client`).
      Today's `SalonStaffRole` has only `admin`; this *adds* `client`
      alongside it, it doesn't inherit a richer existing value space.
      Default `client` is the self-service-registration guard — see
      admin-provisioning below for the sanctioned exception to that
      default.
    - `is_active` (`BooleanField`, default `True`) — needed so a
      deactivated `Account` cannot authenticate; SimpleJWT reads this
      field by name.
    - `last_login` (nullable `DateTimeField`) — via `AbstractBaseUser`;
      surfaced as required by the `PasswordResetTokenGenerator` recon
      (§ confirmation above), not redeclared.
    - `email_verified_at` (nullable `DateTimeField`, null = unverified)
      — mirrors today's `User.email_verified_at`; the verification flow
      (3-R.D) depends on it.
    - `USERNAME_FIELD = "email"`.
    - A `(salon, email)` `UniqueConstraint` in `Meta` (e.g.
      `account_salon_email_uniq`), mirroring `Customer`'s
      `customer_salon_email_uniq`, added alongside the inherited
      `(id, salon)` constraint via `*TenantScopedModel.Meta.constraints`.

  Admin-account provisioning: two paths, clarifying the first-admin-
  provisioning entry above. Both are privileged-actor-driven; role is
  never self-assigned, consistent with the roles-are-never-self-assigned
  rule already recorded.
  1. Existing client elevated to admin. A person already holding an
     `Account` with `role=client` at this salon is granted admin by an
     existing admin of that same salon. This *adds* the admin role to
     their existing `Account` — it does not create a second one. Same
     case as "dual role = one `Account`, not two": one login, both
     roles, switching modes.
  2. Person with no prior history provisioned directly as admin.
     Someone never a client here — hired, walked in — has no `Account`
     and no `Customer`. A privileged actor creates an `Account` directly
     with `role=admin`, `customer=null`. Two sub-cases, differing only
     in who acts: the salon's *first* admin (salon empty, no existing
     admin to grant the role) is created by the platform owner via the
     superuser Django admin (the first-admin-provisioning direction
     already recorded above); subsequent staff hired into an already-
     established salon are created by an existing admin of that salon
     through the product back office.

  This pins, in addition to `customer` nullability above: `role`
  defaulting to `client` applies to self-service public registration
  only — it is the self-assignment guard for that one path, not an
  absolute rule. A privileged actor creating or elevating an `Account`
  through a back-office path sets `role` explicitly, including directly
  to `admin`; that explicit set is the sanctioned exception the guard is
  built to allow, not a violation of it.

  Explicitly deferred, not designed here: the concrete mechanism of
  admin provisioning — which endpoint, which admin-panel action, which
  back-office UI, how a salon admin creates or elevates another account.
  This entry fixes only that the *model* permits these shapes (`Account`
  with `customer=null` and `role=admin`, created by a privileged actor);
  the how belongs to 3-R.D/3-R.E or a later admin-panel stage.

- **Reviews come from any `Customer` with a `COMPLETED` booking via the
  existing guest-token link, never from `Account`/login status.**
  Consistent with, not exceptional to, the "guest booking flow is
  unchanged" bullet above — review submission (Stage 11, not yet built)
  will use the same `Customer` + `GuestAccessToken` mechanism the rest of
  the booking lifecycle already uses, with no dependency on whether that
  `Customer` has a linked `Account`. This retires the *previously
  stated* rule that review submission requires "an authenticated
  `User`-linked `Customer`" (§ Identity, quoted in full under § Stage
  3-R reversals, "ARCHITECTURE §4") — recorded here since Stage 11
  hasn't started and there is nothing in code to reverse, only the
  stated business rule.

- **Deferred — recorded as known-open, not resolved by this entry:**
  - Finer-grained staff roles beyond the single `admin` level for v1
    (e.g. an owner/manager distinction, delegation nuance). `role` is
    kept as an open-ended field specifically so this can be added later
    without a shape migration, mirroring the same reasoning `SalonStaff`
    originally used for its own `role` field.
  - The exact technical mechanism of first-admin provisioning (a Django
    admin action, a management command, an invite-link flow, or
    something else) — only the direction (platform-owner-initiated) is
    decided here.

**`docs/ARCHITECTURE.md` follow-ups (not written by this entry):**

- § 2 (entity table): rewrite `User`, `SalonStaff` entries; add
  `Account`; update `Customer`'s entry to note its optional link to
  `Account`.
- § 3 (authentication architecture): rewrite "Registered users,"
  "Guests," "Guest → registered linking," and "Salon staff" subsections.
- § 4 (authorization): rewrite the role list and permission-class
  descriptions to reflect `Account`/`Customer`/role instead of
  `User`+`SalonStaff`/`Customer`.
- § 13 (API surface): rewrite the auth endpoint list; remove
  `/api/v1/me/...`.

## Stage 3-R reversals (per-salon identity revision)

Decided 2026-08-25, alongside § Stage 3-R decisions above. Each entry
below preserves the *original* reasoning for the record, states what
replaces it, and states why — per the standing rule that a reversal is a
new dated entry, never a backdated edit to the original.

### Reversal: § Identity — one-`Customer`-table with a nullable `User` FK

Original text, quoted in full:
> Guest booking is allowed. There is **one `Customer` table for the whole
> platform**, scoped to a salon (`salon` FK), with `name`, `email`,
> `phone`, and a **nullable** FK to `User`.
> - Guest = `Customer` with `user=NULL`.
> - Registered = `Customer` with `user` set.
>
> ...
>
> - **One `User` may be linked to multiple `Customer` rows** — one per
>   salon, since a platform-wide user account can be a customer at more
>   than one tenant.

Original reasoning preserved: `Customer` correctly represents per-salon
booking identity, and the guest-vs.-registered distinction (a `Customer`
row that may or may not have real login credentials behind it) remains
exactly right — this reversal does **not** touch that. `Customer` itself
is not reversed, dropped, or restructured; it survives unchanged (§
Stage 3-R decisions, "Model shape").

What replaces it and why: only the *target* of the registered-vs.-guest
distinction changes. "Registered = `Customer` with `user` set" is
superseded by "registered = `Customer` with a linked `Account`" — the
new, separate, per-salon `Account` model (§ Stage 3-R decisions) takes
over the role `User` used to play here, scoped to one salon instead of
platform-wide. "One `User` may be linked to multiple `Customer` rows —
one per salon" is fully retired, not narrowed: under full isolation,
an `Account` is linked to exactly one `Customer`, at its own one salon,
full stop — there is no cross-salon linking relationship left to
represent, because the platform-wide login it depended on no longer
exists for product accounts.

### Reversal: § Identity — guest→`User` cross-salon merge

Original text, quoted in full:
> **Linking a guest `Customer` to a new `User` account happens only after
> email verification.** Never link by phone number — phone numbers are
> not a reliable identity signal (reassigned, unverified, shared).

And, from `docs/ARCHITECTURE.md` § 3 (implementation of the above):
> On verification, every `Customer` row across all salons whose email
> exactly matches the verified `User`'s email is linked (`Customer.user`
> set).

Original reasoning preserved: "never link by phone, only by a verified
strong signal" was and remains sound judgment about identity-signal
reliability — that part of the reasoning is not what's wrong, and it
carries forward directly into what replaces this entry, below.

What replaces it and why: the *cross-salon* reach of this mechanism
(`link_guest_customers`, scanning every salon's `Customer` rows for a
matching email) is removed outright — its premise, "the same verified
email across salons is safely the same person, auto-link them," is
exactly what full isolation reverses. What replaces it is a **same-salon**
analogue, made concrete by the `Customer`/`Account` split (§ Stage 3-R
decisions): when a person registers at a salon they've already visited
as a guest, matching their new `Account` to their existing, same-salon
`Customer` row by verified email — never by phone — is exactly the
reconciliation this original entry described, just correctly bounded to
one salon instead of scanning every salon on the platform. The judgment
about *which signal* to trust is unchanged; only the *scope* it's allowed
to search is what's reversed.

### Reversal: `User.username` dropped / global `email` as `USERNAME_FIELD` (§ Stage 3 decisions)

Original text (summarized; full entry runs long in the source file):
`username` was dropped from `User`, `email` was made unique and set as
`USERNAME_FIELD`, with a custom `UserManager` — recorded as having been
implemented first and written up after the fact, later approved by the
user with the process gap producing CLAUDE.md's standing "raise
model/manager/migration changes before implementing" rule.

Original reasoning preserved: "email, not username, is the login
identity" remains correct and is not reversed — that judgment holds for
`Account` exactly as it held for `User`.

What replaces it and why: what's superseded is only the *scope* of the
uniqueness — global → `unique(salon, email)` on the new `Account` model
(§ Stage 3-R decisions) — driven directly by the full-isolation
principle, not by any reconsideration of email-vs-username. `User`
itself keeps its own global-unique `email` unchanged, since it continues
to serve only the platform-operator login (§ Stage 3-R decisions,
"deliberate exception"), where global uniqueness is still correct — this
reversal applies to the *product* login surface only. Worth noting for
the record: this reversal is itself being raised and recorded *before*
any model/migration work happens, the discipline the original entry's
process failure produced.

### Reversal: no-enumeration response shape (§ Stage 3 decisions)

Original text, quoted in full:
> **Registration and password-reset-request never reveal whether an
> email exists.** Both return an identical response (status, body)
> regardless of whether the email is already registered...

Original reasoning preserved: the anti-enumeration requirement itself is
not reversed — a registration or reset-request response must still never
reveal existence.

What replaces it and why: the entry's framing assumed one global email
space, one global "does it exist" question, answered identically either
way. That framing is superseded — existence is now a per-salon question,
and per § Stage 3-R decisions ("No-enumeration, tightened to
isolation-driven wording"), the requirement is stronger than a
same-response-shape guarantee: the *check itself* must never reach
outside the requesting salon, for any reason, not just the response
returned to the caller. A per-salon registration endpoint that happened
to check email existence against other salons — even if it still
returned an identical-looking response — would violate isolation despite
technically satisfying the original entry's letter. This reversal closes
that gap explicitly.

### Reversal: email-verification token subject (§ Stage 3 decisions)

Original text, quoted in full:
> **Email verification token: stateless, not single-use, payload includes
> the target email.** `TimestampSigner`-signed `{"user_id", "email"}`,
> 48h expiry (`EMAIL_VERIFICATION_TIMEOUT`). Verifying is idempotent...
> Including the target email in the signed payload, checked against the
> user's *current* `email` at verify time, means a future "change email"
> feature gets automatic invalidation of old tokens for free.

Original reasoning preserved: the stateless/signed/idempotent shape, and
"embed the target email so a changed email auto-invalidates old tokens,"
both remain sound design choices.

What replaces it and why: the token's subject (`user_id`, naming the
global `User` table) is superseded — a client's verification token now
needs to reference the per-salon `Account` row instead, and since
`Account`'s own uniqueness is `(salon, email)` rather than a bare global
email, the payload likely needs a salon reference too for the token to
stay unambiguous. The exact new payload shape is implementation-stage
detail, not fixed by this entry.

### Reversal: password-reset generator (§ Stage 3 decisions)

Original text, quoted in full:
> **Password reset uses Django's built-in `PasswordResetTokenGenerator`**,
> not a hand-rolled signer — it's already self-invalidating on password
> change (the hash it produces incorporates the current password hash),
> which is exactly the single-use property this token needs...

Original reasoning preserved: "self-invalidating on password change, no
separate revocation step needed" remains exactly the right property to
want for a reset token.

What replaces it and why: `PasswordResetTokenGenerator` is built around
`AUTH_USER_MODEL`-shaped instances. Since `Account` (not `User`) now
holds client/admin credentials, whether this specific Django class still
applies cleanly to an `Account` row is an open technical question, not
yet verified (flagged, not resolved, in the prior paper design
discussion). Recorded here as a known follow-up for whichever stage
implements this — this entry reverses the *applicability*, not the
underlying property being sought.

### Reversal: `docs/ARCHITECTURE.md` § 3 — staff shares the platform-wide login

Original text, quoted in full:
> **Salon staff.** Same `User`/JWT login as registered customers;
> authorization is layered on top via `SalonStaff` (§ 4), not a separate
> auth mechanism.

Original reasoning preserved: the underlying goal — don't invent a
second, redundant auth mechanism for staff — is not reversed; if
anything it's honored more directly now.

What replaces it and why: staff/admin no longer shares a *platform-wide*
login with clients — both now live on the same *per-salon* `Account`
model instead, distinguished by `role`, rather than by using a shared
global identity plus a separate authorization join. "Not a separate auth
mechanism" goes from being true because staff and clients shared `User`,
to being true because staff and clients are rows in the same `Account`
table now. **Needs an `ARCHITECTURE.md` § 3 rewrite** (follow-up, not
done here).

### Reversal: `docs/ARCHITECTURE.md` § 4 — the "Customer" role definition

Original text, quoted in full:
> **Customer** (authenticated `User` with a linked `Customer` in the
> target salon) — everything Guest can do, plus: view booking history,
> leave reviews, cross-salon account management (`/api/v1/me/...`).

Note on naming: this "Customer" is the *role tier's name* in § 4's role
list — a pre-existing naming coincidence with the `Customer` *model*,
not a statement that the model is going away. `Customer` the model
survives (§ Stage 3-R decisions, "Model shape"); only this role tier's
description and one of its named capabilities are wrong now.

Original reasoning preserved: the *tier* itself — "an authenticated
person, above guest, below staff, tied to their booking relationship
with a salon" — remains a real, needed role; only its description and a
named capability are wrong now.

What replaces it and why: two things in this sentence are superseded.
(1) "authenticated `User` with a linked `Customer`" no longer describes
the mechanism — it becomes "an `Account` with `role=client`, linked to a
`Customer`, at one salon." (2) "cross-salon account management
(`/api/v1/me/...`)" directly contradicts full isolation and is retired
along with it — see the `ARCHITECTURE.md` § 13 reversal, next. (3)
"leave reviews" as a capability gated on account status is separately
superseded by the Reviews rule (§ Stage 3-R decisions) — recorded there,
not re-litigated here. **Needs an `ARCHITECTURE.md` § 4 rewrite**
(follow-up, not done here).

### Reversal: `docs/ARCHITECTURE.md` § 13 — auth endpoints outside the salon prefix

Original text, quoted in full:
> A small set of endpoints are platform-level, outside any salon prefix,
> because they aren't salon-scoped: `/api/v1/auth/...` (User
> login/registration) and `/api/v1/me/...` (a User's linked Customers
> across salons).

Original reasoning preserved: the *principle* stated here — the URL shape
should reflect what is and isn't salon-scoped — is not reversed; it's
exactly what's being honored by relocating auth now that auth *is*
salon-scoped.

What replaces it and why: `/api/v1/auth/...` for client/admin
login/registration moves under `/api/v1/salons/<slug>/...` (§ Stage 3-R
decisions, "Login contract"), since it's now salon-scoped by
construction. `/api/v1/me/...` is removed entirely, not relocated — there
is no cross-salon account view for an `Account` that only ever belongs
to one salon, consistent with the § 4 reversal above. **Needs an
`ARCHITECTURE.md` § 13 rewrite** (follow-up, not done here).

### Reversal: `SalonStaff` join + `unique(user, salon)`

Original text, quoted in full (`docs/ARCHITECTURE.md` § 2):
> **SalonStaff** — join of `User` × `Salon` with a `role`. A back-office
> login.

And (§ Business rules):
> **One `SalonStaff` role for v1.** The `role` field is kept on the model
> so a second role can be added later without a shape migration — only
> the field's value space grows.

And the model's own constraint: `models.UniqueConstraint(fields=["user",
"salon"], name="salonstaff_user_salon_uniq")`.

Original reasoning preserved: "keep `role` as an open field with room to
grow, not a hardcoded single value" is exactly right and is carried
forward unchanged onto `Account`.

What replaces it and why: `SalonStaff` as a join table is removed
outright (§ Stage 3-R decisions, "SalonStaff resolution") — its
structural purpose, bridging one `user` to many salons via the composite
`unique(user, salon)` constraint, is exactly the capability the isolation
principle removes. Once staff identity is intrinsically single-salon, a
join table has nothing left to bridge; `role` becomes a plain field on
`Account`. **Needs an `ARCHITECTURE.md` § 2 rewrite** (entity table entry
for `SalonStaff`, and to add `Account` alongside `Customer`) and a § 4
rewrite (permission-class description) (follow-ups, not done here).
