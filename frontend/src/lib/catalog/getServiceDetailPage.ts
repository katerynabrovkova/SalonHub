/**
 * Server-safe data fetch for a single service by id (Stage 15 item 5, Cycle
 * D — the booking step-4 summary). Pure function: no document.cookie, no
 * import from api/client.ts — safe to call from a Server Component, unlike
 * apiRequest() which is browser-only. Mirrors
 * specialists/getSpecialistDetailPage.ts's conventions exactly, including a
 * 404 resolving to `null` instead of throwing, so the caller can decide how
 * to handle "not found" the same way it already does for a specialist.
 *
 * `GET /services/<id>/` (`ServiceDetailView`, `backend/catalog/views.py`)
 * already exists — this re-adds only the frontend helper for it (a helper of
 * this same name existed at this path before Stage 13's `/services/[id]`
 * page was removed; recreated here for the booking-summary use case, not
 * revived for that old page).
 */

export interface ServiceDetail {
  id: number;
  name: string;
  duration_minutes: number;
}

export async function getServiceDetailPage(slug: string, id: number): Promise<ServiceDetail | null> {
  const apiBase = process.env.INTERNAL_API_URL;

  const response = await fetch(`${apiBase}/api/v1/salons/${slug}/services/${id}/`);
  if (response.status === 404) {
    return null;
  }
  if (!response.ok) {
    throw new Error(`Failed to fetch service ${id} for salon ${slug}`);
  }

  return (await response.json()) as ServiceDetail;
}
