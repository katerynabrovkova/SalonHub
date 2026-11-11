/**
 * `/booking/pay` renders the token-moving inline script in its server HTML
 * (docs/DECISIONS.md "Booking > Guest token", decided 11.11.2026).
 */
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

vi.mock("next/headers", () => ({
  headers: vi.fn(),
}));

vi.mock("./PaymentStatus", () => ({
  default: () => <p>pay-component</p>,
}));

import { headers } from "next/headers";

import { SALON_SLUG_HEADER } from "@/middleware";
import { buildFragmentTokenScript, PAY_TOKEN_SCRIPT_CONFIG } from "@/lib/security/fragmentTokenScript";

import BookingPayPage from "./page";

const mockedHeaders = vi.mocked(headers);

describe("booking pay page script", () => {
  it("test_server_html_contains_the_pay_script_before_the_component", async () => {
    mockedHeaders.mockResolvedValueOnce({
      get: (name: string) => (name === SALON_SLUG_HEADER ? "bella-demo" : null),
    } as unknown as Awaited<ReturnType<typeof headers>>);

    const html = renderToStaticMarkup(await BookingPayPage());

    const expected = buildFragmentTokenScript(PAY_TOKEN_SCRIPT_CONFIG);
    expect(expected).not.toBe("");
    const scriptAt = html.indexOf(`<script>${expected}</script>`);
    expect(scriptAt).toBeGreaterThanOrEqual(0);
    expect(scriptAt).toBeLessThan(html.indexOf("pay-component"));
  });
});
