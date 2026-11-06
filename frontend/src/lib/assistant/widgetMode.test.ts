// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { clearDraft, storeDraft } from "./draft";
import {
  clearConversationExpired,
  clearSessionId,
  markConversationExpired,
  storeSessionId,
} from "./sessionId";
import { clearWidgetMode, readWidgetMode, storeWidgetMode } from "./widgetMode";

// docs/DECISIONS.md § "Stage 17 sub-step order", 17.7d: the widget's mode
// (collapsed, small window, side panel) is kept in sessionStorage for the
// life of the tab, next to the session id, under its own key. Storage
// failures never break the widget: reading then means "collapsed".
const MODE_KEY = "salonhub:assistant-mode";
const SESSION_KEY = "salonhub:assistant-session";
const EXPIRED_KEY = "salonhub:assistant-expired";
const DRAFT_KEY = "salonhub:assistant-draft";

beforeEach(() => {
  sessionStorage.clear();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("assistant widget mode", () => {
  it.each(["collapsed", "window", "panel"] as const)(
    "test_store_then_read_returns_the_mode_under_its_own_key_(%s)",
    (mode) => {
      storeWidgetMode(mode);

      expect(readWidgetMode()).toBe(mode);
      expect(sessionStorage.getItem(MODE_KEY)).toBe(mode);
      expect(sessionStorage.getItem(SESSION_KEY)).toBeNull();
      expect(sessionStorage.getItem(EXPIRED_KEY)).toBeNull();
      expect(sessionStorage.getItem(DRAFT_KEY)).toBeNull();
    },
  );

  it("test_read_with_nothing_stored_returns_collapsed", () => {
    expect(readWidgetMode()).toBe("collapsed");
  });

  it.each(["", "open", "Panel", "sidebar"])(
    "test_read_of_an_unknown_value_returns_collapsed_(%s)",
    (value) => {
      sessionStorage.setItem(MODE_KEY, value);

      expect(readWidgetMode()).toBe("collapsed");
    },
  );

  it("test_clear_removes_the_mode", () => {
    storeWidgetMode("panel");

    clearWidgetMode();

    expect(sessionStorage.getItem(MODE_KEY)).toBeNull();
    expect(readWidgetMode()).toBe("collapsed");
  });

  it("test_mode_and_the_other_assistant_keys_are_cleared_independently", () => {
    storeWidgetMode("window");
    storeSessionId("abc123");
    markConversationExpired();
    storeDraft("Привіт");

    clearSessionId();
    clearConversationExpired();
    clearDraft();
    expect(readWidgetMode()).toBe("window");

    storeSessionId("def456");
    markConversationExpired();
    storeDraft("Привіт");
    clearWidgetMode();
    expect(sessionStorage.getItem(SESSION_KEY)).toBe("def456");
    expect(sessionStorage.getItem(EXPIRED_KEY)).not.toBeNull();
    expect(sessionStorage.getItem(DRAFT_KEY)).toBe("Привіт");
  });

  it("test_read_returns_collapsed_without_throwing_when_storage_throws", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new DOMException("access denied", "SecurityError");
    });

    expect(readWidgetMode()).toBe("collapsed");
  });

  it("test_store_does_not_throw_when_storage_throws", () => {
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new DOMException("quota exceeded", "QuotaExceededError");
    });

    expect(() => storeWidgetMode("window")).not.toThrow();
  });

  it("test_clear_does_not_throw_when_storage_throws", () => {
    vi.spyOn(Storage.prototype, "removeItem").mockImplementation(() => {
      throw new DOMException("access denied", "SecurityError");
    });

    expect(() => clearWidgetMode()).not.toThrow();
  });
});
