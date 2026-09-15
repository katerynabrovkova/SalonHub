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
 */

import type { ReactNode } from "react";

import { BookingContactInfoProvider } from "./BookingContactInfoContext";

export default function BookingLayout({ children }: { children: ReactNode }) {
  return <BookingContactInfoProvider>{children}</BookingContactInfoProvider>;
}
