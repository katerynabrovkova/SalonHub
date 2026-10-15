// @vitest-environment jsdom
import { render } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

// The page only renders the form (docs/DECISIONS.md § "Item 9 decisions
// (change password): ending sessions"); the form's behaviour is covered by
// ChangePasswordForm.test.tsx, so it is replaced here by a spy.
let renderCount = 0;
vi.mock("./ChangePasswordForm", () => ({
  default: () => {
    renderCount += 1;
    return null;
  },
}));

import ChangePasswordPage from "./page";

describe("ChangePasswordPage", () => {
  it("test_renders_change_password_form", () => {
    render(<ChangePasswordPage />);

    expect(renderCount).toBeGreaterThan(0);
  });
});
