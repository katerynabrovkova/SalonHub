import { describe, expect, it } from "vitest";

import { isAssistantPath } from "./isAssistantPath";

// docs/DECISIONS.md § "Stage 17 sub-step order", 17.3b, and § "Stage 17
// block A": the widget shows only on an allowlist of exact paths (no
// trailing slash), so a new page has no widget until it is added.
describe("isAssistantPath", () => {
  it.each(["/", "/services", "/specialists", "/specialists/12", "/reviews", "/booking"])(
    "test_allowed_path_shows_the_widget_(%s)",
    (pathname) => {
      expect(isAssistantPath(pathname)).toBe(true);
    },
  );

  it.each([
    "/booking/pay",
    "/login",
    "/register",
    "/forgot-password",
    "/reset-password",
    "/verify-email",
    "/confirm-email-change",
    "/client",
    "/client/profile/email",
    "/admin",
    "/specialists/12/x",
    "/servicesx",
    "/services/",
  ])("test_excluded_path_has_no_widget_(%s)", (pathname) => {
    expect(isAssistantPath(pathname)).toBe(false);
  });
});
