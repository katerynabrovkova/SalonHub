import { describe, expect, it } from "vitest";

import { groupAvailabilityByDay } from "./groupAvailabilityByDay";

describe("groupAvailabilityByDay", () => {
  it("test_returns_empty_object_for_empty_input", () => {
    expect(groupAvailabilityByDay([])).toEqual({});
  });

  it("test_groups_single_day_single_slot", () => {
    const result = groupAvailabilityByDay(["2026-08-17T09:00:00+03:00"]);

    expect(result).toEqual({
      "2026-08-17": ["2026-08-17T09:00:00+03:00"],
    });
  });

  it("test_groups_multiple_days_into_separate_buckets", () => {
    const result = groupAvailabilityByDay([
      "2026-08-17T09:00:00+03:00",
      "2026-08-18T10:00:00+03:00",
      "2026-08-19T11:00:00+03:00",
    ]);

    expect(Object.keys(result)).toEqual(["2026-08-17", "2026-08-18", "2026-08-19"]);
    expect(result["2026-08-17"]).toEqual(["2026-08-17T09:00:00+03:00"]);
    expect(result["2026-08-18"]).toEqual(["2026-08-18T10:00:00+03:00"]);
    expect(result["2026-08-19"]).toEqual(["2026-08-19T11:00:00+03:00"]);
  });

  it("test_day_with_only_one_slot_alongside_a_day_with_several", () => {
    const result = groupAvailabilityByDay([
      "2026-08-17T09:00:00+03:00",
      "2026-08-17T09:15:00+03:00",
      "2026-08-17T09:30:00+03:00",
      "2026-08-18T14:00:00+03:00",
    ]);

    expect(result["2026-08-17"]).toHaveLength(3);
    expect(result["2026-08-18"]).toEqual(["2026-08-18T14:00:00+03:00"]);
  });

  it("test_sorts_unsorted_times_within_a_day", () => {
    const result = groupAvailabilityByDay([
      "2026-08-17T10:30:00+03:00",
      "2026-08-17T09:00:00+03:00",
      "2026-08-17T09:45:00+03:00",
    ]);

    expect(result["2026-08-17"]).toEqual([
      "2026-08-17T09:00:00+03:00",
      "2026-08-17T09:45:00+03:00",
      "2026-08-17T10:30:00+03:00",
    ]);
  });

  it("test_preserves_order_when_input_already_sorted", () => {
    const alreadySorted = [
      "2026-08-17T09:00:00+03:00",
      "2026-08-17T09:15:00+03:00",
      "2026-08-17T09:30:00+03:00",
    ];

    const result = groupAvailabilityByDay(alreadySorted);

    expect(result["2026-08-17"]).toEqual(alreadySorted);
  });

  it("test_near_midnight_offset_time_stays_on_its_own_local_date", () => {
    // Guards against a future regression where the day key is re-derived
    // via `new Date(...)` instead of string slicing: `Date` formatting
    // methods report in the host machine's own local timezone, not the
    // salon's +03:00, so a time this close to midnight could shift onto the
    // 17th depending on where the code runs. The salon-local date is
    // already sitting in the string itself and must stay on the 16th.
    const result = groupAvailabilityByDay(["2026-09-16T23:45:00+03:00"]);

    expect(result).toEqual({
      "2026-09-16": ["2026-09-16T23:45:00+03:00"],
    });
    expect(result["2026-09-17"]).toBeUndefined();
  });
});
