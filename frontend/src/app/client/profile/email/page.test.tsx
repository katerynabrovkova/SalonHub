// @vitest-environment jsdom
import { render } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

// The page only renders the form (docs/DECISIONS.md § "Item 8 decisions
// (change email)"); the form's behaviour is covered by
// ChangeEmailForm.test.tsx, so it is replaced here by a spy.
let renderCount = 0;
vi.mock("./ChangeEmailForm", () => ({
  default: () => {
    renderCount += 1;
    return null;
  },
}));

import ChangeEmailPage from "./page";

describe("ChangeEmailPage", () => {
  it("test_renders_change_email_form", () => {
    render(<ChangeEmailPage />);

    expect(renderCount).toBeGreaterThan(0);
  });
});
