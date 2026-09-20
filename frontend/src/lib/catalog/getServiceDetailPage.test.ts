import { beforeEach, describe, expect, it, vi } from "vitest";

import { getServiceDetailPage } from "./getServiceDetailPage";

// Server-only var (docs/DECISIONS.md § Fix: separate server-side API URL for
// Server Components) — same convention as getSpecialistDetailPage.test.ts.
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

describe("getServiceDetailPage", () => {
  it("test_returns_the_service_and_calls_the_correct_url", async () => {
    const service = { id: 5, name: "Manicure", duration_minutes: 60 };
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockResolvedValueOnce(jsonResponse(service) as Response);

    const result = await getServiceDetailPage("bella-demo", 5);

    expect(fetchMock).toHaveBeenCalledWith(`${API_BASE}/api/v1/salons/bella-demo/services/5/`);
    expect(result).toEqual(service);
  });

  it("test_returns_null_on_404", async () => {
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockResolvedValueOnce(jsonResponse({ detail: "Not found." }, 404) as Response);

    const result = await getServiceDetailPage("bella-demo", 999);

    expect(result).toBeNull();
  });

  it("test_throws_on_other_non_2xx", async () => {
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockResolvedValueOnce(jsonResponse({ error: "boom" }, 500) as Response);

    // Same pattern as getSpecialistDetailPage.ts: a non-2xx, non-404
    // response makes the function reject rather than returning an
    // error-shaped value. Matched on the message, not just "rejects" --
    // `.rejects.toThrow()` with no argument accepts any error, including one
    // thrown by a stub that never reached the real 500-vs-404 branch at all.
    await expect(getServiceDetailPage("bella-demo", 5)).rejects.toThrow(/Failed to fetch service/);
  });
});
