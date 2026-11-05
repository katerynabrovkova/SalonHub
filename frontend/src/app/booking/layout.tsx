"use client";

/**
 * Client layout for the /booking route (docs/DECISIONS.md § Stage 14
 * implementation decisions: step 4 contact-info form). Its only job is to
 * wrap `{children}` in BookingContactInfoProvider — `children` is the
 * already-rendered Server Component tree Next.js passes in (booking/page.tsx
 * and everything it returns), never imported or rendered directly here, so
 * page.tsx's own server-side data fetching (getAvailability,
 * getSpecialistsPage, ...) is untouched.
 *
 * No <html>/<body> here — those belong to the root layout
 * (frontend/src/app/layout.tsx) only; a nested layout composes inside it.
 *
 * While a navigation's pending state is visible (17.6d, docs/DECISIONS.md §
 * "Stage 17 sub-step order"; after the same 150 ms delay as the progress
 * bar), the step is wrapped with `inert`, `aria-busy="true"` and a dim
 * style, so the previous step cannot be used while the next one loads. The
 * wrapper is always rendered, so toggling it never remounts the provider
 * or the step.
 */

import { useSyncExternalStore, type ReactNode } from "react";

import { isVisible, subscribe } from "@/lib/navigation/pendingNavigation";

import { BookingContactInfoProvider } from "./BookingContactInfoContext";

function getServerSnapshot(): boolean {
  return false;
}

export default function BookingLayout({ children }: { children: ReactNode }) {
  const busy = useSyncExternalStore(subscribe, isVisible, getServerSnapshot);
  return (
    <BookingContactInfoProvider>
      <div
        inert={busy}
        aria-busy={busy ? "true" : undefined}
        className={`transition-opacity duration-150 motion-reduce:transition-none${busy ? " opacity-50" : ""}`}
      >
        {children}
      </div>
    </BookingContactInfoProvider>
  );
}
