// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/lib/api/errors";

import { createGuestBooking } from "./createGuestBooking";

const API_BASE = "http://localhost:8000";

function jsonResponse(body: unknown, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  };
}

const BASE_PARAMS = {
  slug: "bella-demo",
  service: "5",
  specialist: "any",
  startDatetime: "2026-08-17T09:00:00+03:00",
  customerName: "Alice",
  customerEmail: "alice@example.com",
  customerPhone: "+10000000000",
};

beforeEach(() => {
  process.env.NEXT_PUBLIC_API_URL = API_BASE;
  document.cookie = "csrftoken=abc123";
  vi.stubGlobal("fetch", vi.fn());
});

describe("createGuestBooking", () => {
  it("test_sends_post_to_bookings_url_with_no_guest_token_header_and_no_credentials", async () => {
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockResolvedValueOnce(
      jsonResponse(
        {
          appointment: {
            id: 1,
            status: "pending_payment",
            start_datetime: "2026-08-17T09:00:00+03:00",
            end_datetime: "2026-08-17T10:15:00+03:00",
          },
          guest_token: "tok-abc",
        },
        201,
      ) as Response,
    );

    await createGuestBooking(BASE_PARAMS);

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("http://localhost:8000/api/v1/salons/bella-demo/bookings/");
    expect(init?.method).toBe("POST");
    expect(init?.credentials).not.toBe("include");
    const headers = new Headers(init?.headers);
    expect(headers.has("X-Guest-Token")).toBe(false);
    expect(headers.has("X-CSRFToken")).toBe(false);
  });

  it("test_request_body_maps_camelcase_params_to_snake_case_fields", async () => {
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockResolvedValueOnce(
      jsonResponse(
        {
          appointment: {
            id: 1,
            status: "pending_payment",
            start_datetime: "2026-08-17T09:00:00+03:00",
            end_datetime: "2026-08-17T10:15:00+03:00",
          },
          guest_token: "tok-abc",
        },
        201,
      ) as Response,
    );

    await createGuestBooking(BASE_PARAMS);

    const [, init] = fetchMock.mock.calls[0];
    expect(JSON.parse(init?.body as string)).toEqual({
      service: "5",
      specialist: "any",
      start_datetime: "2026-08-17T09:00:00+03:00",
      customer_name: "Alice",
      customer_email: "alice@example.com",
      customer_phone: "+10000000000",
    });
  });

  it("test_201_success_returns_mapped_appointment_and_guest_token", async () => {
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockResolvedValueOnce(
      jsonResponse(
        {
          appointment: {
            id: 42,
            status: "pending_payment",
            start_datetime: "2026-08-17T09:00:00+03:00",
            end_datetime: "2026-08-17T10:15:00+03:00",
          },
          guest_token: "tok-xyz",
        },
        201,
      ) as Response,
    );

    const result = await createGuestBooking(BASE_PARAMS);

    expect(result).toEqual({
      appointment: {
        id: 42,
        status: "pending_payment",
        startDatetime: "2026-08-17T09:00:00+03:00",
        endDatetime: "2026-08-17T10:15:00+03:00",
      },
      guestToken: "tok-xyz",
    });
  });

  it("test_409_slot_unavailable_throws_apierror_with_distinct_status_and_code", async () => {
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockResolvedValueOnce(
      jsonResponse(
        {
          error: {
            code: "SLOT_NO_LONGER_AVAILABLE",
            message: "This slot is no longer available.",
            details: {},
          },
        },
        409,
      ) as Response,
    );

    await expect(createGuestBooking(BASE_PARAMS)).rejects.toMatchObject({
      status: 409,
      code: "SLOT_NO_LONGER_AVAILABLE",
    });

    fetchMock.mockResolvedValueOnce(
      jsonResponse(
        {
          error: {
            code: "SLOT_NO_LONGER_AVAILABLE",
            message: "This slot is no longer available.",
            details: {},
          },
        },
        409,
      ) as Response,
    );
    await expect(createGuestBooking(BASE_PARAMS)).rejects.toBeInstanceOf(ApiError);
  });

  it("test_base_url_uses_next_public_api_url", async () => {
    process.env.NEXT_PUBLIC_API_URL = "http://public-base:9999";
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockResolvedValueOnce(
      jsonResponse(
        {
          appointment: {
            id: 1,
            status: "pending_payment",
            start_datetime: "2026-08-17T09:00:00+03:00",
            end_datetime: "2026-08-17T10:15:00+03:00",
          },
          guest_token: "tok-abc",
        },
        201,
      ) as Response,
    );

    await createGuestBooking(BASE_PARAMS);

    const [url] = fetchMock.mock.calls[0];
    expect(url).toBe("http://public-base:9999/api/v1/salons/bella-demo/bookings/");
  });
});
