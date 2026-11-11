/**
 * Server-safe data fetch for a salon's public info (docs/DECISIONS.md §
 * "Resolution: frontend price display shows no currency unit", part (B)/(C)).
 * Pure function: no document.cookie, no import from api/client.ts — safe to
 * call from a Server Component, unlike apiRequest() which is browser-only.
 * Mirrors getServicesPage.ts's fetch/error conventions, minus pagination —
 * this endpoint returns a single object, not a DRF list envelope.
 */
import { internalApiUrl } from "@/lib/api/internalApiUrl";

export interface SalonInfo {
  // Resolved by the backend for `?lang=` (docs/DECISIONS.md § Stage 11.5
  // "API language contract"); can be "".
  name: string;
  currency: string;
  assistant_enabled: boolean;
  // Resolved by the backend for `?lang=`, same fallback as `name`; empty
  // items are omitted (docs/DECISIONS.md § "Stage 17 sub-step order", 17.7c).
  assistant_starters: string[];
}

/**
 * Always fetched in the site language: `internalApiUrl` adds it
 * (docs/DECISIONS.md § Frontend › "Site language").
 */
export async function getSalonInfoPage(slug: string): Promise<SalonInfo> {
  const response = await fetch(internalApiUrl(slug, "/"));
  if (!response.ok) {
    throw new Error(`Failed to fetch salon info for salon ${slug}`);
  }

  return (await response.json()) as SalonInfo;
}
