import { beforeEach, describe, expect, it, vi } from "vitest";

import { getServicesPage } from "./getServicesPage";

// Server-only var (docs/DECISIONS.md § Fix: separate server-side API URL for
// Server Components) — deliberately distinct from api/client.ts's
// NEXT_PUBLIC_API_URL (src/lib/api/client.ts:16), since this module runs
// server-side and must reach the backend via the Docker network, not the
// host-published port.
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

describe("getServicesPage", () => {
  it("test_builds_correct_url_with_slug_and_page", async () => {
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ count: 0, next: null, previous: null, results: [] }) as Response,
    );

    await getServicesPage("bella-demo", 2);

    expect(fetchMock).toHaveBeenCalledWith(
      `${API_BASE}/api/v1/salons/bella-demo/services/?page=2`,
    );
  });

  it("test_defaults_to_page_1_when_no_page_given", async () => {
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ count: 0, next: null, previous: null, results: [] }) as Response,
    );

    await getServicesPage("bella-demo");

    // Page 1 omits ?page= entirely — cleaner than a redundant ?page=1, and
    // matches how a plain first-page browse would look.
    expect(fetchMock).toHaveBeenCalledWith(`${API_BASE}/api/v1/salons/bella-demo/services/`);
  });

  it("test_maps_response_to_props_shape", async () => {
    const results = [
      { id: 1, name: "Manicure" },
      { id: 2, name: "Pedicure" },
    ];
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ count: 45, next: "...", previous: null, results }) as Response,
    );

    const page = await getServicesPage("bella-demo", 2);

    expect(page).toEqual({
      services: results,
      currentPage: 2,
      totalPages: Math.ceil(45 / PAGE_SIZE), // 3
    });
  });

  it("test_handles_empty_results", async () => {
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ count: 0, next: null, previous: null, results: [] }) as Response,
    );

    const page = await getServicesPage("bella-demo", 1);

    expect(page.services).toEqual([]);
    expect(page.services).not.toBeNull();
    expect(page.totalPages).toBe(0);
  });

  it("test_propagates_fetch_failure", async () => {
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockResolvedValueOnce(jsonResponse({ error: "boom" }, false) as Response);

    // Contract: a non-2xx response makes the function reject rather than
    // returning an error-shaped value — page.tsx lets it bubble to Next.js's
    // error.tsx boundary rather than needing to check a result discriminant.
    await expect(getServicesPage("bella-demo", 1)).rejects.toThrow();
  });
});
