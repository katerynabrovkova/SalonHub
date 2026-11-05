// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { clearDraft, readDraft, storeDraft } from "./draft";
import {
  clearConversationExpired,
  clearSessionId,
  markConversationExpired,
  readConversationExpired,
  readSessionId,
  storeSessionId,
} from "./sessionId";

// docs/DECISIONS.md § "Stage 17 sub-step order", 17.7b: the unsent text in
// the chat input is kept in sessionStorage next to the session id, under its
// own key. Storage failures never break the widget.
const DRAFT_KEY = "salonhub:assistant-draft";
const SESSION_KEY = "salonhub:assistant-session";
const EXPIRED_KEY = "salonhub:assistant-expired";

beforeEach(() => {
  sessionStorage.clear();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("assistant draft", () => {
  it("test_store_then_read_returns_the_text_under_its_own_key", () => {
    storeDraft("Скільки коштує манікюр?");

    expect(readDraft()).toBe("Скільки коштує манікюр?");
    expect(sessionStorage.getItem(DRAFT_KEY)).toBe("Скільки коштує манікюр?");
    expect(sessionStorage.getItem(SESSION_KEY)).toBeNull();
    expect(sessionStorage.getItem(EXPIRED_KEY)).toBeNull();
  });

  it("test_text_is_kept_as_typed_including_new_lines_and_outer_spaces", () => {
    storeDraft("  Перший рядок\nДругий  ");

    expect(readDraft()).toBe("  Перший рядок\nДругий  ");
  });

  it("test_clear_removes_the_draft", () => {
    storeDraft("Привіт");

    clearDraft();

    expect(readDraft()).toBe("");
    expect(sessionStorage.getItem(DRAFT_KEY)).toBeNull();
  });

  it("test_read_with_nothing_stored_returns_empty", () => {
    expect(readDraft()).toBe("");
  });

  it.each([
    { name: "empty", text: "" },
    { name: "whitespace_only", text: "  \n\t " },
  ])("test_storing_blank_text_removes_the_stored_draft_($name)", ({ text }) => {
    storeDraft("Привіт");

    storeDraft(text);

    expect(sessionStorage.getItem(DRAFT_KEY)).toBeNull();
    expect(readDraft()).toBe("");
  });

  it("test_draft_session_id_and_expired_flag_are_cleared_independently", () => {
    storeDraft("Привіт");
    storeSessionId("abc123");
    markConversationExpired();

    clearSessionId();
    clearConversationExpired();
    expect(readDraft()).toBe("Привіт");

    storeSessionId("def456");
    markConversationExpired();
    clearDraft();
    expect(readSessionId()).toBe("def456");
    expect(readConversationExpired()).toBe(true);
  });

  it("test_read_returns_empty_without_throwing_when_storage_throws", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new DOMException("access denied", "SecurityError");
    });

    expect(readDraft()).toBe("");
  });

  it("test_store_does_not_throw_when_storage_throws", () => {
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new DOMException("quota exceeded", "QuotaExceededError");
    });

    expect(() => storeDraft("Привіт")).not.toThrow();
  });

  it("test_clear_does_not_throw_when_storage_throws", () => {
    vi.spyOn(Storage.prototype, "removeItem").mockImplementation(() => {
      throw new DOMException("access denied", "SecurityError");
    });

    expect(() => clearDraft()).not.toThrow();
  });
});
