// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  clearConversationExpired,
  clearSessionId,
  markConversationExpired,
  readConversationExpired,
  readSessionId,
  storeSessionId,
} from "./sessionId";

// docs/DECISIONS.md § "Stage 17 sub-step order", 17.7a: the "expired" state
// is kept in sessionStorage next to the session id, under its own key, until
// the customer opens the chat on an allowed page. The id itself is cleared at
// once, so clearing one never clears the other. Storage failures never break
// the widget.
const EXPIRED_KEY = "salonhub:assistant-expired";
const SESSION_KEY = "salonhub:assistant-session";

beforeEach(() => {
  sessionStorage.clear();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("assistant expired flag", () => {
  it("test_mark_then_read_returns_true_under_its_own_key", () => {
    markConversationExpired();

    expect(readConversationExpired()).toBe(true);
    expect(sessionStorage.getItem(EXPIRED_KEY)).not.toBeNull();
    expect(sessionStorage.getItem(SESSION_KEY)).toBeNull();
  });

  it("test_clear_removes_the_flag", () => {
    markConversationExpired();

    clearConversationExpired();

    expect(readConversationExpired()).toBe(false);
    expect(sessionStorage.getItem(EXPIRED_KEY)).toBeNull();
  });

  it("test_read_with_nothing_stored_returns_false", () => {
    expect(readConversationExpired()).toBe(false);
  });

  it("test_flag_and_session_id_are_cleared_independently", () => {
    storeSessionId("abc123");
    markConversationExpired();

    clearSessionId();
    expect(readConversationExpired()).toBe(true);

    storeSessionId("def456");
    clearConversationExpired();
    expect(readSessionId()).toBe("def456");
  });

  it("test_read_returns_false_without_throwing_when_storage_throws", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new DOMException("access denied", "SecurityError");
    });

    expect(readConversationExpired()).toBe(false);
  });

  it("test_mark_does_not_throw_when_storage_throws", () => {
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new DOMException("quota exceeded", "QuotaExceededError");
    });

    expect(() => markConversationExpired()).not.toThrow();
  });

  it("test_clear_does_not_throw_when_storage_throws", () => {
    vi.spyOn(Storage.prototype, "removeItem").mockImplementation(() => {
      throw new DOMException("access denied", "SecurityError");
    });

    expect(() => clearConversationExpired()).not.toThrow();
  });
});
