import { afterAll, beforeAll, describe, expect, it } from "vitest";

import { formatSalonDateTime } from "./formatSalonDateTime";

// docs/DECISIONS.md § "Appointment times in the salon's time zone", points 2
// and 3: the date and the time come from the ISO string itself, which already
// carries the salon's offset, so the result must not depend on the zone the
// test runner (or the customer's browser) is in.
//
// How the runner zone is changed: Node re-reads the zone whenever
// `process.env.TZ` is assigned at runtime, for both `Date`'s local getters and
// `Intl`'s default zone. Vitest's default `forks` pool runs each test file in
// its own child process, so the change cannot leak into another file; it is
// still restored after each block. The guard test in each block proves the
// switch took effect, so a passing test is never vacuous.
//
// Asia/Tbilisi (+04:00) is ahead of the salon's Kyiv offset and
// America/New_York (-04:00 in summer) behind it, so a formatter that read the
// time back in the runner's zone would show a different hour in both, and a
// different day for 23:30 in Tbilisi.

const CASES = [
  {
    name: "summer_midday",
    iso: "2026-07-15T12:15:00+03:00",
    expected: "15 лип. 2026 р., 12:15",
  },
  {
    name: "late_evening_stays_on_the_same_day",
    iso: "2026-07-15T23:30:00+03:00",
    expected: "15 лип. 2026 р., 23:30",
  },
  {
    name: "winter_offset",
    iso: "2026-01-20T09:00:00+02:00",
    expected: "20 січ. 2026 р., 09:00",
  },
];

describe.each([
  { zone: "Asia/Tbilisi", localHourOf0915Utc: 13 },
  { zone: "America/New_York", localHourOf0915Utc: 5 },
])("formatSalonDateTime under runner zone ($zone)", ({ zone, localHourOf0915Utc }) => {
  let previousTz: string | undefined;

  beforeAll(() => {
    previousTz = process.env.TZ;
    process.env.TZ = zone;
  });

  afterAll(() => {
    if (previousTz === undefined) {
      delete process.env.TZ;
    } else {
      process.env.TZ = previousTz;
    }
  });

  it("test_runner_zone_switch_took_effect", () => {
    expect(Intl.DateTimeFormat().resolvedOptions().timeZone).toBe(zone);
    expect(new Date("2026-07-15T09:15:00Z").getHours()).toBe(localHourOf0915Utc);
  });

  it.each(CASES)("test_formats_the_salon_wall_time_($name)", ({ iso, expected }) => {
    expect(formatSalonDateTime(iso)).toBe(expected);
  });
});
