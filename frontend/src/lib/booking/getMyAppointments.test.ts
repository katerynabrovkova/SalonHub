// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from "vitest";

// Mock the api/client.ts function boundary, not raw fetch -- this helper is
// cookie-authenticated (apiRequest), unlike the server-side getXPage.ts
// helpers, which mock global fetch instead (see getReviews.test.ts).
vi.mock("@/lib/api/client", () => ({
  apiRequest: vi.fn(),
}));

// Imported after the mock above so the mocked module is what the helper sees.
import { apiRequest } from "@/lib/api/client";
import { getMyAppointments, type MyAppointment } from "./getMyAppointments";

const mockedApiRequest = vi.mocked(apiRequest);

beforeEach(() => {
  mockedApiRequest.mockReset();
});

const APPOINTMENT: MyAppointment = {
  id: 1,
  status: "confirmed",
  start_datetime: "2026-10-01T10:00:00Z",
  end_datetime: "2026-10-01T11:00:00Z",
  specialist: { id: 2, name: "Jane" },
  service: { id: 3, name: "Manicure" },
  cancelled_at: null,
  cancelled_by: null,
  cancellation_reason: "",
  service_price_at_booking: "500.00",
  deposit_percentage_at_booking: "20.00",
  payment_status: "succeeded",
  payment_amount: "100.00",
  amount_due_at_visit: "400.00",
};

describe("getMyAppointments", () => {
  it("test_fetches_the_first_page_and_returns_its_results", async () => {
    mockedApiRequest.mockResolvedValueOnce({
      count: 1,
      next: null,
      previous: null,
      results: [APPOINTMENT],
    });

    const appointments = await getMyAppointments("bella-demo");

    expect(appointments).toEqual([APPOINTMENT]);
    expect(mockedApiRequest).toHaveBeenCalledWith("bella-demo", "/appointments/mine/");
  });

  it("test_returns_an_empty_array_when_there_are_no_appointments", async () => {
    mockedApiRequest.mockResolvedValueOnce({
      count: 0,
      next: null,
      previous: null,
      results: [],
    });

    const appointments = await getMyAppointments("bella-demo");

    expect(appointments).toEqual([]);
  });

  it("test_propagates_a_rejection_from_apiRequest", async () => {
    mockedApiRequest.mockRejectedValueOnce(new Error("network down"));

    await expect(getMyAppointments("bella-demo")).rejects.toThrow("network down");
  });
});
