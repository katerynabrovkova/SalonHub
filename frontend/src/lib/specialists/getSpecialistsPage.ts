/**
 * Server-safe data fetch for the /specialists catalog page (docs/DECISIONS.md
 * § Stage 13 amendment "/specialists page: serializer and card scope"). Pure
 * function: no document.cookie, no import from api/client.ts — safe to call
 * from a Server Component, unlike apiRequest() which is browser-only. Mirrors
 * catalog/getServicesPage.ts's shape and conventions.
 */

// Must match backend/core/pagination.py's DefaultPagination.page_size (20).
// The DRF list envelope ({count, next, previous, results}) doesn't carry the
// page size itself, so it has to be duplicated here as a constant. A
// mismatch between the two sides miscalculates totalPages silently — no
// runtime error, just a wrong page count — so treat a change to either
// side as a deliberate, paired change.
const PAGE_SIZE = 20;

export interface SpecialistService {
  id: number;
  name: string;
}

export interface Specialist {
  id: number;
  salon: number;
  name: string;
  bio: string;
  photo: string | null;
  is_active: boolean;
  services: SpecialistService[];
  average_rating: number | null;
  review_count: number;
  created_at: string;
  updated_at: string;
}

interface DrfPage<T> {
  count: number;
  next: string | null;
  previous: string | null;
  results: T[];
}

export interface SpecialistsPage {
  specialists: Specialist[];
  currentPage: number;
  totalPages: number;
}

export async function getSpecialistsPage(slug: string, page = 1): Promise<SpecialistsPage> {
  const apiBase = process.env.INTERNAL_API_URL;
  const path = `/api/v1/salons/${slug}/specialists/`;
  const url = page === 1 ? `${apiBase}${path}` : `${apiBase}${path}?page=${page}`;

  const response = await fetch(url);
  if (!response.ok) {
    throw new Error(`Failed to fetch specialists page ${page} for salon ${slug}`);
  }

  const body = (await response.json()) as DrfPage<Specialist>;

  return {
    specialists: body.results,
    currentPage: page,
    totalPages: Math.ceil(body.count / PAGE_SIZE),
  };
}
