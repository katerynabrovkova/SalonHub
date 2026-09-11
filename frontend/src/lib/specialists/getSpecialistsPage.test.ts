import { beforeEach, describe, expect, it, vi } from "vitest";

import { getSpecialistsPage } from "./getSpecialistsPage";

// Server-only var (docs/DECISIONS.md § Fix: separate server-side API URL for
// Server Components) — deliberately distinct from api/client.ts's
// NEXT_PUBLIC_API_URL, since this module runs server-side and must reach the
// backend via the Docker network, not the host-published port. Mirrors
// getServicesPage.test.ts's convention.
const API_BASE = "http://backend:8000";

// Mirrors the backend's core.pagination.DefaultPagination.page_size (20,
// backend/core/pagination.py). Not present in the DRF list envelope itself
// ({count, next, previous, results}), so it has to be a hardcoded constant
// on this side too — asserted here, not discovered from the response.
const PAGE_SIZE = 20;

function jsonResponse(body: unknown, ok = true) {
  return {
    ok,
    status: ok ? 200 : 500,
    json: async () => body,
  };
}

beforeEach(() => {
  process.env.INTERNAL_API_URL = API_BASE;
  vi.stubGlobal("fetch", vi.fn());
});

describe("getSpecialistsPage", () => {
  it("test_maps_response_to_props_shape", async () => {
    const results = [
      {
        id: 1,
        salon: 1,
        name: "Jane",
        bio: "Nail artist",
        photo: "https://example.com/photos/jane.jpg",
        is_active: true,
        services: [
          { id: 10, name: "Manicure" },
          { id: 11, name: "Pedicure" },
        ],
        average_rating: 4.5,
        review_count: 2,
        created_at: "2026-01-01T00:00:00Z",
        updated_at: "2026-01-01T00:00:00Z",
      },
    ];
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ count: 1, next: null, previous: null, results }) as Response,
    );

    const page = await getSpecialistsPage("bella-demo", 1);

    expect(page).toEqual({
      specialists: results,
      currentPage: 1,
      totalPages: Math.ceil(1 / PAGE_SIZE), // 1
    });
  });

  it("test_handles_empty_results", async () => {
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ count: 0, next: null, previous: null, results: [] }) as Response,
    );

    const page = await getSpecialistsPage("bella-demo", 1);

    expect(page.specialists).toEqual([]);
    expect(page.specialists).not.toBeNull();
    expect(page.totalPages).toBe(0);
  });

  it("test_propagates_fetch_failure", async () => {
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockResolvedValueOnce(jsonResponse({ error: "boom" }, false) as Response);

    // Contract: a non-2xx response makes the function reject rather than
    // returning an error-shaped value — same as getServicesPage.ts, so
    // page.tsx can let it bubble to Next.js's error.tsx boundary rather than
    // needing to check a result discriminant.
    await expect(getSpecialistsPage("bella-demo", 1)).rejects.toThrow();
  });
});
