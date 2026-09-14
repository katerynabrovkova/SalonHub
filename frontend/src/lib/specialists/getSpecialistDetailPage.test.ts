import { beforeEach, describe, expect, it, vi } from "vitest";

import { getSpecialistDetailPage } from "./getSpecialistDetailPage";

// Server-only var (docs/DECISIONS.md § Fix: separate server-side API URL for
// Server Components) — same convention as getSpecialistsPage.test.ts /
// getServicesPage.test.ts.
const API_BASE = "http://backend:8000";

function jsonResponse(body: unknown, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  };
}

beforeEach(() => {
  process.env.INTERNAL_API_URL = API_BASE;
  vi.stubGlobal("fetch", vi.fn());
});

describe("getSpecialistDetailPage", () => {
  it("test_fetches_correct_url", async () => {
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockResolvedValueOnce(jsonResponse({ id: 7, name: "Olena" }) as Response);

    await getSpecialistDetailPage("bella-demo", 7);

    expect(fetchMock).toHaveBeenCalledWith(`${API_BASE}/api/v1/salons/bella-demo/specialists/7/`);
  });

  it("test_returns_parsed_specialist_on_success", async () => {
    const specialist = {
      id: 7,
      salon: 1,
      name: "Olena",
      bio: "Nail artist",
      photo: "https://example.com/photos/olena.jpg",
      is_active: true,
      services: [{ id: 10, name: "Manicure" }],
      average_rating: 4.5,
      review_count: 2,
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-01T00:00:00Z",
    };
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockResolvedValueOnce(jsonResponse(specialist) as Response);

    const result = await getSpecialistDetailPage("bella-demo", 7);

    expect(result).toEqual(specialist);
  });

  it("test_returns_null_on_404", async () => {
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockResolvedValueOnce(jsonResponse({ detail: "Not found." }, 404) as Response);

    const result = await getSpecialistDetailPage("bella-demo", 999);

    expect(result).toBeNull();
  });

  it("test_throws_on_other_non_2xx", async () => {
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockResolvedValueOnce(jsonResponse({ error: "boom" }, 500) as Response);

    // Same pattern as getServicesPage.ts / the deleted getServiceDetailPage.ts:
    // a non-2xx, non-404 response makes the function reject rather than
    // returning an error-shaped value.
    await expect(getSpecialistDetailPage("bella-demo", 7)).rejects.toThrow();
  });
});
