"use client";

/**
 * Client Component boundary for the service-selection grid
 * (docs/DECISIONS.md § "Service selection: select-then-confirm interaction
 * pattern"). services/page.tsx stays a Server Component; selection state
 * (which card is chosen) and the "Продовжити" navigation have to live here,
 * since a Server Component can't hold useState or attach an onClick.
 *
 * Unlike ServiceInfoPopover (one client instance per card, no shared
 * state), selection is exclusive across the whole grid, so this component
 * wraps the entire grid + confirm button as a single client boundary,
 * receiving the service list as a prop from the Server Component parent.
 */
import { useRouter } from "next/navigation";
import { useState } from "react";

import { formatPrice } from "@/lib/pricing/formatPrice";

import ServiceInfoPopover from "./ServiceInfoPopover";

interface Service {
  id: number;
  name: string;
  description: string | null;
  duration_minutes: number;
  price: string;
  category?: { id: number; name: string };
}

type ConfirmTarget = { mode: "service" } | { mode: "specialist"; specialistId: number };

interface ServiceSelectionGridProps {
  services: Service[];
  confirmTarget: ConfirmTarget;
  currency: string;
}

function buildConfirmUrl(confirmTarget: ConfirmTarget, selectedId: number): string {
  if (confirmTarget.mode === "service") {
    return `/booking?entry=service&service=${selectedId}`;
  }
  return `/booking?entry=specialist&specialist=${confirmTarget.specialistId}&service=${selectedId}&step=3`;
}

export default function ServiceSelectionGrid({
  services,
  confirmTarget,
  currency,
}: ServiceSelectionGridProps) {
  const router = useRouter();
  const [selectedId, setSelectedId] = useState<number | null>(null);

  function handleConfirm() {
    if (selectedId === null) {
      return;
    }
    router.push(buildConfirmUrl(confirmTarget, selectedId));
  }

  return (
    <div className="flex flex-col gap-6">
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {services.map((service) => (
          <div
            key={service.id}
            className="rounded border border-zinc-200 p-4 hover:border-zinc-400 dark:border-zinc-700 dark:hover:border-zinc-500"
          >
            {/* The <label> wraps only the radio input (still "a <label>
                wrapping a radio input" as intended), not the name/duration/
                price content — the heading and the info button must share
                a parent (test_info_button_renders_inline_with_service_name),
                which isn't possible if the heading sits inside the label
                while the popover stays a sibling outside it. Duration/price
                stay outside the label too, for the same reason: keeping the
                label's contents minimal is what keeps this row's DOM shape
                compatible with that pre-existing test. */}
            <div className="flex items-center gap-2">
              <label className="cursor-pointer">
                <input
                  type="radio"
                  name="service"
                  checked={selectedId === service.id}
                  onChange={() => setSelectedId(service.id)}
                  aria-label={service.name}
                />
              </label>
              <h2 className="font-semibold">{service.name}</h2>

              {/* Sibling of the <label>, not nested inside it — a <button>
                  inside a <label> has its own click-propagation quirks with
                  the label's associated control, the same reason the info
                  button previously stayed a sibling of the stretched <Link>. */}
              <ServiceInfoPopover service={service} currency={currency} />
            </div>
            <p className="text-sm text-zinc-600 dark:text-zinc-400">
              {service.duration_minutes} хв
            </p>
            <p className="text-sm text-zinc-600 dark:text-zinc-400">
              {formatPrice(service.price, currency)}
            </p>
          </div>
        ))}
      </div>

      <button
        type="button"
        disabled={selectedId === null}
        onClick={handleConfirm}
        className="self-start rounded border border-zinc-300 px-4 py-2 font-semibold disabled:cursor-not-allowed disabled:opacity-50 dark:border-zinc-600"
      >
        Продовжити
      </button>
    </div>
  );
}
