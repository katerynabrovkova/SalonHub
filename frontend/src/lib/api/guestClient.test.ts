// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "./errors";
import { guestApiRequest } from "./guestClient";

const API_BASE = "http://localhost:8000";

function jsonResponse(body: unknown, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  };
}

beforeEach(() => {
  process.env.NEXT_PUBLIC_API_URL = API_BASE;
  document.cookie = "csrftoken=abc123";
  vi.stubGlobal("fetch", vi.fn());
});

describe("guestApiRequest", () => {
  it("test_sends_x_guest_token_header_on_every_request", async () => {
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockResolvedValueOnce(jsonResponse({ id: 1 }) as Response);
    fetchMock.mockResolvedValueOnce(jsonResponse({ id: 1 }) as Response);

    await guestApiRequest("bella-demo", "/guest/appointments/1/", "tok-abc");
    await guestApiRequest("bella-demo", "/guest/appointments/1/pay/", "tok-abc", {
      method: "POST",
    });

    const [, getInit] = fetchMock.mock.calls[0];
    const [, postInit] = fetchMock.mock.calls[1];
    const getHeaders = new Headers(getInit?.headers);
    const postHeaders = new Headers(postInit?.headers);

    expect(getHeaders.get("X-Guest-Token")).toBe("tok-abc");
    expect(postHeaders.get("X-Guest-Token")).toBe("tok-abc");
  });

  it("test_does_not_send_credentials_include", async () => {
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockResolvedValueOnce(jsonResponse({ id: 1 }) as Response);

    await guestApiRequest("bella-demo", "/guest/appointments/1/", "tok-abc");

    const [, init] = fetchMock.mock.calls[0];
    expect(init?.credentials).not.toBe("include");
  });

  it("test_does_not_set_csrf_token_header_even_when_csrftoken_cookie_present", async () => {
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockResolvedValueOnce(jsonResponse({ ok: true }) as Response);

    await guestApiRequest("bella-demo", "/guest/appointments/1/pay/", "tok-abc", {
      method: "POST",
    });

    const [, init] = fetchMock.mock.calls[0];
    const headers = new Headers(init?.headers);
    expect(headers.has("X-CSRFToken")).toBe(false);
  });

  it("test_get_returns_parsed_json_body", async () => {
    const appointment = { id: 1, status: "confirmed" };
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockResolvedValueOnce(jsonResponse(appointment) as Response);

    const result = await guestApiRequest("bella-demo", "/guest/appointments/1/", "tok-abc");

    expect(result).toEqual(appointment);
  });

  it("test_post_sends_json_content_type_and_body_and_returns_parsed_response", async () => {
    const responseBody = { payment: { id: 5 }, provider_data: {} };
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockResolvedValueOnce(jsonResponse(responseBody) as Response);

    const result = await guestApiRequest(
      "bella-demo",
      "/guest/appointments/1/pay/",
      "tok-abc",
      { method: "POST", body: JSON.stringify({ foo: "bar" }) },
    );

    const [, init] = fetchMock.mock.calls[0];
    const headers = new Headers(init?.headers);
    expect(init?.method).toBe("POST");
    expect(headers.get("Content-Type")).toBe("application/json");
    expect(result).toEqual(responseBody);
  });

  it("test_non_2xx_response_throws_apierror_with_status_and_code_from_envelope", async () => {
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockResolvedValueOnce(
      jsonResponse(
        { error: { code: "SLOT_NO_LONGER_AVAILABLE", message: "Slot is gone." } },
        409,
      ) as Response,
    );

    await expect(
      guestApiRequest("bella-demo", "/guest/appointments/1/pay/", "tok-abc", {
        method: "POST",
      }),
    ).rejects.toMatchObject({
      status: 409,
      code: "SLOT_NO_LONGER_AVAILABLE",
    });

    fetchMock.mockResolvedValueOnce(
      jsonResponse(
        { error: { code: "SLOT_NO_LONGER_AVAILABLE", message: "Slot is gone." } },
        409,
      ) as Response,
    );
    await expect(
      guestApiRequest("bella-demo", "/guest/appointments/1/pay/", "tok-abc", {
        method: "POST",
      }),
    ).rejects.toBeInstanceOf(ApiError);
  });

  it("test_base_url_uses_next_public_api_url", async () => {
    process.env.NEXT_PUBLIC_API_URL = "http://public-base:9999";
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockResolvedValueOnce(jsonResponse({ id: 1 }) as Response);

    await guestApiRequest("bella-demo", "/guest/appointments/1/", "tok-abc");

    const [url] = fetchMock.mock.calls[0];
    expect(url).toBe("http://public-base:9999/api/v1/salons/bella-demo/guest/appointments/1/");
  });
});
