/**
 * Silent session renewal (docs/DECISIONS.md § "Session renewal and session
 * lifetime", "S2 design details", points 1, 6, 7, 10 and 11). Browser-only.
 * Must not import from ./client.ts (no import cycle; tests mock that module).
 *
 * Module-level state: `flights`, one in-flight renewal per slug. Nothing else.
 */
import { apiBaseUrl, readCookie } from "./browserContext";
import { markSessionLost } from "./sessionHint";

export type RenewalOutcome = "renewed" | "lost" | "unsure";

const REFRESH_TIMEOUT_MS = 10_000;

const flights = new Map<string, Promise<RenewalOutcome>>();

/** Sends the refresh request. Never throws: anything unexpected is "unsure". */
async function requestRenewal(slug: string): Promise<RenewalOutcome> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  try {
    const headers = new Headers();
    const csrfToken = readCookie("csrftoken");
    if (csrfToken !== null) {
      headers.set("X-CSRFToken", csrfToken);
    }

    const controller = new AbortController();
    timer = setTimeout(() => controller.abort(), REFRESH_TIMEOUT_MS);

    const response = await fetch(`${apiBaseUrl()}/api/v1/salons/${slug}/auth/refresh/`, {
      method: "POST",
      headers,
      credentials: "include",
      signal: controller.signal,
    });

    if (response.ok) {
      return "renewed";
    }
    return response.status === 401 ? "lost" : "unsure";
  } catch {
    return "unsure";
  } finally {
    if (timer !== undefined) {
      clearTimeout(timer);
    }
  }
}

/**
 * One refresh request per slug at a time. Never rejects: resolves to
 * "renewed" (2xx), "lost" (401) or "unsure" (anything else, including a
 * network error and the 10 second timeout). Only "lost" deletes the hint and
 * dispatches the session-expired event, once per flight.
 */
export function renewSession(slug: string): Promise<RenewalOutcome> {
  const existing = flights.get(slug);
  if (existing !== undefined) {
    return existing;
  }

  const flight = (async (): Promise<RenewalOutcome> => {
    try {
      const outcome = await requestRenewal(slug);
      if (outcome === "lost") {
        markSessionLost();
      }
      return outcome;
    } finally {
      flights.delete(slug);
    }
  })();
  flights.set(slug, flight);
  return flight;
}

/** Resolves when no renewal is in flight for `slug`. */
export async function whenRenewalIdle(slug: string): Promise<void> {
  let flight = flights.get(slug);
  while (flight !== undefined) {
    const awaited = flight;
    await awaited;
    const next = flights.get(slug);
    if (next === awaited) {
      // Still registered after settling: cleanup didn't run (or won't).
      // Looping again on the same settled promise would spin forever.
      return;
    }
    flight = next;
  }
}
