import { beforeEach, describe, expect, it, vi } from "vitest";

// The language module is mocked to "en", not the real "uk", so the tests
// prove the helper reads the module rather than its own literal.
vi.mock("@/lib/i18n/siteLanguage", () => ({
  getSiteLanguage: () => "en",
}));

import { internalApiUrl } from "./internalApiUrl";

beforeEach(() => {
  process.env.INTERNAL_API_URL = "http://backend:8000";
});

describe("internalApiUrl", () => {
  it("test_builds_the_salon_url_with_only_the_site_language", () => {
    expect(internalApiUrl("bella-demo", "/reviews/")).toBe(
      "http://backend:8000/api/v1/salons/bella-demo/reviews/?lang=en",
    );
  });

  it("test_keeps_params_in_order_encoded_and_adds_the_site_language_last", () => {
    expect(
      internalApiUrl("bella-demo", "/availability/", {
        service: "5",
        date_from: "2026-08-17T09:00:00+03:00",
      }),
    ).toBe(
      "http://backend:8000/api/v1/salons/bella-demo/availability/?service=5&date_from=2026-08-17T09%3A00%3A00%2B03%3A00&lang=en",
    );
  });

  it("test_a_caller_lang_param_never_overrides_the_site_language", () => {
    const url = new URL(internalApiUrl("bella-demo", "/services/", { lang: "uk", page: "2" }));

    expect(url.searchParams.getAll("lang")).toEqual(["en"]);
    expect(url.searchParams.get("page")).toBe("2");
  });
});
