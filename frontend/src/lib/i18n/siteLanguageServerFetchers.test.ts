import { beforeEach, describe, expect, it, vi } from "vitest";

// docs/DECISIONS.md § Frontend › "Site language" and "API client": every
// server-side fetcher builds its INTERNAL_API_URL request through one shared
// helper that always adds the site language as `lang=`. The language module
// is mocked to "en", not the real "uk", so each test proves the code reads
// the module rather than its own "uk" literal; the helper is wrapped in a
// spy (real behaviour kept) so each test also proves the fetcher goes
// through it rather than adding `lang` on its own.
vi.mock("@/lib/i18n/siteLanguage", () => ({
  getSiteLanguage: () => "en",
}));

vi.mock("@/lib/api/internalApiUrl", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api/internalApiUrl")>();
  return { internalApiUrl: vi.fn(actual.internalApiUrl) };
});

// Imported after the mocks above so the mocked modules are what the code sees.
import { getServiceCategories } from "@/lib/catalog/getServiceCategories";
import { getServiceDetailPage } from "@/lib/catalog/getServiceDetailPage";
import { getServicesPage } from "@/lib/catalog/getServicesPage";
import { getReviews } from "@/lib/reviews/getReviews";
import { getAvailability } from "@/lib/scheduling/getAvailability";
import { getSpecialistDetailPage } from "@/lib/specialists/getSpecialistDetailPage";
import { getSpecialistsPage } from "@/lib/specialists/getSpecialistsPage";
import { getSalonInfoPage } from "@/lib/tenants/getSalonInfoPage";

const API_BASE = "http://backend:8000";
const EMPTY_PAGE = { count: 0, next: null, previous: null, results: [] };

function jsonResponse(body: unknown): Response {
  return { ok: true, status: 200, json: async () => body } as Response;
}

/** The single fetched URL as origin, path and sorted query pairs. */
function fetchedUrl(): { origin: string; pathname: string; query: [string, string][] } {
  const calls = vi.mocked(fetch).mock.calls;
  expect(calls).toHaveLength(1);
  const url = new URL(String(calls[0][0]));
  return {
    origin: url.origin,
    pathname: url.pathname,
    query: [...url.searchParams.entries()].sort(([a], [b]) => a.localeCompare(b)),
  };
}

/**
 * The fetcher built its URL through the shared helper. Imported per test,
 * not at the top, so a missing helper fails each test here rather than the
 * whole file at load.
 */
async function expectBuiltThroughSharedHelper(): Promise<void> {
  const { internalApiUrl } = await import("@/lib/api/internalApiUrl");
  expect(internalApiUrl).toHaveBeenCalled();
}

beforeEach(() => {
  process.env.INTERNAL_API_URL = API_BASE;
  vi.stubGlobal("fetch", vi.fn());
  vi.clearAllMocks();
});

describe("site language in the server-side fetchers", () => {
  it("test_get_service_categories_sends_the_site_language_with_its_page_size", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(EMPTY_PAGE));

    await getServiceCategories("bella-demo");

    expect(fetchedUrl()).toEqual({
      origin: API_BASE,
      pathname: "/api/v1/salons/bella-demo/categories/",
      query: [
        ["lang", "en"],
        ["page_size", "100"],
      ],
    });
    await expectBuiltThroughSharedHelper();
  });

  it("test_get_services_page_sends_the_site_language_with_page_and_category", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(EMPTY_PAGE));

    await getServicesPage("bella-demo", 2, "3");

    expect(fetchedUrl()).toEqual({
      origin: API_BASE,
      pathname: "/api/v1/salons/bella-demo/services/",
      query: [
        ["category", "3"],
        ["lang", "en"],
        ["page", "2"],
      ],
    });
    await expectBuiltThroughSharedHelper();
  });

  it("test_get_service_detail_page_sends_the_site_language", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(
      jsonResponse({ id: 5, name: "Manicure", duration_minutes: 60 }),
    );

    await getServiceDetailPage("bella-demo", 5);

    expect(fetchedUrl()).toEqual({
      origin: API_BASE,
      pathname: "/api/v1/salons/bella-demo/services/5/",
      query: [["lang", "en"]],
    });
    await expectBuiltThroughSharedHelper();
  });

  it("test_get_specialists_page_sends_the_site_language_with_page_and_service", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(EMPTY_PAGE));

    await getSpecialistsPage("bella-demo", 2, "5");

    expect(fetchedUrl()).toEqual({
      origin: API_BASE,
      pathname: "/api/v1/salons/bella-demo/specialists/",
      query: [
        ["lang", "en"],
        ["page", "2"],
        ["service", "5"],
      ],
    });
    await expectBuiltThroughSharedHelper();
  });

  it("test_get_specialist_detail_page_sends_the_site_language", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse({ id: 7, name: "Yana" }));

    await getSpecialistDetailPage("bella-demo", 7);

    expect(fetchedUrl()).toEqual({
      origin: API_BASE,
      pathname: "/api/v1/salons/bella-demo/specialists/7/",
      query: [["lang", "en"]],
    });
    await expectBuiltThroughSharedHelper();
  });

  it("test_get_reviews_sends_the_site_language", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse([]));

    await getReviews("bella-demo");

    expect(fetchedUrl()).toEqual({
      origin: API_BASE,
      pathname: "/api/v1/salons/bella-demo/reviews/",
      query: [["lang", "en"]],
    });
    await expectBuiltThroughSharedHelper();
  });

  it("test_get_salon_info_page_always_sends_the_site_language", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(
      jsonResponse({ name: "Bella", currency: "UAH", assistant_enabled: true, assistant_starters: [] }),
    );

    await getSalonInfoPage("bella-demo");

    expect(fetchedUrl()).toEqual({
      origin: API_BASE,
      pathname: "/api/v1/salons/bella-demo/",
      query: [["lang", "en"]],
    });
    await expectBuiltThroughSharedHelper();
  });

  it("test_get_availability_sends_the_site_language_with_its_range_and_specialist", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse({ available_times: [] }));

    await getAvailability("bella-demo", "5", "2026-08-17", "2026-08-30", "12");

    expect(fetchedUrl()).toEqual({
      origin: API_BASE,
      pathname: "/api/v1/salons/bella-demo/availability/",
      query: [
        ["date_from", "2026-08-17"],
        ["date_to", "2026-08-30"],
        ["lang", "en"],
        ["service", "5"],
        ["specialist", "12"],
      ],
    });
    await expectBuiltThroughSharedHelper();
  });
});
