import { beforeEach, describe, expect, it, vi } from "vitest";

import { getAvailability } from "./getAvailability";

// Server-only var (docs/DECISIONS.md § Fix: separate server-side API URL for
// Server Components) — deliberately distinct from api/client.ts's
// NEXT_PUBLIC_API_URL, since this module runs server-side and must reach the
// backend via the Docker network, not the host-published port. Mirrors
// getServicesPage.test.ts / getSpecialistsPage.test.ts's convention.
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

describe("getAvailability", () => {
  it("test_maps_response_to_result_shape", async () => {
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ available_times: ["2026-08-17T09:00:00+03:00"] }) as Response,
    );

    const result = await getAvailability("bella-demo", "5", "2026-08-17", "2026-08-30");

    expect(result).toEqual({ availableTimes: ["2026-08-17T09:00:00+03:00"] });
  });

  it("test_builds_url_without_specialist_param_when_omitted", async () => {
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockResolvedValueOnce(jsonResponse({ available_times: [] }) as Response);

    await getAvailability("bella-demo", "5", "2026-08-17", "2026-08-30");

    expect(fetchMock).toHaveBeenCalledWith(
      `${API_BASE}/api/v1/salons/bella-demo/availability/?service=5&date_from=2026-08-17&date_to=2026-08-30`,
    );
  });

  it("test_builds_url_with_specialist_param_when_provided", async () => {
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockResolvedValueOnce(jsonResponse({ available_times: [] }) as Response);

    await getAvailability("bella-demo", "5", "2026-08-17", "2026-08-30", "12");

    expect(fetchMock).toHaveBeenCalledWith(
      `${API_BASE}/api/v1/salons/bella-demo/availability/?service=5&date_from=2026-08-17&date_to=2026-08-30&specialist=12`,
    );
  });

  it("test_propagates_fetch_failure", async () => {
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockResolvedValueOnce(jsonResponse({ error: "boom" }, false) as Response);

    // Contract: a non-2xx response makes the function reject rather than
    // returning an error-shaped value — same pattern as getServicesPage.ts /
    // getSpecialistsPage.ts / getReviews.ts.
    await expect(
      getAvailability("bella-demo", "5", "2026-08-17", "2026-08-30"),
    ).rejects.toThrow();
  });
});
