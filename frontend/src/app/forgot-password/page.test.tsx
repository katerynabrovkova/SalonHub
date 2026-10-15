// @vitest-environment jsdom
import { render } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

// The page only renders the form (docs/DECISIONS.md § "Item 11 decisions
// (forgot password)"); the form's behaviour is covered by
// ForgotPasswordForm.test.tsx, so it is replaced here by a spy.
let renderCount = 0;
vi.mock("./ForgotPasswordForm", () => ({
  default: () => {
    renderCount += 1;
    return null;
  },
}));

import ForgotPasswordPage from "./page";

describe("ForgotPasswordPage", () => {
  it("test_renders_forgot_password_form", () => {
    render(<ForgotPasswordPage />);

    expect(renderCount).toBeGreaterThan(0);
  });
});
