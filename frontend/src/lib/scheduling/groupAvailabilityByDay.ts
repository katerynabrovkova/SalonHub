/**
 * Pure grouping of a flat `available_times` list (salon-local ISO datetimes
 * with a UTC offset, as returned by `GET .../availability/`) into per-day
 * buckets (docs/DECISIONS.md § Stage 14 UI decisions: booking step 3
 * (date/time selection), "grouped into per-day buckets client-side (JS)").
 *
 * No fetching, no side effects. A day's key is the ISO string's own date
 * portion (its first 10 characters), not a `Date`-object-then-format round
 * trip: since `available_times` are already salon-local (not UTC), the
 * calendar date is already sitting in the string itself, and re-deriving it
 * via `Date` (which reports in the host's local timezone) would risk
 * reintroducing exactly the UTC/local mismatch this data shape exists to
 * avoid.
 */

export function groupAvailabilityByDay(availableTimes: string[]): Record<string, string[]> {
  const buckets: Record<string, string[]> = {};

  for (const time of availableTimes) {
    const day = time.slice(0, 10);
    (buckets[day] ??= []).push(time);
  }

  for (const times of Object.values(buckets)) {
    times.sort((a, b) => new Date(a).getTime() - new Date(b).getTime());
  }

  return buckets;
}
