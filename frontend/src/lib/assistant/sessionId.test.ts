// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { clearSessionId, readSessionId, storeSessionId } from "./sessionId";

// docs/DECISIONS.md § "Stage 17 sub-step order", 17.4a: the assistant
// session id lives in sessionStorage under one shared key (the 17.6 booking
// forms read it too), and storage failures never break the widget.
const KEY = "salonhub:assistant-session";

beforeEach(() => {
  sessionStorage.clear();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("assistant session id", () => {
  it("test_store_then_read_returns_the_id_under_the_shared_key", () => {
    storeSessionId("abc123");

    expect(readSessionId()).toBe("abc123");
    expect(sessionStorage.getItem(KEY)).toBe("abc123");
  });

  it("test_clear_removes_the_id", () => {
    storeSessionId("abc123");

    clearSessionId();

    expect(readSessionId()).toBeNull();
    expect(sessionStorage.getItem(KEY)).toBeNull();
  });

  it("test_read_with_nothing_stored_returns_null", () => {
    expect(readSessionId()).toBeNull();
  });

  it("test_read_returns_null_without_throwing_when_storage_throws", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new DOMException("access denied", "SecurityError");
    });

    expect(readSessionId()).toBeNull();
  });

  it("test_store_does_not_throw_when_storage_throws", () => {
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new DOMException("quota exceeded", "QuotaExceededError");
    });

    expect(() => storeSessionId("abc123")).not.toThrow();
  });
});
