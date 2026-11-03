/**
 * Server-safe data fetch for a salon's public info (docs/DECISIONS.md §
 * "Resolution: frontend price display shows no currency unit", part (B)/(C)).
 * Pure function: no document.cookie, no import from api/client.ts — safe to
 * call from a Server Component, unlike apiRequest() which is browser-only.
 * Mirrors getServicesPage.ts's fetch/error conventions, minus pagination —
 * this endpoint returns a single object, not a DRF list envelope.
 */

export interface SalonInfo {
  // Resolved by the backend for `?lang=` (docs/DECISIONS.md § Stage 11.5
  // "API language contract"); can be "".
  name: string;
  currency: string;
  assistant_enabled: boolean;
}

/**
 * `lang` is sent as `?lang=` only when given (docs/DECISIONS.md § "Stage 17
 * sub-step order", 17.3b); without it the URL is unchanged, so the existing
 * callers keep the backend's default language resolution.
 */
export async function getSalonInfoPage(slug: string, lang?: string): Promise<SalonInfo> {
  const apiBase = process.env.INTERNAL_API_URL;
  const base = `${apiBase}/api/v1/salons/${slug}/`;
  const url = lang === undefined ? base : `${base}?lang=${encodeURIComponent(lang)}`;

  const response = await fetch(url);
  if (!response.ok) {
    throw new Error(`Failed to fetch salon info for salon ${slug}`);
  }

  return (await response.json()) as SalonInfo;
}
