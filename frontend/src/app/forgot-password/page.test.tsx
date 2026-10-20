// @vitest-environment jsdom
import { render } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

// The page only renders the form (docs/DECISIONS.md § "Item 11 decisions
// (forgot password)"); the form's behaviour is covered by
// ForgotPasswordForm.test.tsx, so it is replaced here by a spy that records
// the props it receives.
let renderCount = 0;
let lastProps: Record<string, unknown> | null = null;
vi.mock("./ForgotPasswordForm", () => ({
  default: (props: Record<string, unknown>) => {
    renderCount += 1;
    lastProps = props;
    return null;
  },
}));

import ForgotPasswordPage from "./page";

async function renderPage(searchParams: { [key: string]: string | string[] | undefined }) {
  const element = await ForgotPasswordPage({ searchParams: Promise.resolve(searchParams) });
  render(element);
}

beforeEach(() => {
  renderCount = 0;
  lastProps = null;
});

describe("ForgotPasswordPage", () => {
  it("test_renders_forgot_password_form", async () => {
    await renderPage({});

    expect(renderCount).toBeGreaterThan(0);
  });

  // Point 8, changed 20.10.2026: the server-side allowlist for `from`.
  it.each([
    { name: "change_password", from: "change-password", backLink: "change-password" },
    { name: "no_parameter", from: undefined, backLink: "login" },
    { name: "unknown_value", from: "profile", backLink: "login" },
    { name: "path", from: "/client/profile/password", backLink: "login" },
    { name: "full_url", from: "https://example.com", backLink: "login" },
  ])("test_from_maps_to_back_link_($name)", async ({ from, backLink }) => {
    await renderPage(from === undefined ? {} : { from });

    expect(lastProps).toEqual({ backLink });
  });
});
