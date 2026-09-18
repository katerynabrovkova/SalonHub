/**
 * Booking flow routing skeleton (docs/DECISIONS.md § Stage 14 planning).
 * Decides which step to render from URL query params only — no data
 * fetching yet. The real step components (ServiceStep/SpecialistStep/
 * DateTimeStep, Stage 14.B.2+) replace these placeholders later.
 *
 * Query params: `entry` ("service" | "specialist"), `service`,
 * `specialist`, `slot`, `step`. Selections live in the URL (not component
 * state) so the flow survives a refresh and a step can be shared/returned
 * to — contact info stays out of the URL entirely (Stage 14.C).
 */
import { headers } from "next/headers";
import { notFound } from "next/navigation";

import { getAvailability } from "@/lib/scheduling/getAvailability";
import { groupAvailabilityByDay } from "@/lib/scheduling/groupAvailabilityByDay";
import { getSpecialistDetailPage } from "@/lib/specialists/getSpecialistDetailPage";
import { getSpecialistsPage } from "@/lib/specialists/getSpecialistsPage";
import { getSalonInfoPage } from "@/lib/tenants/getSalonInfoPage";
import { SALON_SLUG_HEADER } from "@/middleware";

import ServiceSelectionGrid from "../services/ServiceSelectionGrid";
import SpecialistSelectionGrid from "../specialists/SpecialistSelectionGrid";
import ContactInfoForm from "./ContactInfoForm";
import DateTimeSelectionGrid from "./DateTimeSelectionGrid";

interface BookingPageProps {
  searchParams: Promise<{ [key: string]: string | string[] | undefined }>;
}

// Step 3's loaded window size (docs/DECISIONS.md § Stage 14 UI decisions:
// booking step 3, "Data fetching"). Matches DateTimeSelectionGrid's own
// WINDOW_DAYS — not imported from there, since that component exports no
// constants and its internals are out of scope for this change.
const WINDOW_DAYS = 14;

// Mirrors services/page.tsx's parseCategory: Next.js repeats a query key as
// string[] only for a literally repeated `?x=1&x=1` URL, which nothing in
// this app's own links ever produces — collapsing to the first value is a
// normalization, not data loss in practice.
function paramToString(value: string | string[] | undefined): string | undefined {
  return Array.isArray(value) ? value[0] : value;
}

function todayIsoDate(): string {
  return new Date().toISOString().slice(0, 10);
}

// Pure UTC calendar-date arithmetic, same reasoning as
// DateTimeSelectionGrid's own addDays: `date` is a plain YYYY-MM-DD with no
// time/offset of its own, so anchoring to UTC keeps this immune to the
// server process's host timezone.
function addDays(date: string, days: number): string {
  const [year, month, day] = date.split("-").map(Number);
  return new Date(Date.UTC(year, month - 1, day + days)).toISOString().slice(0, 10);
}

export default async function BookingPage({ searchParams }: BookingPageProps) {
  const params = await searchParams;

  const entry = params.entry;
  if (entry !== "service" && entry !== "specialist") {
    notFound();
  }

  const service = paramToString(params.service);
  const specialist = paramToString(params.specialist);

  // There is no earlier step where the identifying id could still be
  // absent — every real entry into /booking already carries it — so this
  // check is unconditional, not gated behind a specific step value.
  const requiredId = entry === "service" ? service : specialist;
  if (!requiredId) {
    notFound();
  }

  const parsedStep = Number(params.step);
  if (parsedStep === 1) {
    notFound();
  }
  const step = Number.isInteger(parsedStep) && parsedStep >= 2 ? parsedStep : 2;

  if (step === 3) {
    if (!service || !specialist) {
      notFound();
    }
  }

  if (step === 4) {
    if (!service || !specialist || !paramToString(params.slot)) {
      notFound();
    }
  }

  if (step === 2 && entry === "service") {
    const slug = (await headers()).get(SALON_SLUG_HEADER);
    if (slug === null) {
      return (
        <main className="p-8 text-center text-zinc-600 dark:text-zinc-400">
          <p>The platform is still in development.</p>
        </main>
      );
    }

    const serviceId = Number(service);
    if (Number.isNaN(serviceId)) {
      notFound();
    }

    const { specialists } = await getSpecialistsPage(slug, 1, String(serviceId));

    return (
      <main className="flex flex-col gap-6 p-8">
        <SpecialistSelectionGrid specialists={specialists} serviceId={serviceId} />
      </main>
    );
  }

  if (step === 2) {
    const slug = (await headers()).get(SALON_SLUG_HEADER);
    if (slug === null) {
      return (
        <main className="p-8 text-center text-zinc-600 dark:text-zinc-400">
          <p>The platform is still in development.</p>
        </main>
      );
    }

    const specialistId = Number(specialist);
    if (Number.isNaN(specialistId)) {
      notFound();
    }

    const [specialistDetail, { currency }] = await Promise.all([
      getSpecialistDetailPage(slug, specialistId),
      getSalonInfoPage(slug),
    ]);
    if (specialistDetail === null) {
      notFound();
    }

    return (
      <main className="flex flex-col gap-6 p-8">
        <ServiceSelectionGrid
          services={specialistDetail.services_detail}
          confirmTarget={{ mode: "specialist", specialistId }}
          currency={currency}
        />
      </main>
    );
  }

  if (step === 3) {
    const slug = (await headers()).get(SALON_SLUG_HEADER);
    if (slug === null) {
      return (
        <main className="p-8 text-center text-zinc-600 dark:text-zinc-400">
          <p>The platform is still in development.</p>
        </main>
      );
    }

    // Re-asserts the same non-undefined condition the earlier step-3 guard
    // above already enforces at runtime — needed here only because that
    // guard's type narrowing doesn't survive past its own `if` block (the
    // two branches of that block merge back to the original
    // `string | undefined` type once it ends), not because the rule
    // differs.
    if (service === undefined || specialist === undefined) {
      notFound();
    }

    const dateFrom = paramToString(params.date_from) ?? todayIsoDate();
    const dateTo = addDays(dateFrom, WINDOW_DAYS - 1);

    const { availableTimes } = await getAvailability(
      slug,
      service,
      dateFrom,
      dateTo,
      specialist === "any" ? undefined : specialist,
    );

    return (
      <main className="flex flex-col gap-6 p-8">
        <DateTimeSelectionGrid
          availabilityByDay={groupAvailabilityByDay(availableTimes)}
          dateFrom={dateFrom}
          entry={entry === "service" ? "service" : "specialist"}
          service={service}
          specialist={specialist}
        />
      </main>
    );
  }

  if (step === 4) {
    const slug = (await headers()).get(SALON_SLUG_HEADER);
    if (slug === null) {
      return (
        <main className="p-8 text-center text-zinc-600 dark:text-zinc-400">
          <p>The platform is still in development.</p>
        </main>
      );
    }

    // Re-asserts the same non-undefined condition the earlier step-4 guard
    // above already enforces at runtime — needed here only because that
    // guard's type narrowing doesn't survive past its own `if` block, same
    // reasoning as step 3's own re-check above.
    const slot = paramToString(params.slot);
    if (service === undefined || specialist === undefined || slot === undefined) {
      notFound();
    }

    return (
      <main className="flex flex-col gap-6 p-8">
        <ContactInfoForm
          slug={slug}
          entry={entry === "service" ? "service" : "specialist"}
          service={service}
          specialist={specialist}
          startDatetime={decodeURIComponent(slot)}
        />
      </main>
    );
  }

  return <div data-testid="step-not-implemented" />;
}
