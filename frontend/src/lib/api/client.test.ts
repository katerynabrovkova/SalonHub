// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { apiRequest } from "./client";
import { ApiError, RenewalUnsureError } from "./errors";
import { renewSession } from "./renewSession";

// docs/DECISIONS.md, "S2 design details", point 11: renewSession lives
// outside client.ts. Mocked here so tests control its outcome without a
// real fetch; renewSession's own behavior is covered by renewSession.test.ts.
vi.mock("./renewSession", () => ({ renewSession: vi.fn() }));

const SLUG = "bella-demo";

function clearCookie(name: string) {
  document.cookie = `${name}=; max-age=0; path=/`;
}

function unauthorizedResponse() {
  return new Response(JSON.stringify({ error: { code: "authentication_failed", message: "Bad" } }), {
    status: 401,
    headers: { "Content-Type": "application/json" },
  });
}

beforeEach(() => {
  process.env.NEXT_PUBLIC_API_PORT = "8000";
  document.cookie = "csrftoken=abc123";
  vi.stubGlobal("fetch", vi.fn());
  vi.mocked(renewSession).mockReset();
});

afterEach(() => {
  clearCookie("session_hint");
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

describe("apiRequest session renewal (docs/DECISIONS.md, S2 design details)", () => {
  it("test_401_without_the_hint_cookie_never_calls_renewsession", async () => {
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockResolvedValueOnce(unauthorizedResponse());

    await expect(apiRequest(SLUG, "/auth/me/")).rejects.toMatchObject({
      status: 401,
    });

    expect(renewSession).not.toHaveBeenCalled();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it.each([["/auth/login/"], ["/auth/refresh/"], ["/auth/logout/"], ["/auth/csrf/"]])(
    "test_401_with_the_hint_on_the_excluded_path_%s_never_calls_renewsession",
    async (path) => {
      document.cookie = "session_hint=1; path=/";
      const fetchMock = vi.mocked(fetch);
      fetchMock.mockResolvedValueOnce(unauthorizedResponse());

      await expect(apiRequest(SLUG, path)).rejects.toMatchObject({ status: 401 });

      expect(renewSession).not.toHaveBeenCalled();
      expect(fetchMock).toHaveBeenCalledTimes(1);
    },
  );

  it("test_401_with_hint_renewed_retries_once_with_the_same_method_body_and_headers", async () => {
    document.cookie = "session_hint=1; path=/";
    vi.mocked(renewSession).mockResolvedValueOnce("renewed");
    const fetchMock = vi.mocked(fetch);
    fetchMock
      .mockResolvedValueOnce(unauthorizedResponse())
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ ok: true }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      );

    const result = await apiRequest(SLUG, "/auth/me/", {
      method: "PATCH",
      body: JSON.stringify({ name: "New" }),
      headers: { "X-Custom": "keep-me" },
    });

    expect(fetchMock).toHaveBeenCalledTimes(2);
    const [, retryInit] = fetchMock.mock.calls[1];
    expect(retryInit?.method).toBe("PATCH");
    expect(retryInit?.body).toBe(JSON.stringify({ name: "New" }));
    expect(new Headers(retryInit?.headers).get("X-Custom")).toBe("keep-me");
    expect(result).toEqual({ ok: true });
  });

  it("test_401_on_the_retry_after_renewed_throws_that_401_and_calls_renewsession_only_once", async () => {
    document.cookie = "session_hint=1; path=/";
    vi.mocked(renewSession).mockResolvedValueOnce("renewed");
    const fetchMock = vi.mocked(fetch);
    fetchMock
      .mockResolvedValueOnce(unauthorizedResponse())
      .mockResolvedValueOnce(unauthorizedResponse());

    await expect(apiRequest(SLUG, "/auth/me/")).rejects.toMatchObject({ status: 401 });

    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(renewSession).toHaveBeenCalledTimes(1);
  });

  it("test_401_with_hint_lost_throws_the_original_401_with_no_retry", async () => {
    document.cookie = "session_hint=1; path=/";
    vi.mocked(renewSession).mockResolvedValueOnce("lost");
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockResolvedValueOnce(unauthorizedResponse());

    await expect(apiRequest(SLUG, "/auth/me/")).rejects.toMatchObject({ status: 401 });

    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("test_401_with_hint_unsure_throws_a_distinct_error_not_a_401_apierror", async () => {
    document.cookie = "session_hint=1; path=/";
    vi.mocked(renewSession).mockResolvedValueOnce("unsure");
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockResolvedValueOnce(unauthorizedResponse());

    let caught: unknown;
    try {
      await apiRequest(SLUG, "/auth/me/");
    } catch (error) {
      caught = error;
    }

    expect(caught).toBeInstanceOf(RenewalUnsureError);
    expect(caught).not.toBeInstanceOf(ApiError);
  });

  it("test_the_retry_reads_the_csrftoken_cookie_again_at_retry_time", async () => {
    document.cookie = "session_hint=1; path=/";
    document.cookie = "csrftoken=first-token; path=/";
    vi.mocked(renewSession).mockImplementationOnce(async () => {
      document.cookie = "csrftoken=second-token; path=/";
      return "renewed";
    });
    const fetchMock = vi.mocked(fetch);
    fetchMock
      .mockResolvedValueOnce(unauthorizedResponse())
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ ok: true }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      );

    await apiRequest(SLUG, "/auth/me/", { method: "POST" });

    const [, firstInit] = fetchMock.mock.calls[0];
    const [, retryInit] = fetchMock.mock.calls[1];
    expect(new Headers(firstInit?.headers).get("X-CSRFToken")).toBe("first-token");
    expect(new Headers(retryInit?.headers).get("X-CSRFToken")).toBe("second-token");
  });

  it("test_calls_renewsession_with_the_same_slug_apirequest_was_called_with", async () => {
    document.cookie = "session_hint=1; path=/";
    vi.mocked(renewSession).mockResolvedValueOnce("lost");
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockResolvedValueOnce(unauthorizedResponse());

    await expect(apiRequest("other-salon", "/auth/me/")).rejects.toMatchObject({ status: 401 });

    expect(renewSession).toHaveBeenCalledWith("other-salon");
  });
});
