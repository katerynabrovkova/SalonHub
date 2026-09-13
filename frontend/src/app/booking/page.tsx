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

  const parsedStep = Number(params.step);
  const step = Number.isInteger(parsedStep) && parsedStep >= 1 ? parsedStep : 1;

  if (step === 2) {
    const prerequisite = entry === "service" ? service : specialist;
    if (!prerequisite) {
      notFound();
    }
  }

  if (step === 3) {
    if (!service || !specialist) {
      notFound();
    }
  }

  if (step === 1) {
    return entry === "service" ? (
      <div data-testid="step-service" />
    ) : (
      <div data-testid="step-specialist" />
    );
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
