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

  // Idiomatic Next.js way to pass request-scoped data to Server Components:
  // clone the incoming headers, add ours, and hand them back via
  // NextResponse.next({ request }). A cookie would persist to the browser and a
  // rewrite would change the matched route — neither is wanted; we only need
  // the value readable server-side for this one request.
  const requestHeaders = new Headers(request.headers);

  if (slug === null) {
    // Apex / www / unrecognized host — no salon. A client-sent x-salon-slug
    // must never reach the app, or the client would choose the salon
    // (docs/DECISIONS.md § "Stage 17 sub-step order", 17.3a). The apex
    // placeholder page is a separate step.
    requestHeaders.delete(SALON_SLUG_HEADER);
    return NextResponse.next({ request: { headers: requestHeaders } });
  }

  requestHeaders.set(SALON_SLUG_HEADER, slug);

  return NextResponse.next({ request: { headers: requestHeaders } });
}

export const config = {
  // Run on every path except Next.js internals and static assets — the
  // pattern recommended by the Next.js middleware docs.
  matcher: ["/((?!_next/static|_next/image|favicon.ico|.*\\.(?:svg|png|jpg|jpeg|gif|webp)$).*)"],
};
