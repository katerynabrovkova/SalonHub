/**
 * Format a backend price/amount string with its ISO 4217 currency code for
 * display (docs/DECISIONS.md § "Resolution: frontend price display shows no
 * currency unit", part (C)). Locale is hardcoded to 'uk-UA', matching the
 * current all-Ukrainian UI chrome — not the visitor's browser locale.
 */

export function formatPrice(price: string, currencyCode: string): string {
  return new Intl.NumberFormat("uk-UA", { style: "currency", currency: currencyCode }).format(
    Number(price),
  );
}
