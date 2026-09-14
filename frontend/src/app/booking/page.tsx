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

import { getSpecialistDetailPage } from "@/lib/specialists/getSpecialistDetailPage";
import { getSpecialistsPage } from "@/lib/specialists/getSpecialistsPage";
import { SALON_SLUG_HEADER } from "@/middleware";

import ServiceSelectionGrid from "../services/ServiceSelectionGrid";
import SpecialistSelectionGrid from "../specialists/SpecialistSelectionGrid";

interface BookingPageProps {
  searchParams: Promise<{ [key: string]: string | string[] | undefined }>;
}

export default async function BookingPage({ searchParams }: BookingPageProps) {
  const params = await searchParams;

  const entry = params.entry;
  if (entry !== "service" && entry !== "specialist") {
    notFound();
  }

  const service = params.service;
  const specialist = params.specialist;

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

    const specialistDetail = await getSpecialistDetailPage(slug, specialistId);
    if (specialistDetail === null) {
      notFound();
    }

    return (
      <main className="flex flex-col gap-6 p-8">
        <ServiceSelectionGrid
          services={specialistDetail.services_detail}
          confirmTarget={{ mode: "specialist", specialistId }}
        />
      </main>
    );
  }

  if (step === 3) {
    return <div data-testid="step-datetime" />;
  }

  return <div data-testid="step-not-implemented" />;
}
