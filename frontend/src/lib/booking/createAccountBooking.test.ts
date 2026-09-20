// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api/client", () => ({
  apiRequest: vi.fn(),
}));

import { apiRequest } from "@/lib/api/client";
import { ApiError } from "@/lib/api/errors";
import { createAccountBooking } from "./createAccountBooking";

const mockedApiRequest = vi.mocked(apiRequest);

beforeEach(() => {
  mockedApiRequest.mockReset();
});

describe("createAccountBooking", () => {
  it("test_without_contact_the_body_has_no_customer_fields", async () => {
    mockedApiRequest.mockResolvedValueOnce({
      appointment: { id: 1, status: "pending_payment", start_datetime: "x", end_datetime: "y" },
    });

    await createAccountBooking({
      slug: "bella-demo",
      service: "5",
      specialist: "any",
      startDatetime: "2026-08-17T09:00:00+03:00",
    });

    const [, , options] = mockedApiRequest.mock.calls[0];
    const body = JSON.parse((options as RequestInit).body as string) as Record<string, unknown>;
    expect("customer_name" in body).toBe(false);
    expect("customer_phone" in body).toBe(false);
    expect("customer_email" in body).toBe(false);
    expect(body).toMatchObject({
      service: "5",
      specialist: "any",
      start_datetime: "2026-08-17T09:00:00+03:00",
    });
  });

  it("test_with_contact_the_body_has_customer_name_and_phone_but_no_email", async () => {
    mockedApiRequest.mockResolvedValueOnce({
      appointment: { id: 1, status: "pending_payment", start_datetime: "x", end_datetime: "y" },
    });

    await createAccountBooking({
      slug: "bella-demo",
      service: "5",
      specialist: "any",
      startDatetime: "2026-08-17T09:00:00+03:00",
      contact: { name: "Alice", phone: "+10000000000" },
    });

    const [, , options] = mockedApiRequest.mock.calls[0];
    const body = JSON.parse((options as RequestInit).body as string) as Record<string, unknown>;
    expect(body).toMatchObject({
      customer_name: "Alice",
      customer_phone: "+10000000000",
    });
    expect("customer_email" in body).toBe(false);
  });

  it("test_specialist_any_and_numeric_id_are_sent_the_same_way_as_guest_booking", async () => {
    mockedApiRequest.mockResolvedValueOnce({
      appointment: { id: 1, status: "pending_payment", start_datetime: "x", end_datetime: "y" },
    });
    await createAccountBooking({
      slug: "bella-demo",
      service: "5",
      specialist: "any",
      startDatetime: "2026-08-17T09:00:00+03:00",
    });
    const [, , optionsAny] = mockedApiRequest.mock.calls[0];
    const bodyAny = JSON.parse((optionsAny as RequestInit).body as string) as Record<
      string,
      unknown
    >;
    expect(bodyAny.specialist).toBe("any");

    mockedApiRequest.mockResolvedValueOnce({
      appointment: { id: 2, status: "pending_payment", start_datetime: "x", end_datetime: "y" },
    });
    await createAccountBooking({
      slug: "bella-demo",
      service: "5",
      specialist: "7",
      startDatetime: "2026-08-17T09:00:00+03:00",
    });
    const [, , optionsNumeric] = mockedApiRequest.mock.calls[1];
    const bodyNumeric = JSON.parse((optionsNumeric as RequestInit).body as string) as Record<
      string,
      unknown
    >;
    expect(bodyNumeric.specialist).toBe("7");
  });

  it("test_apierror_403_unverified_account_is_rethrown_unchanged", async () => {
    const error = new ApiError(403, "email_not_verified", "Please verify your email.", {});
    mockedApiRequest.mockRejectedValueOnce(error);

    await expect(
      createAccountBooking({
        slug: "bella-demo",
        service: "5",
        specialist: "any",
        startDatetime: "2026-08-17T09:00:00+03:00",
      }),
    ).rejects.toBe(error);
  });
});
