/**
 * Server-safe data fetch for the /reviews page (docs/DECISIONS.md § Stage 13
 * amendment "/reviews page: flat feed, not grouped-by-specialist"). Pure
 * function: no document.cookie, no import from api/client.ts — safe to call
 * from a Server Component, unlike apiRequest() which is browser-only. Mirrors
 * catalog/getServicesPage.ts / specialists/getSpecialistsPage.ts's
 * server-only fetch conventions.
 *
 * Unlike those helpers, GET .../reviews/ returns a bare array grouped by
 * specialist ({specialist, reviews}[]), not a DRF pagination envelope — this
 * helper flattens that into a single chronological list and discards the
 * backend's own grouping/ordering entirely (that ordering — review count
 * desc — serves the grouped endpoint contract, not this page).
 */

interface ReviewSpecialist {
  id: number;
  name: string;
}

interface ReviewService {
  id: number;
  name: string;
}

interface RawReview {
  id: number;
  rating: number;
  text: string;
  created_at: string;
  service: ReviewService;
}

interface RawReviewGroup {
  specialist: ReviewSpecialist;
  reviews: RawReview[];
}

export interface FlatReview {
  id: number;
  rating: number;
  text: string;
  created_at: string;
  specialist: ReviewSpecialist;
  service: ReviewService;
}

export async function getReviews(slug: string): Promise<FlatReview[]> {
  const apiBase = process.env.INTERNAL_API_URL;
  const url = `${apiBase}/api/v1/salons/${slug}/reviews/`;

  const response = await fetch(url);
  if (!response.ok) {
    throw new Error(`Failed to fetch reviews for salon ${slug}`);
  }

  const groups = (await response.json()) as RawReviewGroup[];

  const flattened: FlatReview[] = groups.flatMap(({ specialist, reviews }) =>
    reviews.map((review) => ({
      id: review.id,
      rating: review.rating,
      text: review.text,
      created_at: review.created_at,
      specialist,
      service: review.service,
    })),
  );

  return flattened.sort(
    (a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime(),
  );
}
