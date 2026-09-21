// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from "vitest";

import { apiRequest } from "./client";

beforeEach(() => {
  process.env.NEXT_PUBLIC_API_PORT = "8000";
  document.cookie = "csrftoken=abc123";
  vi.stubGlobal("fetch", vi.fn());
});

describe("apiRequest empty-body 2xx responses", () => {
  it("test_post_with_empty_202_body_resolves_undefined", async () => {
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockResolvedValueOnce(new Response(null, { status: 202 }));

    const result = await apiRequest("bella-demo", "/auth/register/", { method: "POST" });

    expect(result).toBeUndefined();
  });

  it("test_200_with_json_body_still_resolves_parsed_object", async () => {
    const fetchMock = vi.mocked(fetch);
    const body = { id: 1, status: "confirmed" };
    fetchMock.mockResolvedValueOnce(
      new Response(JSON.stringify(body), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    const result = await apiRequest("bella-demo", "/auth/me/");

    expect(result).toEqual(body);
  });

  it("test_204_still_resolves_undefined", async () => {
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockResolvedValueOnce(new Response(null, { status: 204 }));

    const result = await apiRequest("bella-demo", "/auth/verify-email/", { method: "POST" });

    expect(result).toBeUndefined();
  });

  it("test_empty_205_body_resolves_undefined", async () => {
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockResolvedValueOnce(new Response(null, { status: 205 }));

    const result = await apiRequest("bella-demo", "/auth/logout/", { method: "POST" });

    expect(result).toBeUndefined();
  });
});
