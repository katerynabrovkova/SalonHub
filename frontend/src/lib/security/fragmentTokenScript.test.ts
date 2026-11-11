// @vitest-environment jsdom
/**
 * The shared inline script that moves a token out of the fragment before
 * hydration (docs/DECISIONS.md "Booking > Guest token", decided 11.11.2026).
 * The built text is run as the browser would run an inline script: as a
 * plain function body against the page's globals.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  buildFragmentTokenScript,
  manageTokenScriptConfig,
  PAY_TOKEN_SCRIPT_CONFIG,
  RESET_LINK_SCRIPT_CONFIG,
  type FragmentTokenScriptConfig,
} from "./fragmentTokenScript";

const MANAGE = manageTokenScriptConfig(42);

function setUrl(url: string, state: unknown = null) {
  window.history.replaceState(state, "", url);
}

function runScript(config: FragmentTokenScriptConfig) {
  new Function(buildFragmentTokenScript(config))();
}

function storageSnapshot(): Record<string, string> {
  const snapshot: Record<string, string> = {};
  for (let i = 0; i < window.sessionStorage.length; i++) {
    const key = window.sessionStorage.key(i)!;
    snapshot[key] = window.sessionStorage.getItem(key)!;
  }
  return snapshot;
}

function blockStorage() {
  const blocked = () => {
    throw new DOMException("The operation is insecure.", "SecurityError");
  };
  return vi.spyOn(Storage.prototype, "setItem").mockImplementation(blocked);
}

let originalUrl: string;

beforeEach(() => {
  originalUrl = window.location.href;
  window.sessionStorage.clear();
});

afterEach(() => {
  vi.restoreAllMocks();
  window.sessionStorage.clear();
  window.history.replaceState(null, "", originalUrl);
});

describe("buildFragmentTokenScript", () => {
  it("test_manage_stores_the_token_under_its_id_and_removes_the_hash_keeping_path_and_state", () => {
    const state = { kept: true };
    setUrl("/appointments/42/manage?from=email#token=tok-abc", state);

    runScript(MANAGE);

    expect(window.sessionStorage.getItem("guest-manage-token:42")).toBe("tok-abc");
    expect(window.location.hash).toBe("");
    expect(window.location.href).not.toContain("#");
    expect(window.location.pathname).toBe("/appointments/42/manage");
    expect(window.location.search).toBe("?from=email");
    expect(window.history.state).toEqual(state);
  });

  it("test_pay_stores_the_token_per_appointment_id_and_keeps_the_id_in_the_hash", () => {
    setUrl("/booking/pay#appointment_id=42&token=tok-abc");

    runScript(PAY_TOKEN_SCRIPT_CONFIG);

    expect(window.sessionStorage.getItem("guest-pay-token:42")).toBe("tok-abc");
    expect(window.location.pathname).toBe("/booking/pay");
    expect(window.location.hash).toBe("#appointment_id=42");
    expect(window.location.href).not.toContain("tok-abc");
  });

  it("test_reset_password_stores_uid_and_token_under_its_own_key_and_removes_the_hash", () => {
    setUrl("/reset-password#uid=MQ&token=cz1x2y-0123456789abcdef");

    runScript(RESET_LINK_SCRIPT_CONFIG);

    const stored = window.sessionStorage.getItem("password-reset-link");
    expect(stored).not.toBeNull();
    const params = new URLSearchParams(stored!);
    expect(params.get("uid")).toBe("MQ");
    expect(params.get("token")).toBe("cz1x2y-0123456789abcdef");
    expect(window.location.hash).toBe("");
    expect(window.location.pathname).toBe("/reset-password");
  });

  it.each([
    { name: "manage", config: MANAGE, url: "/appointments/42/manage#token=tok-abc" },
    { name: "pay", config: PAY_TOKEN_SCRIPT_CONFIG, url: "/booking/pay#appointment_id=42&token=tok-abc" },
    { name: "reset", config: RESET_LINK_SCRIPT_CONFIG, url: "/reset-password#uid=MQ&token=tok-abc" },
  ])("test_blocked_storage_leaves_the_hash_alone_($name)", ({ config, url }) => {
    setUrl(url);
    const setItemSpy = blockStorage();
    const replaceStateSpy = vi.spyOn(window.history, "replaceState");

    runScript(config);

    // The script did try to store, and then left the URL as it was.
    expect(setItemSpy).toHaveBeenCalled();
    expect(replaceStateSpy).not.toHaveBeenCalled();
    expect(window.location.pathname + window.location.hash).toBe(url);
  });

  it.each([
    { name: "manage_no_hash", config: MANAGE, url: "/appointments/42/manage" },
    { name: "manage_other_param", config: MANAGE, url: "/appointments/42/manage#foo=bar" },
    { name: "manage_empty_token", config: MANAGE, url: "/appointments/42/manage#token=" },
    // A reload after the script ran: only the id is left in the hash.
    { name: "pay_id_only", config: PAY_TOKEN_SCRIPT_CONFIG, url: "/booking/pay#appointment_id=42" },
    { name: "pay_token_without_id", config: PAY_TOKEN_SCRIPT_CONFIG, url: "/booking/pay#token=tok-abc" },
    {
      name: "pay_non_integer_id",
      config: PAY_TOKEN_SCRIPT_CONFIG,
      url: "/booking/pay#appointment_id=4x2&token=tok-abc",
    },
    { name: "reset_uid_only", config: RESET_LINK_SCRIPT_CONFIG, url: "/reset-password#uid=MQ" },
    { name: "reset_no_hash", config: RESET_LINK_SCRIPT_CONFIG, url: "/reset-password" },
  ])("test_without_a_complete_link_does_nothing_($name)", ({ config, url }) => {
    setUrl(url);
    window.sessionStorage.setItem("guest-pay-token:42", "tok-earlier");
    const replaceStateSpy = vi.spyOn(window.history, "replaceState");

    runScript(config);

    expect(storageSnapshot()).toEqual({ "guest-pay-token:42": "tok-earlier" });
    expect(replaceStateSpy).not.toHaveBeenCalled();
    expect(window.location.pathname + window.location.hash).toBe(url);
  });

  it.each([
    {
      name: "manage",
      config: MANAGE,
      url: "/appointments/42/manage#token=tok-new",
      ownKey: "guest-manage-token:42",
    },
    {
      name: "pay",
      config: PAY_TOKEN_SCRIPT_CONFIG,
      url: "/booking/pay#appointment_id=42&token=tok-new",
      ownKey: "guest-pay-token:42",
    },
    {
      name: "reset",
      config: RESET_LINK_SCRIPT_CONFIG,
      url: "/reset-password#uid=MQ&token=tok-new",
      ownKey: "password-reset-link",
    },
  ])("test_never_touches_another_page_key_($name)", ({ config, url, ownKey }) => {
    const others: Record<string, string> = {
      "guest-manage-token:42": "m42",
      "guest-manage-token:7": "m7",
      "guest-pay-token:42": "p42",
      "guest-pay-token:7": "p7",
      "password-reset-link": "uid=X&token=Y",
      "salonhub:return-path": "/client",
    };
    delete others[ownKey];
    for (const [key, value] of Object.entries(others)) {
      window.sessionStorage.setItem(key, value);
    }
    setUrl(url);

    runScript(config);

    const { [ownKey]: own, ...rest } = storageSnapshot();
    expect(own).toBeDefined();
    expect(own).toContain("tok-new");
    expect(rest).toEqual(others);
  });

  it("test_a_hostile_key_cannot_close_the_script_element", () => {
    const key = "</script><script>alert(1)//";
    const config: FragmentTokenScriptConfig = { storageKey: key, moveParams: ["token"], keepParams: [] };
    setUrl("/x#token=tok-abc");

    const text = buildFragmentTokenScript(config);

    expect(text).not.toMatch(/<\/script/i);
    new Function(text)();
    expect(window.sessionStorage.getItem(key)).toBe("tok-abc");
  });
});
