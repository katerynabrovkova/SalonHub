// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api/client", () => ({
  apiRequest: vi.fn(),
}));

import { apiRequest } from "@/lib/api/client";
import { payForAppointment } from "./payForAppointment";

const mockedApiRequest = vi.mocked(apiRequest);

beforeEach(() => {
  mockedApiRequest.mockReset();
});

describe("payForAppointment", () => {
  it("test_posts_to_the_account_pay_endpoint_via_apirequest_and_returns_the_body", async () => {
    const body = {
      payment: { id: 9, status: "pending", amount: "100.00", currency: "UAH" },
      provider_data: null,
    };
    mockedApiRequest.mockResolvedValueOnce(body);

    const result = await payForAppointment("bella-demo", 42);

    expect(mockedApiRequest).toHaveBeenCalledTimes(1);
    const [slug, path, options] = mockedApiRequest.mock.calls[0];
    expect(slug).toBe("bella-demo");
    expect(path).toBe("/appointments/42/pay/");
    expect((options as RequestInit | undefined)?.method).toBe("POST");
    expect(result).toEqual(body);
  });
});
