/**
 * Presentational booking-step-4 summary (Stage 15 item 5, Cycle D): service,
 * specialist, and slot, shown above either the guest or account form. No
 * hooks, no fetching -- the caller (booking/page.tsx's step-4 branch) already
 * resolved the service/specialist names and passes them down as plain props,
 * mirroring how DateTimeSelectionGrid/ContactInfoForm receive already-fetched
 * data rather than fetching their own.
 */
import { formatSlotForSummary } from "@/lib/booking/formatSlotForSummary";

interface BookingSummaryProps {
  serviceName: string;
  /** `null` for the "any specialist" case (docs/DECISIONS.md § Stage 14
   * implementation decisions, "'Any specialist' is encoded as the literal
   * URL value specialist=any"). */
  specialistName: string | null;
  /** Already-decoded ISO datetime with the salon's own UTC offset, same
   * shape as `startDatetime` passed into ContactInfoForm. */
  slot: string;
}

interface SummaryRowProps {
  label: string;
  value: string;
}

function SummaryRow({ label, value }: SummaryRowProps) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-sm text-zinc-500 dark:text-zinc-400">{label}</span>
      <span>{value}</span>
    </div>
  );
}

export default function BookingSummary({ serviceName, specialistName, slot }: BookingSummaryProps) {
  return (
    <section className="flex flex-col gap-3 rounded border border-zinc-200 p-4 dark:border-zinc-700">
      <SummaryRow label="Послуга" value={serviceName} />
      <SummaryRow label="Спеціаліст" value={specialistName ?? "Будь-який спеціаліст"} />
      <SummaryRow label="Дата і час" value={formatSlotForSummary(slot)} />
    </section>
  );
}
