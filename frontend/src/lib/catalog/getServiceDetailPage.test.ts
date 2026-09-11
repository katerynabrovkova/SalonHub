import { beforeEach, describe, expect, it, vi } from "vitest";

import { getServiceDetailPage } from "./getServiceDetailPage";

// Server-only var (docs/DECISIONS.md § Fix: separate server-side API URL for
// Server Components) — same convention as getServicesPage.test.ts.
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
  it("test_happy_path_returns_service_and_specialists", async () => {
    const service = { id: 7, name: "Manicure", price: "500.00" };
    const specialists = [
      { id: 1, name: "Olena" },
      { id: 2, name: "Iryna" },
    ];
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockResolvedValueOnce(jsonResponse(service) as Response);
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ count: 2, next: null, previous: null, results: specialists }) as Response,
    );

    const result = await getServiceDetailPage("bella-demo", 7);

    expect(result).toEqual({ service, specialists });
  });

  it("test_service_not_found_returns_null_and_skips_specialists_fetch", async () => {
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockResolvedValueOnce(jsonResponse({ detail: "Not found." }, 404) as Response);

    const result = await getServiceDetailPage("bella-demo", 999);

    expect(result).toBeNull();
    // No wasted request: the specialists endpoint must never be called once
    // the service itself is confirmed not to exist.
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("test_non_404_error_throws", async () => {
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockResolvedValueOnce(jsonResponse({ error: "boom" }, 500) as Response);

    // Same pattern as getServicesPage.ts: a non-2xx, non-404 response makes
    // the function reject rather than returning an error-shaped value.
    await expect(getServiceDetailPage("bella-demo", 7)).rejects.toThrow();
  });
});
