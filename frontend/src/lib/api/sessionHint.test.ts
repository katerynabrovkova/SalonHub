// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";

import { hasSessionHint, markSessionLost, SESSION_EXPIRED_EVENT } from "./sessionHint";

function clearCookie(name: string) {
  document.cookie = `${name}=; max-age=0; path=/`;
}

afterEach(() => {
  clearCookie("session_hint");
  clearCookie("x_session_hint");
  clearCookie("csrftoken");
});

describe("hasSessionHint", () => {
  it("test_true_when_session_hint_cookie_is_1", () => {
    document.cookie = "session_hint=1; path=/";

    expect(hasSessionHint()).toBe(true);
  });

  it("test_false_when_session_hint_cookie_is_absent", () => {
    expect(hasSessionHint()).toBe(false);
  });

  it("test_false_for_a_cookie_whose_name_only_ends_with_session_hint", () => {
    document.cookie = "x_session_hint=1; path=/";

    expect(hasSessionHint()).toBe(false);
  });
});

describe("markSessionLost", () => {
  it("test_removes_the_session_hint_cookie", () => {
    document.cookie = "session_hint=1; path=/";

    markSessionLost();

    expect(document.cookie).not.toContain("session_hint");
  });

  it("test_dispatches_the_session_expired_event_once", () => {
    const listener = vi.fn();
    window.addEventListener(SESSION_EXPIRED_EVENT, listener);
    try {
      markSessionLost();

      expect(listener).toHaveBeenCalledTimes(1);
    } finally {
      window.removeEventListener(SESSION_EXPIRED_EVENT, listener);
    }
  });
});
