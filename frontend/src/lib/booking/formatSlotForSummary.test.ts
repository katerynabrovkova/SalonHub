import { describe, expect, it } from "vitest";

import { formatSlotForSummary } from "./formatSlotForSummary";

describe("formatSlotForSummary", () => {
  it("test_formats_a_normal_slot_as_date_comma_time", () => {
    expect(formatSlotForSummary("2026-09-24T14:30:00+03:00")).toBe("24 вересня, 14:30");
  });

  it("test_uses_the_iso_strings_own_wall_date_not_its_utc_date", () => {
    // +09:00 means this instant's UTC date is the 23rd, but the string's own
    // calendar date -- the salon-local wall date the customer actually
    // picked -- is the 24th. `new Date(iso)` in the test runner's local
    // timezone must never leak in here.
    expect(formatSlotForSummary("2026-09-24T01:30:00+09:00")).toBe("24 вересня, 01:30");
  });
});
