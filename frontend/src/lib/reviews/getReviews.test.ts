import { beforeEach, describe, expect, it, vi } from "vitest";

import { getReviews } from "./getReviews";

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

describe("getReviews", () => {
  it("test_flattens_grouped_response_into_flat_array", async () => {
    const grouped = [
      {
        specialist: { id: 1, name: "Jane" },
        reviews: [
          {
            id: 101,
            rating: 5,
            text: "Great work",
            created_at: "2026-06-03T12:00:00Z",
            service: { id: 10, name: "Manicure" },
          },
        ],
      },
    ];
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockResolvedValueOnce(jsonResponse(grouped) as Response);

    const reviews = await getReviews("bella-demo");

    expect(reviews).toEqual([
      {
        id: 101,
        rating: 5,
        text: "Great work",
        created_at: "2026-06-03T12:00:00Z",
        specialist: { id: 1, name: "Jane" },
        service: { id: 10, name: "Manicure" },
      },
    ]);
  });

  it("test_sorts_flat_array_by_created_at_descending_across_specialists", async () => {
    // Specialist A (2 reviews, both older) is listed first in the raw
    // response — the backend groups by review count desc, so A outranks B
    // there even though B's single review is the most recent overall. The
    // flattened, chronologically-sorted output must not inherit that group
    // order: B's newer review belongs before both of A's.
    const grouped = [
      {
        specialist: { id: 1, name: "Jane" },
        reviews: [
          {
            id: 101,
            rating: 5,
            text: "Older #1",
            created_at: "2026-06-01T10:00:00Z",
            service: { id: 10, name: "Manicure" },
          },
          {
            id: 102,
            rating: 4,
            text: "Older #2",
            created_at: "2026-05-01T10:00:00Z",
            service: { id: 10, name: "Manicure" },
          },
        ],
      },
      {
        specialist: { id: 2, name: "Zoe" },
        reviews: [
          {
            id: 201,
            rating: 5,
            text: "Newest",
            created_at: "2026-06-05T10:00:00Z",
            service: { id: 11, name: "Pedicure" },
          },
        ],
      },
    ];
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockResolvedValueOnce(jsonResponse(grouped) as Response);

    const reviews = await getReviews("bella-demo");

    expect(reviews.map((r) => r.id)).toEqual([201, 101, 102]);
  });

  it("test_handles_empty_salon", async () => {
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockResolvedValueOnce(jsonResponse([]) as Response);

    const reviews = await getReviews("bella-demo");

    expect(reviews).toEqual([]);
  });

  it("test_non_ok_response_throws", async () => {
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockResolvedValueOnce(jsonResponse({ error: "boom" }, false) as Response);

    // Contract: a non-2xx response makes the function reject rather than
    // returning an error-shaped value — same pattern as getServicesPage.ts /
    // getSpecialistsPage.ts, so page.tsx can let it bubble to Next.js's
    // error.tsx boundary rather than needing to check a result discriminant.
    await expect(getReviews("bella-demo")).rejects.toThrow();
  });
});
