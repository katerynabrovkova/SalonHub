import { describe, expect, it } from "vitest";

import { formatPrice } from "./formatPrice";

describe("formatPrice", () => {
  it("test_formats_uah_with_uk_ua_grouping_and_currency_symbol", () => {
    // Real Intl.NumberFormat('uk-UA', { style: 'currency', currency: 'UAH' })
    // output for 1234.50, confirmed via `node -e` in the frontend container
    // (not a guessed string): U+00A0 (non-breaking space) as the grouping
    // separator and before the symbol, comma as the decimal separator, ₴
    // after the amount.
    expect(formatPrice("1234.50", "UAH")).toBe("1 234,50 ₴");
  });

  it("test_same_amount_different_currency_produces_a_different_symbol", () => {
    // Same 1234.50 value, currency=USD: the symbol/format genuinely differs
    // from the UAH case above (USD has no dedicated single-glyph symbol in
    // uk-UA's currency-display data, so ICU prints the ISO code "USD"
    // instead of "$") — proves the output is currency-driven, not a
    // hardcoded suffix (docs/DECISIONS.md § "not a hardcoded-symbol fix").
    expect(formatPrice("1234.50", "USD")).toBe("1 234,50 USD");
  });

  it("test_currency_with_fewer_minor_units_than_the_input_pins_current_rounding_behavior", () => {
    // JPY has 0 minor units (no decimal places), unlike the 2-decimal input.
    // This pins whatever Intl.NumberFormat actually does today (round to 0
    // decimals) — it documents current behavior only, the same precedent as
    // groupAvailabilityByDay.test.ts's near-midnight DST-adjacent case, not
    // a claim about what the "correct" rounding rule should be.
    expect(formatPrice("100.00", "JPY")).toBe("100 ¥");
  });

  it("test_non_numeric_price_string_pins_current_defined_behavior", () => {
    // Real backend Decimal fields (Service.price, Payment.amount) never
    // produce a non-numeric string — this only documents what
    // Number()-coercion-then-Intl.NumberFormat does for an unexpected input,
    // not a contract any caller should rely on.
    expect(formatPrice("abc", "UAH")).toBe("NaN ₴");
  });
});
