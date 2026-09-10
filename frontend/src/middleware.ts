import { type NextRequest, NextResponse } from "next/server";

import { resolveSlugFromHost } from "@/lib/routing/resolveSlugFromHost";

// Middleware runs server-side (Edge Runtime), so a plain server env var would
// work here. We use the NEXT_PUBLIC_ name because the active salon slug is also
// useful to client components later, and reading one canonical var keeps the
// two in sync. The literal fallback mirrors the backend's PLATFORM_DOMAIN
// default (config/settings/base.py) — a placeholder domain, not a real one.
const PLATFORM_DOMAIN =
  process.env.NEXT_PUBLIC_PLATFORM_DOMAIN ?? process.env.PLATFORM_DOMAIN ?? "salonhub.com";

/** Header carrying the resolved tenant slug into the downstream request. */
export const SALON_SLUG_HEADER = "x-salon-slug";

export function middleware(request: NextRequest): NextResponse {
  const host = request.headers.get("host") ?? "";
  const slug = resolveSlugFromHost(host, PLATFORM_DOMAIN);

  if (slug === null) {
    // Apex / unrecognized host — pass through untouched. The apex placeholder
    // page is a separate step.
    return NextResponse.next();
  }

  // Idiomatic Next.js way to pass request-scoped data to Server Components:
  // clone the incoming headers, add ours, and hand them back via
  // NextResponse.next({ request }). A cookie would persist to the browser and a
  // rewrite would change the matched route — neither is wanted; we only need
  // the value readable server-side for this one request.
  const requestHeaders = new Headers(request.headers);
  requestHeaders.set(SALON_SLUG_HEADER, slug);

  return NextResponse.next({ request: { headers: requestHeaders } });
}

export const config = {
  // Run on every path except Next.js internals and static assets — the
  // pattern recommended by the Next.js middleware docs.
  matcher: ["/((?!_next/static|_next/image|favicon.ico|.*\\.(?:svg|png|jpg|jpeg|gif|webp)$).*)"],
};
