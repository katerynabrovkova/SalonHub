/**
 * Adds the site language as `lang=` to a salon-relative path, merged with any
 * query string the path already has (docs/DECISIONS.md § Frontend › "Site
 * language" and "API client"). Used by the browser request layer
 * (`apiRequest`, `guestApiRequest`), so callers pass their path unchanged.
 */
import { getSiteLanguage } from "@/lib/i18n/siteLanguage";

export function withSiteLanguage(path: string): string {
  const queryStart = path.indexOf("?");
  const pathname = queryStart === -1 ? path : path.slice(0, queryStart);
  const query = new URLSearchParams(queryStart === -1 ? "" : path.slice(queryStart + 1));
  query.set("lang", getSiteLanguage());
  return `${pathname}?${query.toString()}`;
}
