/**
 * Server-safe data fetch for the /specialists/[id] detail page
 * (docs/DECISIONS.md § Stage 14 implementation decisions). Pure function: no
 * document.cookie, no import from api/client.ts — safe to call from a Server
 * Component, unlike apiRequest() which is browser-only. Mirrors
 * getSpecialistsPage.ts's conventions, except a 404 resolves to `null`
 * instead of throwing — the caller uses that to render notFound().
 */

export interface SpecialistDetail {
  id: number;
  salon: number;
  name: string;
  bio: string;
  photo: string | null;
  is_active: boolean;
  services: { id: number; name: string }[];
  average_rating: number | null;
  review_count: number;
  created_at: string;
  updated_at: string;
}

export async function getSpecialistDetailPage(
  slug: string,
  id: number,
): Promise<SpecialistDetail | null> {
  const apiBase = process.env.INTERNAL_API_URL;

  const response = await fetch(`${apiBase}/api/v1/salons/${slug}/specialists/${id}/`);
  if (response.status === 404) {
    return null;
  }
  if (!response.ok) {
    throw new Error(`Failed to fetch specialist ${id} for salon ${slug}`);
  }

  return (await response.json()) as SpecialistDetail;
}
