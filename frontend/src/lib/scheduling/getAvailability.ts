/**
 * Server-safe data fetch for the availability time-grid endpoint
 * (docs/DECISIONS.md § Stage 14 UI decisions: booking step 3 (date/time
 * selection)). Pure function: no document.cookie, no import from
 * api/client.ts — safe to call from a Server Component, unlike apiRequest()
 * which is browser-only. Mirrors catalog/getServicesPage.ts's /
 * specialists/getSpecialistsPage.ts's server-only fetch conventions.
 *
 * Unlike those helpers, `GET .../availability/` is not paginated — the
 * response is a bare `{available_times: string[]}` envelope, so there's no
 * DrfPage wrapping or page/totalPages bookkeeping here.
 *
 * `specialist` is optional and omitted from the query string entirely when
 * absent (the "any specialist" case, per backend/scheduling/views.py's
 * AvailabilityView) — never sent as `specialist=any`, which is a
 * booking-flow URL encoding (docs/DECISIONS.md § Stage 14 implementation
 * decisions), not this endpoint's own query-param contract.
 */

export interface AvailabilityResult {
  availableTimes: string[];
}

export async function getAvailability(
  slug: string,
  service: string,
  dateFrom: string,
  dateTo: string,
  specialist?: string,
): Promise<AvailabilityResult> {
  const apiBase = process.env.INTERNAL_API_URL;
  const path = `/api/v1/salons/${slug}/availability/`;
  const params = new URLSearchParams({ service, date_from: dateFrom, date_to: dateTo });
  if (specialist !== undefined) {
    params.set("specialist", specialist);
  }
  const url = `${apiBase}${path}?${params.toString()}`;

  const response = await fetch(url);
  if (!response.ok) {
    throw new Error(`Failed to fetch availability for salon ${slug}`);
  }

  const body = (await response.json()) as { available_times: string[] };

  return { availableTimes: body.available_times };
}
