/**
 * Formats an appointment time for display in the salon's own wall time
 * (docs/DECISIONS.md § "Appointment times in the salon's time zone", point
 * 2), e.g. "15 лип. 2026 р., 12:15". `iso` carries the salon's offset (e.g.
 * "2026-07-15T12:15:00+03:00"), so the calendar date and the wall-clock time
 * to show are already in the string itself: both are read from its
 * characters, never from `new Date(iso)`'s local getters, which would give
 * the browser's zone instead of the salon's. Same approach as
 * formatSlotForSummary.
 *
 * The wall time is built as a UTC instant and formatted with
 * `timeZone: "UTC"`, which makes `Intl` print exactly those fields whatever
 * the browser's zone. The format, with the year, is the one the dashboard
 * used before (`dateStyle: "medium"`, `timeStyle: "short"`).
 */
export function formatSalonDateTime(iso: string): string {
  const [year, month, day] = iso.slice(0, 10).split("-").map(Number);
  const [hour, minute] = iso.slice(11, 16).split(":").map(Number);

  return new Intl.DateTimeFormat("uk-UA", {
    dateStyle: "medium",
    timeStyle: "short",
    timeZone: "UTC",
  }).format(new Date(Date.UTC(year, month - 1, day, hour, minute)));
}
