/**
 * Resolve the active salon slug from an incoming request's Host header.
 *
 * Each tenant is served from its own subdomain — `<slug>.salonhub.com` in
 * production, `<slug>.localhost:3000` in development (docs/DECISIONS.md
 * § "Frontend routing: subdomain-based"). This is the pure parsing core used
 * by the Next.js middleware; it takes no globals so it is trivially testable.
 *
 * Returns the slug, or `null` when the host is an apex / unrecognized host
 * that should NOT be treated as a tenant.
 */

// A slug is a single DNS label: lowercase alphanumerics and hyphens. Anchored
// on both ends so a multi-label or otherwise malformed left-hand side (which a
// spoofed host could produce) is rejected rather than passed through.
const SLUG_RE = /^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$/;

// "www" is a reserved marketing host, not a salon slug. Treating it as
// apex-equivalent is a deliberate simplification for now — revisit if a real
// www-hosted app is ever introduced. Not an oversight.
const RESERVED_SUBDOMAINS = new Set(["www"]);

function stripPort(host: string): string {
  // Strip a trailing ":<port>" only — nothing else about the host.
  return host.replace(/:\d+$/, "");
}

function slugFromSuffix(hostname: string, suffix: string): string | null {
  // Anchored suffix match: the hostname must END with `.<suffix>`, and there
  // must be a non-empty label before it. A substring/`includes` check here is
  // the anchoring bug (`salonhub.com.evil.com`) — do not reintroduce it.
  const dottedSuffix = `.${suffix}`;
  if (!hostname.endsWith(dottedSuffix)) {
    return null;
  }
  const candidate = hostname.slice(0, -dottedSuffix.length);
  if (!SLUG_RE.test(candidate) || RESERVED_SUBDOMAINS.has(candidate)) {
    return null;
  }
  return candidate;
}

export function resolveSlugFromHost(host: string, platformDomain: string): string | null {
  if (!host) {
    return null;
  }

  const hostname = stripPort(host);

  // Dev: `localhost:3000` is always the dev apex, regardless of platformDomain.
  const devSlug = slugFromSuffix(hostname, "localhost");
  if (devSlug !== null) {
    return devSlug;
  }

  // Prod: anchored suffix match on the full platform domain.
  return slugFromSuffix(hostname, platformDomain);
}
