/**
 * Server-safe data fetch for the /services catalog page (docs/DECISIONS.md
 * § Stage 13 amendment). Pure function: no document.cookie, no import from
 * api/client.ts — safe to call from a Server Component, unlike apiRequest()
 * which is browser-only.
 */

// Must match backend/core/pagination.py's DefaultPagination.page_size (20).
// The DRF list envelope ({count, next, previous, results}) doesn't carry the
// page size itself, so it has to be duplicated here as a constant. A
// mismatch between the two sides miscalculates totalPages silently — no
// runtime error, just a wrong page count — so treat a change to either
// side as a deliberate, paired change.
const PAGE_SIZE = 20;

export interface Service {
  id: number;
  salon: number;
  category: { id: number; name: string };
  name: string;
  duration_minutes: number;
  price: string;
  buffer_minutes: number;
  ordering: number;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

interface DrfPage<T> {
  count: number;
  next: string | null;
  previous: string | null;
  results: T[];
}

export interface ServicesPage {
  services: Service[];
  currentPage: number;
  totalPages: number;
}

export async function getServicesPage(slug: string, page = 1): Promise<ServicesPage> {
  const apiBase = process.env.NEXT_PUBLIC_API_URL;
  const path = `/api/v1/salons/${slug}/services/`;
  const url = page === 1 ? `${apiBase}${path}` : `${apiBase}${path}?page=${page}`;

  const response = await fetch(url);
  if (!response.ok) {
    throw new Error(`Failed to fetch services page ${page} for salon ${slug}`);
  }

  const body = (await response.json()) as DrfPage<Service>;

  return {
    services: body.results,
    currentPage: page,
    totalPages: Math.ceil(body.count / PAGE_SIZE),
  };
}
