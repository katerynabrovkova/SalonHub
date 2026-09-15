"use client";

/**
 * Contact-info state container for the guest booking flow's step 4
 * (docs/DECISIONS.md § Stage 14 implementation decisions: step 4
 * contact-info form). In-memory React state only — never written to the
 * URL, localStorage, or sessionStorage (docs/DECISIONS.md § Stage 14
 * planning, "Client-side flow state is split by sensitivity").
 *
 * Lives in `layout.tsx`'s Provider, not a step component's own `useState`,
 * specifically so it survives the "return to step 3 on slot conflict, then
 * forward to step 4 again" round trip: `booking/page.tsx` is a Server
 * Component, so each step's page render is an independent instance with no
 * state of its own — `layout.tsx` is the one part of the App Router tree
 * that persists across page-level navigations within `/booking`.
 *
 * Exposed field names are camelCase (`customerName`/`customerEmail`/
 * `customerPhone`), matching this codebase's existing convention for
 * parsed/exposed values (e.g. getAvailability.ts's `availableTimes` for the
 * backend's `available_times`) rather than mirroring the backend
 * serializer's snake_case field names verbatim. Mapping to
 * `customer_name`/`customer_email`/`customer_phone` for the `POST
 * bookings/` request body happens wherever that call is built — not here.
 */

import { createContext, useContext, useState, type ReactNode } from "react";

interface BookingContactInfoValue {
  customerName: string;
  setCustomerName: (value: string) => void;
  customerEmail: string;
  setCustomerEmail: (value: string) => void;
  customerPhone: string;
  setCustomerPhone: (value: string) => void;
}

const BookingContactInfoContext = createContext<BookingContactInfoValue | null>(null);

export function BookingContactInfoProvider({ children }: { children: ReactNode }) {
  const [customerName, setCustomerName] = useState("");
  const [customerEmail, setCustomerEmail] = useState("");
  const [customerPhone, setCustomerPhone] = useState("");

  return (
    <BookingContactInfoContext.Provider
      value={{
        customerName,
        setCustomerName,
        customerEmail,
        setCustomerEmail,
        customerPhone,
        setCustomerPhone,
      }}
    >
      {children}
    </BookingContactInfoContext.Provider>
  );
}

/** Throws outside a BookingContactInfoProvider — every /booking route is
 * wrapped by layout.tsx, so an out-of-tree usage is a real bug, not a case
 * to silently tolerate with a default value. */
export function useBookingContactInfo(): BookingContactInfoValue {
  const value = useContext(BookingContactInfoContext);
  if (value === null) {
    throw new Error("useBookingContactInfo must be used within a BookingContactInfoProvider");
  }
  return value;
}
