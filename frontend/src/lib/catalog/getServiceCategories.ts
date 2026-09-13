/**
 * Server-safe data fetch for the /services category list (docs/DECISIONS.md
 * § Stage 13 reopened: implementation decisions for the popup, category
 * fetch, and detail-page removal). Pure function: no document.cookie, no
 * import from api/client.ts — safe to call from a Server Component, unlike
 * apiRequest() which is browser-only. Mirrors getServicesPage.ts's
 * conventions.
 *
 * Requests ?page_size=100 (core.pagination.DefaultPagination.max_page_size)
 * as a single request rather than looping pages like getServicesPage.ts —
 * the response is still the standard DrfPage envelope, just at its maximum
 * page size. If more categories exist than that one page can hold
 * (count > results.length), this throws rather than silently returning a
 * truncated list — the same "raise on incomplete data, never silently
 * return partial results" principle as TenantScopedManager.
 */

const CATEGORIES_PAGE_SIZE = 100;

export interface ServiceCategory {
  id: number;
  name: string;
  photo: string | null;
}

interface DrfPage<T> {
  count: number;
  next: string | null;
  previous: string | null;
  results: T[];
}

export async function getServiceCategories(slug: string): Promise<ServiceCategory[]> {
  const apiBase = process.env.INTERNAL_API_URL;
  const url = `${apiBase}/api/v1/salons/${slug}/categories/?page_size=${CATEGORIES_PAGE_SIZE}`;

  const response = await fetch(url);
  if (!response.ok) {
    throw new Error(`Failed to fetch categories for salon ${slug}`);
  }

  const body = (await response.json()) as DrfPage<ServiceCategory>;

  if (body.count > body.results.length) {
    throw new Error(
      `Incomplete category page for salon ${slug}: count=${body.count} exceeds ` +
        `results.length=${body.results.length} at page_size=${CATEGORIES_PAGE_SIZE}`,
    );
  }

  return body.results;
}
