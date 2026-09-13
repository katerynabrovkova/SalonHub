import { beforeEach, describe, expect, it, vi } from "vitest";

import { getServiceCategories } from "./getServiceCategories";

// Server-only var (docs/DECISIONS.md § Fix: separate server-side API URL for
// Server Components) — same convention as getServicesPage.test.ts.
const API_BASE = "http://backend:8000";

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

describe("getServiceCategories", () => {
  it("test_builds_correct_url_with_page_size_100", async () => {
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ count: 0, next: null, previous: null, results: [] }) as Response,
    );

    await getServiceCategories("bella-demo");

    expect(fetchMock).toHaveBeenCalledWith(
      `${API_BASE}/api/v1/salons/bella-demo/categories/?page_size=100`,
    );
  });

  it("test_returns_parsed_results_array_on_success", async () => {
    const results = [
      { id: 1, name: "Nails", photo: null },
      { id: 2, name: "Hair", photo: "https://example.com/hair.jpg" },
    ];
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ count: 2, next: null, previous: null, results }) as Response,
    );

    const categories = await getServiceCategories("bella-demo");

    expect(categories).toEqual(results);
  });

  it("test_propagates_fetch_failure", async () => {
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockResolvedValueOnce(jsonResponse({ error: "boom" }, false) as Response);

    await expect(getServiceCategories("bella-demo")).rejects.toThrow();
  });

  it("test_throws_when_response_is_an_incomplete_page", async () => {
    // count exceeds results.length -> more categories exist than the single
    // ?page_size=100 request returned. Must throw explicitly rather than
    // silently returning a truncated category list (docs/DECISIONS.md §
    // Stage 13 reopened: implementation decisions for the popup, category
    // fetch, and detail-page removal).
    const results = [{ id: 1, name: "Nails", photo: null }];
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ count: 101, next: "...", previous: null, results }) as Response,
    );

    await expect(getServiceCategories("bella-demo")).rejects.toThrow();
  });
});
