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
import { notFound } from "next/navigation";

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

  if (step === 2) {
    return entry === "service" ? (
      <div data-testid="step-specialist" />
    ) : (
      <div data-testid="step-service" />
    );
  }

  if (step === 3) {
    return <div data-testid="step-datetime" />;
  }

  return <div data-testid="step-not-implemented" />;
}
