import { NextRequest, type NextResponse } from "next/server";
import { describe, expect, it } from "vitest";

import { middleware, SALON_SLUG_HEADER } from "./middleware";

// docs/DECISIONS.md § "Stage 17 sub-step order", 17.3a: the salon is always
// resolved by the server from the host, never chosen by a client-sent
// x-salon-slug header. Hosts use the dev `localhost` suffix, which
// resolveSlugFromHost treats the same regardless of PLATFORM_DOMAIN.

function requestFor(host: string, spoofedSlug: string): NextRequest {
  return new NextRequest(`http://${host}/services`, {
    headers: { host, [SALON_SLUG_HEADER]: spoofedSlug },
  });
}

/**
 * The request headers the app receives after the middleware ran. Mirrors how
 * Next.js applies a middleware response (next/dist/server/lib/router-utils/
 * resolve-routes.js, the `x-middleware-override-headers` branch): without
 * that header the incoming request goes through unchanged; with it, only the
 * listed headers are kept, each taking its `x-middleware-request-<name>`
 * value.
 */
function forwardedRequestHeaders(request: NextRequest, response: NextResponse): Headers {
  const override = response.headers.get("x-middleware-override-headers");
  if (override === null) {
    return new Headers(request.headers);
  }
  const forwarded = new Headers();
  for (const name of override.split(",").map((key) => key.trim())) {
    const value = response.headers.get(`x-middleware-request-${name}`);
    if (name !== "" && value !== null) {
      forwarded.set(name, value);
    }
  }
  return forwarded;
}

describe("middleware", () => {
  it.each([
    { name: "apex", host: "localhost:3000" },
    { name: "www", host: "www.localhost:3000" },
    { name: "unrecognised_host", host: "example.org" },
  ])(
    "test_client_sent_salon_header_is_removed_without_a_resolved_salon_($name)",
    ({ host }) => {
      const request = requestFor(host, "bella-demo");

      const forwarded = forwardedRequestHeaders(request, middleware(request));

      expect(forwarded.get(SALON_SLUG_HEADER)).toBeNull();
      // Only the spoofed header is dropped; the rest of the request is kept.
      expect(forwarded.get("host")).toBe(host);
    },
  );

  it("test_salon_subdomain_gets_its_own_slug_over_a_client_sent_one", () => {
    const request = requestFor("bella-demo.localhost:3000", "other-salon");

    const forwarded = forwardedRequestHeaders(request, middleware(request));

    expect(forwarded.get(SALON_SLUG_HEADER)).toBe("bella-demo");
  });
});
