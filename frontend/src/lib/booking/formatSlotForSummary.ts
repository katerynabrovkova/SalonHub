/**
 * Formats a booking slot's ISO datetime for the step-4 summary (Stage 15
 * item 5, Cycle D), e.g. "24 вересня, 14:30". Pure string/`Date.UTC`
 * arithmetic only, entirely anchored to the ISO string's own characters --
 * never `new Date(iso)` read back in the local timezone. `iso` already
 * carries the salon's own UTC offset (same shape as `available_times`,
 * DateTimeSelectionGrid.tsx's `slot.slice(11, 16)`), so both the calendar
 * date and the wall-clock time it should display are already sitting in the
 * string itself -- re-deriving either through `Date`'s local-timezone
 * getters would risk showing a different day/hour than what the customer
 * actually picked.
 */

export function formatSlotForSummary(iso: string): string {
  const [year, month, day] = iso.slice(0, 10).split("-").map(Number);
  const time = iso.slice(11, 16);

  const dateText = new Intl.DateTimeFormat("uk-UA", {
    day: "numeric",
    month: "long",
    timeZone: "UTC",
  }).format(new Date(Date.UTC(year, month - 1, day)));

  return `${dateText}, ${time}`;
}
