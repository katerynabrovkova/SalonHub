import type { ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

// docs/DECISIONS.md § Frontend › "Site language": <html lang> reads the site
// language from lib/i18n/siteLanguage.ts. Mocked to "en", not the real
// "uk", so the test proves the layout reads the module rather than its own
// literal.
//
// Rendered to static markup in the node environment, not with Testing
// Library: render() mounts into a <div>, where an <html> element is invalid
// nesting. Everything the layout renders around `children` is mocked; the
// only thing under test is the <html> element's lang attribute.
vi.mock("@/lib/i18n/siteLanguage", () => ({
  getSiteLanguage: () => "en",
}));

vi.mock("next/font/google", () => ({
  Geist: () => ({ variable: "font-sans" }),
  Geist_Mono: () => ({ variable: "font-mono" }),
}));

vi.mock("./AssistantWidgetLoader", () => ({ default: () => null }));
vi.mock("./NavigationProgress", () => ({ default: () => null }));
vi.mock("./AuthContext", () => ({
  AuthProvider: ({ children }: { children: ReactNode }) => <>{children}</>,
}));

import RootLayout from "./layout";

describe("RootLayout", () => {
  it("test_html_lang_is_the_site_language", () => {
    const markup = renderToStaticMarkup(
      <RootLayout {...({ children: <main>page</main> } as unknown as LayoutProps<"/">)} />,
    );

    expect(markup).toMatch(/^<html[^>]*\slang="en"/);
  });
});
