/**
 * Server-safe data fetch for a salon's public info (docs/DECISIONS.md §
 * "Resolution: frontend price display shows no currency unit", part (B)/(C)).
 * Pure function: no document.cookie, no import from api/client.ts — safe to
 * call from a Server Component, unlike apiRequest() which is browser-only.
 * Mirrors getServicesPage.ts's fetch/error conventions, minus pagination —
 * this endpoint returns a single object, not a DRF list envelope.
 */

export interface SalonInfo {
  currency: string;
}

export async function getSalonInfoPage(slug: string): Promise<SalonInfo> {
  const apiBase = process.env.INTERNAL_API_URL;
  const url = `${apiBase}/api/v1/salons/${slug}/`;

  const response = await fetch(url);
  if (!response.ok) {
    throw new Error(`Failed to fetch salon info for salon ${slug}`);
  }

  return (await response.json()) as SalonInfo;
}
