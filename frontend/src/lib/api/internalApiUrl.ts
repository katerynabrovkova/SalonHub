/**
 * The one builder of server-side backend URLs (docs/DECISIONS.md § Frontend ›
 * "Site language" and "API client"). Every Server Component fetcher calls it,
 * so every request carries the site language as `lang=` and no new page can
 * forget it. An ESLint rule keeps `process.env.INTERNAL_API_URL` out of every
 * other file.
 *
 * `params` keep their order; `lang` always comes last and always from
 * `lib/i18n/siteLanguage.ts`, overriding any `lang` a caller passed.
 */
import { getSiteLanguage } from "@/lib/i18n/siteLanguage";

export function internalApiUrl(
  slug: string,
  path: string,
  params: Record<string, string> = {},
): string {
  const query = new URLSearchParams(params);
  query.set("lang", getSiteLanguage());
  return `${process.env.INTERNAL_API_URL}/api/v1/salons/${slug}${path}?${query.toString()}`;
}
