/**
 * Guest manage page (docs/DECISIONS.md "Frontend > Guest manage page"): the
 * link in BOOKING_CONFIRMED and APPOINTMENT_REMINDER,
 * `/appointments/<id>/manage#token=...`. Stays a Server Component and resolves
 * `slug` the same way `/booking/pay` does (headers() -> SALON_SLUG_HEADER ->
 * null-slug fallback). The token is in the URL fragment, never sent to the
 * server, so reading it and everything after is the client component's job.
 *
 * `referrer: "no-referrer"`: the page must not leak its URL to anything it
 * loads or links to.
 */
import type { Metadata } from "next";
import { headers } from "next/headers";

import { SALON_SLUG_HEADER } from "@/middleware";

import ManageAppointment from "./ManageAppointment";

export const metadata: Metadata = {
  referrer: "no-referrer",
};

interface ManageAppointmentPageProps {
  params: Promise<{ id: string }>;
}

export default async function ManageAppointmentPage({ params }: ManageAppointmentPageProps) {
  const slug = (await headers()).get(SALON_SLUG_HEADER);
  if (slug === null) {
    return (
      <main className="p-8 text-center text-zinc-600 dark:text-zinc-400">
        <p>Платформа ще в розробці.</p>
      </main>
    );
  }

  const { id } = await params;

  return (
    <main className="flex flex-col gap-6 p-8">
      <ManageAppointment slug={slug} appointmentId={Number(id)} />
    </main>
  );
}
