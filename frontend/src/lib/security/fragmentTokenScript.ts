/**
 * Inline script that moves a credential token out of the URL fragment before
 * Next.js hydrates (docs/DECISIONS.md "Booking > Guest token", decided
 * 11.11.2026). The Next.js router copies the URL, hash included, on first
 * load and writes that copy back on later router updates, so the hash has to
 * be gone before the router reads `window.location`.
 *
 * One builder for the manage, pay and reset-password pages, so they cannot
 * drift. Each page passes its config; the components read the same keys and
 * never remove the hash themselves. If storage is blocked the script leaves
 * the hash alone and the component reads it (accepted edge case).
 *
 * Ordering: the script must reach the browser before Next's first data
 * chunk (hydration starts on that chunk). No `loading.tsx` or Suspense
 * boundary above these pages without re-checking this in a browser.
 */

export interface FragmentTokenScriptConfig {
  /** A fixed key, or a prefix completed by the integer value of `idParam`
   * read from the fragment. */
  storageKey: string | { prefix: string; idParam: string };
  /** Fragment params moved into storage; all must be present and non-empty.
   * One param is stored as its raw value, several as a query string. */
  moveParams: readonly string[];
  /** Fragment params left in the URL after the move. */
  keepParams: readonly string[];
}

export const GUEST_MANAGE_TOKEN_PREFIX = "guest-manage-token:";
export const GUEST_PAY_TOKEN_PREFIX = "guest-pay-token:";
export const PASSWORD_RESET_LINK_KEY = "password-reset-link";

export function guestManageTokenKey(appointmentId: number): string {
  return `${GUEST_MANAGE_TOKEN_PREFIX}${appointmentId}`;
}

export function guestPayTokenKey(appointmentId: number): string {
  return `${GUEST_PAY_TOKEN_PREFIX}${appointmentId}`;
}

export function manageTokenScriptConfig(appointmentId: number): FragmentTokenScriptConfig {
  return {
    storageKey: guestManageTokenKey(appointmentId),
    moveParams: ["token"],
    keepParams: [],
  };
}

export const PAY_TOKEN_SCRIPT_CONFIG: FragmentTokenScriptConfig = {
  storageKey: { prefix: GUEST_PAY_TOKEN_PREFIX, idParam: "appointment_id" },
  moveParams: ["token"],
  keepParams: ["appointment_id"],
};

export const RESET_LINK_SCRIPT_CONFIG: FragmentTokenScriptConfig = {
  storageKey: PASSWORD_RESET_LINK_KEY,
  moveParams: ["uid", "token"],
  keepParams: [],
};

/**
 * The script body, a plain string rather than a stringified function so no
 * build step can rewrite it into something that needs a helper. It takes the
 * config as its only argument and never throws: any failure leaves the URL as
 * it was, and the component reads the hash.
 */
const SCRIPT_BODY = `(function (c) {
  try {
    var raw = location.hash.slice(1);
    if (raw === "") return;
    var params = new URLSearchParams(raw);
    var moved = new URLSearchParams();
    for (var i = 0; i < c.moveParams.length; i++) {
      var value = params.get(c.moveParams[i]);
      if (value === null || value === "") return;
      moved.set(c.moveParams[i], value);
    }
    var key = c.storageKey;
    if (typeof key !== "string") {
      var id = params.get(key.idParam);
      if (id === null || !/^[0-9]+$/.test(id)) return;
      key = key.prefix + id;
    }
    var stored = c.moveParams.length === 1 ? moved.get(c.moveParams[0]) : moved.toString();
    try {
      sessionStorage.setItem(key, stored);
    } catch (e) {
      return;
    }
    var kept = new URLSearchParams();
    for (var j = 0; j < c.keepParams.length; j++) {
      var keptValue = params.get(c.keepParams[j]);
      if (keptValue !== null) kept.set(c.keepParams[j], keptValue);
    }
    var rest = kept.toString();
    history.replaceState(
      history.state,
      "",
      location.pathname + location.search + (rest === "" ? "" : "#" + rest)
    );
  } catch (e) {}
})`;

/**
 * Config as a JS literal that is safe inside an HTML `<script>` element:
 * `<` is escaped so no value can close the element. (U+2028/U+2029 need no
 * escaping: they are legal in JS string literals since ES2019.)
 */
function scriptLiteral(config: FragmentTokenScriptConfig): string {
  return JSON.stringify(config).replace(/</g, "\\u003c");
}

export function buildFragmentTokenScript(config: FragmentTokenScriptConfig): string {
  return `${SCRIPT_BODY}(${scriptLiteral(config)});`;
}
