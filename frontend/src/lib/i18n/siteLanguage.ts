/**
 * The site language: the single source of the language the site is shown in
 * (docs/DECISIONS.md § Frontend › "Site language"). It drives `<html lang>`,
 * the `?lang=` of every backend request (added by `apiRequest`,
 * `guestApiRequest`, `internalApiUrl` and the assistant API), and the
 * `language` every booking request sends (stored as the customer's email
 * language).
 *
 * A constant for now. When the UI language switcher exists, this reads the
 * customer's choice instead; the callers stay the same.
 */
export function getSiteLanguage(): string {
  return "uk";
}
