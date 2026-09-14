"use client";

/**
 * Client Component for booking step 2, `entry=service` (docs/DECISIONS.md §
 * "Stage 14 UI decisions: booking step 2 for entry=service (specialist
 * selection)"). Mirrors services/ServiceSelectionGrid.tsx's select-then-
 * confirm mechanics: selection lives in local state, cards don't navigate on
 * click, and a "Продовжити" button — disabled until something is selected —
 * is the only way to proceed, navigating via a plain template-string URL
 * (same convention as ServiceSelectionGrid's buildConfirmUrl, not
 * URLSearchParams).
 *
 * Does not fetch: `specialists` and `serviceId` (the service already chosen
 * in step 1) are passed in by the caller.
 *
 * "Any specialist" is rendered as its own selectable element above the
 * grid, not a synthetic card inside it (per today's decision). It shares
 * the same native radio `name` as the specialist cards purely for semantic
 * grouping; mutual exclusion is still driven by the single `selected` state
 * value below, not by native radio-group behavior.
 */
import { useRouter } from "next/navigation";
import { useState } from "react";

import type { Specialist } from "@/lib/specialists/getSpecialistsPage";

import SpecialistCard from "./SpecialistCard";

type Selection = number | "any" | null;

interface SpecialistSelectionGridProps {
  specialists: Specialist[];
  serviceId: number;
}

function buildConfirmUrl(serviceId: number, selection: number | "any"): string {
  return `/booking?entry=service&service=${serviceId}&specialist=${selection}&step=3`;
}

export default function SpecialistSelectionGrid({
  specialists,
  serviceId,
}: SpecialistSelectionGridProps) {
  const router = useRouter();
  const [selected, setSelected] = useState<Selection>(null);

  function handleConfirm() {
    if (selected === null) {
      return;
    }
    router.push(buildConfirmUrl(serviceId, selected));
  }

  return (
    <div className="flex flex-col gap-6">
      <label className="flex cursor-pointer items-center gap-2 rounded border border-zinc-200 p-4 hover:border-zinc-400 dark:border-zinc-700 dark:hover:border-zinc-500">
        <input
          type="radio"
          name="specialist"
          checked={selected === "any"}
          onChange={() => setSelected("any")}
          aria-label="Будь-який спеціаліст"
        />
        <span className="font-semibold">Будь-який спеціаліст</span>
      </label>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {specialists.map((specialist) => (
          <label key={specialist.id} className="flex cursor-pointer flex-col gap-2">
            <input
              type="radio"
              name="specialist"
              checked={selected === specialist.id}
              onChange={() => setSelected(specialist.id)}
              aria-label={specialist.name}
              className="self-center"
            />
            <SpecialistCard specialist={specialist} variant="grid" />
          </label>
        ))}
      </div>

      <button
        type="button"
        disabled={selected === null}
        onClick={handleConfirm}
        className="self-start rounded border border-zinc-300 px-4 py-2 font-semibold disabled:cursor-not-allowed disabled:opacity-50 dark:border-zinc-600"
      >
        Продовжити
      </button>
    </div>
  );
}
