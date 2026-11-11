// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from "vitest";

// docs/DECISIONS.md § Frontend › "Site language": the booking requests and
// the assistant API's ?lang= read the site language from
// lib/i18n/siteLanguage.ts. The module is mocked to "en", not the real "uk",
// so each test proves the code reads the module rather than its own "uk"
// literal. The request layer's lang= (apiRequest, guestApiRequest,
// internalApiUrl) is covered by siteLanguageRequests.test.tsx and
// siteLanguageServerFetchers.test.ts.
vi.mock("@/lib/i18n/siteLanguage", () => ({
  getSiteLanguage: () => "en",
}));

vi.mock("@/lib/api/client", () => ({
  apiRequest: vi.fn(),
}));

// Imported after the mocks above so the mocked modules are what the code sees.
import { apiRequest } from "@/lib/api/client";
import { sendAssistantMessage } from "@/lib/assistant/assistantApi";
import { createAccountBooking } from "@/lib/booking/createAccountBooking";
import { createGuestBooking } from "@/lib/booking/createGuestBooking";

const API_BASE = "http://localhost:8000";

const mockedApiRequest = vi.mocked(apiRequest);

const BOOKING = {
  slug: "bella-demo",
  service: "5",
  specialist: "any",
  startDatetime: "2026-08-17T09:00:00+03:00",
};

beforeEach(() => {
  process.env.NEXT_PUBLIC_API_URL = API_BASE;
  vi.stubGlobal("fetch", vi.fn());
  mockedApiRequest.mockReset();
});

describe("site language consumers", () => {
  it("test_guest_booking_body_carries_the_site_language", async () => {
    vi.mocked(fetch).mockResolvedValueOnce({
      ok: true,
      status: 201,
      json: async () => ({
        appointment: {
          id: 1,
          status: "pending_payment",
          start_datetime: BOOKING.startDatetime,
          end_datetime: "2026-08-17T10:15:00+03:00",
        },
        guest_token: "tok-abc",
      }),
    } as Response);

    await createGuestBooking({
      ...BOOKING,
      customerName: "Alice",
      customerEmail: "alice@example.com",
      customerPhone: "+10000000000",
    });

    const [, init] = vi.mocked(fetch).mock.calls[0];
    expect(JSON.parse(init?.body as string)).toMatchObject({ language: "en" });
  });

  it("test_account_booking_body_carries_the_site_language_with_and_without_contact", async () => {
    mockedApiRequest.mockResolvedValue({});

    // Linked Customer: no contact fields.
    await createAccountBooking(BOOKING);
    // No linked Customer yet: name and phone are sent.
    await createAccountBooking({ ...BOOKING, contact: { name: "Bob", phone: "+10000000002" } });

    const bodies = mockedApiRequest.mock.calls.map(
      ([, , options]) =>
        JSON.parse((options as RequestInit).body as string) as Record<string, unknown>,
    );
    expect(bodies).toHaveLength(2);
    expect(bodies[0]).toMatchObject({ language: "en" });
    expect(bodies[1]).toMatchObject({ language: "en", customer_name: "Bob" });
  });

  it("test_assistant_message_url_carries_the_site_language", async () => {
    vi.mocked(fetch).mockResolvedValueOnce({
      ok: true,
      status: 200,
      headers: new Headers(),
      body: null,
    } as Response);

    await sendAssistantMessage("bella-demo", "Hello", null);

    const [url] = vi.mocked(fetch).mock.calls[0];
    expect(url).toBe(`${API_BASE}/api/v1/salons/bella-demo/assistant/messages/?lang=en`);
  });

});
