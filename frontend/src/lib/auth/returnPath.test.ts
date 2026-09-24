// @vitest-environment jsdom
import { afterEach, describe, expect, it, test, vi } from "vitest";

import { consumeReturnPath, isSafeReturnPath, saveReturnPath } from "./returnPath";

afterEach(() => {
  vi.restoreAllMocks();
  sessionStorage.clear();
});

describe("saveReturnPath / consumeReturnPath", () => {
  it("test_consume_returns_the_saved_path", () => {
    saveReturnPath("/client/profile?tab=x");

    expect(consumeReturnPath()).toBe("/client/profile?tab=x");
  });

  it("test_consume_removes_the_path_so_a_second_consume_returns_null", () => {
    saveReturnPath("/client/profile");

    consumeReturnPath();

    expect(consumeReturnPath()).toBeNull();
  });

  it("test_consume_with_nothing_stored_returns_null", () => {
    expect(consumeReturnPath()).toBeNull();
  });

  it("test_path_is_stored_under_the_salonhub_return_path_key", () => {
    saveReturnPath("/client/profile");

    expect(sessionStorage.getItem("salonhub:return-path")).toBe("/client/profile");
  });

  it("test_save_does_not_throw_when_session_storage_set_item_throws", () => {
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new DOMException("quota exceeded", "QuotaExceededError");
    });

    expect(() => saveReturnPath("/client")).not.toThrow();
  });

  it("test_consume_returns_null_without_throwing_when_session_storage_get_item_throws", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new DOMException("access denied", "SecurityError");
    });

    let result: string | null | undefined;
    expect(() => {
      result = consumeReturnPath();
    }).not.toThrow();
    expect(result).toBeNull();
  });
});

describe("isSafeReturnPath", () => {
  test.each(["/client", "/client/profile", "/client?tab=x", "/client/profile?tab=x"])(
    "test_accepts_%s_for_client_role",
    (path) => {
      expect(isSafeReturnPath(path, "client")).toBe(true);
    },
  );

  test.each([
    "//evil.com",
    "https://evil.com",
    "/\\evil.com",
    "javascript:alert(1)",
    "/clientx",
    "/client/../admin",
    "https://evil.com/client",
    "//evil.com/client/profile",
  ])("test_rejects_%s_for_client_role", (path) => {
    expect(isSafeReturnPath(path, "client")).toBe(false);
  });

  it("test_rejects_a_client_path_for_admin_role", () => {
    expect(isSafeReturnPath("/client/profile", "admin")).toBe(false);
  });
});
