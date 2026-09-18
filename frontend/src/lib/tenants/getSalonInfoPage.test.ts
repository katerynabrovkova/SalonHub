import { beforeEach, describe, expect, it, vi } from "vitest";

import { getSalonInfoPage } from "./getSalonInfoPage";

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

describe("getSalonInfoPage", () => {
  it("test_fetches_correct_url_and_returns_currency", async () => {
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockResolvedValueOnce(jsonResponse({ currency: "UAH" }) as Response);

    const result = await getSalonInfoPage("bella-demo");

    expect(fetchMock).toHaveBeenCalledWith(`${API_BASE}/api/v1/salons/bella-demo/`);
    expect(result).toEqual({ currency: "UAH" });
  });

  it("test_throws_on_non_ok_response", async () => {
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockResolvedValueOnce(jsonResponse({ error: "boom" }, false) as Response);

    // Mirrors getServicesPage's error contract: a non-2xx response makes the
    // function reject rather than returning an error-shaped value. Unlike
    // getSpecialistDetailPage's null-on-404 convention (a URL-supplied
    // specialist id can legitimately not exist), a 404 here means something
    // is wrong with an already-resolved slug, so there's no special 404 case
    // — any !response.ok throws the same generic Error.
    await expect(getSalonInfoPage("bella-demo")).rejects.toThrow();
  });
});
