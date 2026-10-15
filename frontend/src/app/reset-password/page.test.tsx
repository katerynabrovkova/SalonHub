// @vitest-environment jsdom
import { render } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

// The page only resolves the slug and renders the form (docs/DECISIONS.md §
// "Item 11 decisions (forgot password)"); the form's behaviour is covered
// by ResetPasswordForm.test.tsx, so it is replaced here by a spy.
vi.mock("next/headers", () => ({
  headers: vi.fn(),
}));

const renderedProps: { slug: string }[] = [];
vi.mock("./ResetPasswordForm", () => ({
  default: (props: { slug: string }) => {
    renderedProps.push(props);
    return null;
  },
}));

// Imported after the mocks above so the mocked modules are what page.tsx sees.
import { SALON_SLUG_HEADER } from "@/middleware";
import { headers } from "next/headers";

import ResetPasswordPage from "./page";

const mockedHeaders = vi.mocked(headers);

describe("ResetPasswordPage", () => {
  it("test_renders_reset_password_form_with_slug_from_header", async () => {
    mockedHeaders.mockResolvedValueOnce({
      get: (name: string) => (name === SALON_SLUG_HEADER ? "bella-demo" : null),
    } as unknown as Awaited<ReturnType<typeof headers>>);

    render(await ResetPasswordPage());

    expect(renderedProps.at(-1)).toEqual({ slug: "bella-demo" });
  });
});
