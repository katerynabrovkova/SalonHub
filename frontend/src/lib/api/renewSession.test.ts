// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";

import { renewSession, whenRenewalIdle } from "./renewSession";
import { SESSION_EXPIRED_EVENT } from "./sessionHint";

// NEXT_PUBLIC_API_PORT is read at import time by client.ts and is not set
// here (docs/DECISIONS.md, S2 design details, point 12), so URL assertions
// use `expect.stringContaining` rather than a full URL.

const SLUG = "bella-demo";

function clearCookie(name: string) {
  document.cookie = `${name}=; max-age=0; path=/`;
}

/** Stub `fetch` so each call answers immediately with a fresh Response. */
function stubFetchStatus(status: number) {
  vi.stubGlobal(
    "fetch",
    vi.fn(() => Promise.resolve(new Response(status === 200 ? "{}" : null, { status }))),
  );
}

/** Stub `fetch` so it never answers, but rejects on abort like a real fetch. */
function stubHangingFetch() {
  vi.stubGlobal(
    "fetch",
    vi.fn(
      (_url: unknown, init?: RequestInit) =>
        new Promise<Response>((_resolve, reject) => {
          init?.signal?.addEventListener("abort", () => {
            reject(new DOMException("The operation was aborted.", "AbortError"));
          });
        }),
    ),
  );
}

/** Stub `fetch` so the test decides when (and how) each call answers. */
function stubControllableFetch() {
  const resolvers: Array<(response: Response) => void> = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(
      () =>
        new Promise<Response>((resolve) => {
          resolvers.push(resolve);
        }),
    ),
  );
  return {
    async respond(status: number) {
      await vi.waitFor(() => expect(resolvers.length).toBeGreaterThan(0));
      for (const resolve of resolvers.splice(0)) {
        resolve(new Response(status === 200 ? "{}" : null, { status }));
      }
    },
  };
}

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  clearCookie("session_hint");
  clearCookie("csrftoken");
});

describe("renewSession outcomes", () => {
  it("test_a_2xx_answer_gives_renewed", async () => {
    stubFetchStatus(200);

    expect(await renewSession(SLUG)).toBe("renewed");
  });

  it("test_a_401_answer_gives_lost", async () => {
    stubFetchStatus(401);

    expect(await renewSession(SLUG)).toBe("lost");
  });

  it("test_a_403_answer_gives_unsure", async () => {
    stubFetchStatus(403);

    expect(await renewSession(SLUG)).toBe("unsure");
  });

  it("test_a_500_answer_gives_unsure", async () => {
    stubFetchStatus(500);

    expect(await renewSession(SLUG)).toBe("unsure");
  });

  it("test_a_rejecting_fetch_gives_unsure", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.reject(new TypeError("Failed to fetch"))),
    );

    expect(await renewSession(SLUG)).toBe("unsure");
  });

  it("test_a_fetch_that_never_answers_gives_unsure_after_10_seconds_and_keeps_the_hint", async () => {
    vi.useFakeTimers();
    document.cookie = "session_hint=1; path=/";
    stubHangingFetch();

    let settled = false;
    const outcome = renewSession(SLUG).then((value) => {
      settled = true;
      return value;
    });

    await vi.advanceTimersByTimeAsync(9_999);
    expect(settled).toBe(false);

    await vi.advanceTimersByTimeAsync(1);
    expect(await outcome).toBe("unsure");
    expect(document.cookie).toContain("session_hint=1");
  });
});

describe("renewSession cleanup and robustness", () => {
  it("test_no_timers_are_left_pending_once_a_renewal_has_settled", async () => {
    vi.useFakeTimers();
    stubFetchStatus(200);

    expect(await renewSession(SLUG)).toBe("renewed");

    expect(vi.getTimerCount()).toBe(0);
  });

  it("test_a_malformed_csrftoken_cookie_gives_unsure_and_does_not_reject", async () => {
    // decodeURIComponent throws on this value; the promise must still never reject.
    document.cookie = "csrftoken=%E0%A4%A; path=/";
    stubFetchStatus(200);

    await expect(renewSession(SLUG)).resolves.toBe("unsure");
  });
});

describe("renewSession request shape", () => {
  it("test_posts_to_the_salon_refresh_url_with_credentials_and_the_csrf_header", async () => {
    document.cookie = "csrftoken=abc123; path=/";
    stubFetchStatus(200);

    await renewSession(SLUG);

    const [url, init] = vi.mocked(fetch).mock.calls[0];
    expect(url).toEqual(expect.stringContaining(`/api/v1/salons/${SLUG}/auth/refresh/`));
    expect(init?.method).toBe("POST");
    expect(init?.credentials).toBe("include");
    expect(new Headers(init?.headers).get("X-CSRFToken")).toBe("abc123");
  });

  it("test_still_sends_the_request_without_the_csrf_header_when_the_cookie_is_missing", async () => {
    stubFetchStatus(200);

    await renewSession(SLUG);

    expect(vi.mocked(fetch)).toHaveBeenCalledTimes(1);
    const [, init] = vi.mocked(fetch).mock.calls[0];
    expect(new Headers(init?.headers).has("X-CSRFToken")).toBe(false);
  });
});

describe("renewSession single flight", () => {
  it("test_two_concurrent_calls_make_one_fetch_and_share_the_outcome", async () => {
    stubFetchStatus(200);

    const [first, second] = await Promise.all([renewSession(SLUG), renewSession(SLUG)]);

    expect(vi.mocked(fetch)).toHaveBeenCalledTimes(1);
    expect(first).toBe("renewed");
    expect(second).toBe("renewed");
  });

  it("test_a_new_call_after_the_flight_settled_makes_a_new_fetch", async () => {
    stubFetchStatus(200);

    await renewSession(SLUG);
    await renewSession(SLUG);

    expect(vi.mocked(fetch)).toHaveBeenCalledTimes(2);
  });

  it("test_a_new_call_after_an_unsure_outcome_makes_a_new_fetch", async () => {
    stubFetchStatus(500);

    expect(await renewSession(SLUG)).toBe("unsure");
    expect(await renewSession(SLUG)).toBe("unsure");

    expect(vi.mocked(fetch)).toHaveBeenCalledTimes(2);
  });

  it("test_different_slugs_do_not_share_a_flight", async () => {
    stubFetchStatus(200);

    await Promise.all([renewSession("bella-demo"), renewSession("other-salon")]);

    const urls = vi.mocked(fetch).mock.calls.map(([url]) => String(url));
    expect(urls).toHaveLength(2);
    expect(urls.some((url) => url.includes("/salons/bella-demo/"))).toBe(true);
    expect(urls.some((url) => url.includes("/salons/other-salon/"))).toBe(true);
  });
});

describe("renewSession side effects", () => {
  it("test_lost_with_three_waiters_deletes_the_hint_and_dispatches_the_event_once", async () => {
    document.cookie = "session_hint=1; path=/";
    stubFetchStatus(401);
    const listener = vi.fn();
    window.addEventListener(SESSION_EXPIRED_EVENT, listener);
    try {
      const outcomes = await Promise.all([
        renewSession(SLUG),
        renewSession(SLUG),
        renewSession(SLUG),
      ]);

      expect(outcomes).toEqual(["lost", "lost", "lost"]);
      expect(document.cookie).not.toContain("session_hint");
      expect(listener).toHaveBeenCalledTimes(1);
    } finally {
      window.removeEventListener(SESSION_EXPIRED_EVENT, listener);
    }
  });

  it("test_unsure_keeps_the_hint_and_dispatches_no_event", async () => {
    document.cookie = "session_hint=1; path=/";
    stubFetchStatus(403);
    const listener = vi.fn();
    window.addEventListener(SESSION_EXPIRED_EVENT, listener);
    try {
      expect(await renewSession(SLUG)).toBe("unsure");

      expect(document.cookie).toContain("session_hint=1");
      expect(listener).not.toHaveBeenCalled();
    } finally {
      window.removeEventListener(SESSION_EXPIRED_EVENT, listener);
    }
  });
});

describe("whenRenewalIdle", () => {
  it("test_waits_for_an_in_flight_renewal", async () => {
    const controller = stubControllableFetch();
    const renewal = renewSession(SLUG);

    let idle = false;
    const waiting = whenRenewalIdle(SLUG).then(() => {
      idle = true;
    });

    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(idle).toBe(false);

    await controller.respond(200);
    await waiting;
    expect(idle).toBe(true);
    expect(await renewal).toBe("renewed");
  });

  it("test_resolves_immediately_when_no_renewal_is_in_flight", async () => {
    const winner = await Promise.race([
      whenRenewalIdle(SLUG).then(() => "idle"),
      new Promise<string>((resolve) => setTimeout(() => resolve("timeout"), 0)),
    ]);

    expect(winner).toBe("idle");
  });
});
