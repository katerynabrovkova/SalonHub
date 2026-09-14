"use client";

/**
 * Client Component for booking step 3, date/time selection
 * (docs/DECISIONS.md § "Stage 14 UI decisions: booking step 3 (date/time
 * selection)"). Mirrors services/ServiceSelectionGrid.tsx's and
 * specialists/SpecialistSelectionGrid.tsx's shape: selection lives in local
 * state, navigation happens via a plain template-string URL, and the
 * component does not fetch — `availabilityByDay` and `dateFrom` are the
 * already-server-fetched, already-grouped (groupAvailabilityByDay) props for
 * the currently loaded 14-day window.
 *
 * Unlike those two grids, this one has two navigation targets, not one:
 * selecting a time slot advances to step 4 (`handleSelectSlot`, a
 * `router.push`, same mechanics as the other grids' confirm buttons); "load
 * next window" is a plain `<Link>` doing a full server re-navigation
 * (mirroring services/page.tsx's `?page=N` pagination), not a client fetch
 * — per today's "Data fetching" clarification, since getAvailability.ts is
 * server-only and this endpoint needs no client auth model to justify one.
 *
 * Days with no available times render disabled, with no reason shown (the
 * API returns absence, not a reason code) — the full 14-day range is
 * generated from `dateFrom` here, rather than only rendering
 * `Object.keys(availabilityByDay)`, precisely because a day with zero slots
 * is absent from that object entirely and still needs a (disabled) place in
 * the strip.
 */
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

const WINDOW_DAYS = 14;

interface DateTimeSelectionGridProps {
  availabilityByDay: Record<string, string[]>;
  /** YYYY-MM-DD, the first day of the currently loaded window. */
  dateFrom: string;
  entry: "service" | "specialist";
  /** Service id as it already appears in the URL. */
  service: string;
  /** Specialist id, or "any", as it already appears in the URL. */
  specialist: string;
}

/**
 * Pure calendar-date arithmetic, entirely in UTC (`Date.UTC` /
 * `toISOString`) — `dateFrom` is a plain YYYY-MM-DD with no time or offset
 * of its own, so anchoring the arithmetic to UTC (rather than the host
 * machine's local timezone, which is what bare `new Date(dateFrom)` methods
 * like `getDate()`/`toLocaleDateString()` would use) is what keeps this
 * immune to where the code happens to run — same reasoning as
 * groupAvailabilityByDay's string-slicing day key.
 */
function addDays(date: string, days: number): string {
  const [year, month, day] = date.split("-").map(Number);
  return new Date(Date.UTC(year, month - 1, day + days)).toISOString().slice(0, 10);
}

function buildBaseParams(entry: "service" | "specialist", service: string, specialist: string): string {
  return `entry=${entry}&service=${service}&specialist=${specialist}`;
}

function buildNextWindowUrl(
  entry: "service" | "specialist",
  service: string,
  specialist: string,
  dateFrom: string,
): string {
  const nextDateFrom = addDays(dateFrom, WINDOW_DAYS);
  return `/booking?${buildBaseParams(entry, service, specialist)}&step=3&date_from=${nextDateFrom}`;
}

function buildSlotUrl(
  entry: "service" | "specialist",
  service: string,
  specialist: string,
  slot: string,
): string {
  return `/booking?${buildBaseParams(entry, service, specialist)}&step=4&slot=${encodeURIComponent(slot)}`;
}

export default function DateTimeSelectionGrid({
  availabilityByDay,
  dateFrom,
  entry,
  service,
  specialist,
}: DateTimeSelectionGridProps) {
  const router = useRouter();
  const [selectedDay, setSelectedDay] = useState<string | null>(null);

  const days = Array.from({ length: WINDOW_DAYS }, (_, i) => addDays(dateFrom, i));
  const slotsForSelectedDay =
    selectedDay !== null ? (availabilityByDay[selectedDay] ?? []) : [];

  function handleSelectSlot(slot: string) {
    router.push(buildSlotUrl(entry, service, specialist, slot));
  }

  return (
    <div className="flex flex-col gap-6">
      <div className="flex gap-2 overflow-x-auto">
        {days.map((day) => {
          const hasSlots = (availabilityByDay[day]?.length ?? 0) > 0;
          return (
            <button
              key={day}
              type="button"
              disabled={!hasSlots}
              aria-pressed={selectedDay === day}
              onClick={() => setSelectedDay(day)}
              className="shrink-0 rounded border border-zinc-200 px-3 py-2 disabled:cursor-not-allowed disabled:opacity-50 dark:border-zinc-700"
            >
              {day}
            </button>
          );
        })}
      </div>

      {selectedDay !== null && (
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
          {slotsForSelectedDay.map((slot) => (
            <button
              key={slot}
              type="button"
              onClick={() => handleSelectSlot(slot)}
              className="rounded border border-zinc-200 px-3 py-2 dark:border-zinc-700"
            >
              {slot.slice(11, 16)}
            </button>
          ))}
        </div>
      )}

      <Link
        href={buildNextWindowUrl(entry, service, specialist, dateFrom)}
        className="self-start underline"
      >
        Наступні 14 днів →
      </Link>
    </div>
  );
}
