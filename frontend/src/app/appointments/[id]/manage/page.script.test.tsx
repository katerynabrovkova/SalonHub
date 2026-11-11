/**
 * Guest manage page renders the token-moving inline script in its server
 * HTML (docs/DECISIONS.md "Booking > Guest token", decided 11.11.2026).
 */
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

vi.mock("next/headers", () => ({
  headers: vi.fn(),
}));

vi.mock("./ManageAppointment", () => ({
  default: () => <p>manage-component</p>,
}));

import { headers } from "next/headers";

import { SALON_SLUG_HEADER } from "@/middleware";
import { buildFragmentTokenScript, manageTokenScriptConfig } from "@/lib/security/fragmentTokenScript";

import ManageAppointmentPage from "./page";

const mockedHeaders = vi.mocked(headers);

function withSlug() {
  mockedHeaders.mockResolvedValueOnce({
    get: (name: string) => (name === SALON_SLUG_HEADER ? "bella-demo" : null),
  } as unknown as Awaited<ReturnType<typeof headers>>);
}

describe("guest manage page script", () => {
  it("test_server_html_contains_the_manage_script_for_this_id_before_the_component", async () => {
    withSlug();

    const html = renderToStaticMarkup(
      await ManageAppointmentPage({ params: Promise.resolve({ id: "42" }) }),
    );

    const expected = buildFragmentTokenScript(manageTokenScriptConfig(42));
    expect(expected).not.toBe("");
    const scriptAt = html.indexOf(`<script>${expected}</script>`);
    expect(scriptAt).toBeGreaterThanOrEqual(0);
    expect(scriptAt).toBeLessThan(html.indexOf("manage-component"));
  });
});
