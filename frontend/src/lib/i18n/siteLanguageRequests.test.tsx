// @vitest-environment jsdom
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// docs/DECISIONS.md § Frontend › "Site language" and "API client": the
// browser request layer (apiRequest, guestApiRequest) adds the site
// language as `lang=` to every request URL, so no page can forget it. The
// module is mocked to "en", not the real "uk", so each test proves the code
// reads the module rather than its own "uk" literal.
// browserContext.ts reads NEXT_PUBLIC_API_PORT once at module load, so it
// is set before any import runs.
vi.hoisted(() => {
  process.env.NEXT_PUBLIC_API_PORT = "8000";
});

vi.mock("@/lib/i18n/siteLanguage", () => ({
  getSiteLanguage: () => "en",
}));

vi.mock("@/lib/api/renewSession", () => ({ renewSession: vi.fn() }));

// Imported after the mocks above so the mocked modules are what the code sees.
import ManageAppointment from "@/app/appointments/[id]/manage/ManageAppointment";
import { apiRequest } from "@/lib/api/client";
import { guestApiRequest } from "@/lib/api/guestClient";

const PUBLIC_API_BASE = "http://localhost:8000";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

/** The query of a fetched URL as sorted [key, value] pairs, plus its path. */
function parsed(url: unknown): { pathname: string; query: [string, string][] } {
  const parsedUrl = new URL(String(url));
  return {
    pathname: parsedUrl.pathname,
    query: [...parsedUrl.searchParams.entries()].sort(([a], [b]) => a.localeCompare(b)),
  };
}

function fetchedUrls(): string[] {
  return vi.mocked(fetch).mock.calls.map(([url]) => String(url));
}

beforeEach(() => {
  process.env.NEXT_PUBLIC_API_URL = PUBLIC_API_BASE;
  vi.stubGlobal("fetch", vi.fn());
  window.sessionStorage.clear();
  window.history.replaceState(null, "", "/");
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("site language in the browser request layer", () => {
  it("test_api_request_adds_the_site_language_to_a_path_without_a_query", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse({ results: [] }));

    await apiRequest("bella-demo", "/appointments/mine/");

    const [url] = fetchedUrls();
    expect(url).toBe("http://localhost:8000/api/v1/salons/bella-demo/appointments/mine/?lang=en");
  });

  it("test_api_request_merges_the_site_language_with_an_existing_query", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse({ results: [] }));

    await apiRequest("bella-demo", "/appointments/mine/?page=2");

    const [url] = fetchedUrls();
    expect(url.split("?")).toHaveLength(2);
    expect(parsed(url)).toEqual({
      pathname: "/api/v1/salons/bella-demo/appointments/mine/",
      query: [
        ["lang", "en"],
        ["page", "2"],
      ],
    });
  });

  it("test_guest_api_request_adds_the_site_language", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse({ id: 1 }));

    await guestApiRequest("bella-demo", "/guest/appointments/1/", "tok-abc");

    const [url] = fetchedUrls();
    expect(url).toBe(`${PUBLIC_API_BASE}/api/v1/salons/bella-demo/guest/appointments/1/?lang=en`);
  });

  it("test_guest_manage_page_detail_and_cancel_both_carry_the_site_language", async () => {
    const detail = {
      id: 42,
      status: "confirmed",
      start_datetime: "2026-11-14T14:00:00+02:00",
      end_datetime: "2026-11-14T15:30:00+02:00",
      specialist: { id: 7, name: "Yana" },
      service: { id: 3, name: "Manicure" },
      cancelled_at: null,
      cancelled_by: null,
      cancellation_reason: "",
      service_price_at_booking: "120.00",
      deposit_amount: null,
      amount_due_at_visit: "120.00",
      currency: "UAH",
      deposit_outcome: null,
      refund_deadline: null,
    };
    vi.mocked(fetch)
      .mockResolvedValueOnce(jsonResponse(detail))
      .mockResolvedValueOnce(
        jsonResponse({
          ...detail,
          status: "cancelled",
          cancelled_at: "2026-11-11T10:00:00+02:00",
          cancelled_by: "customer",
        }),
      );
    vi.spyOn(window, "confirm").mockReturnValue(true);
    window.history.replaceState(null, "", "/appointments/42/manage#token=tok-abc");

    render(<ManageAppointment slug="bella-demo" appointmentId={42} />);
    fireEvent.click(await screen.findByRole("button", { name: "Скасувати запис" }));
    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(2));

    expect(fetchedUrls().map(parsed)).toEqual([
      { pathname: "/api/v1/salons/bella-demo/guest/appointments/42/", query: [["lang", "en"]] },
      {
        pathname: "/api/v1/salons/bella-demo/guest/appointments/42/cancel/",
        query: [["lang", "en"]],
      },
    ]);
  });
});
