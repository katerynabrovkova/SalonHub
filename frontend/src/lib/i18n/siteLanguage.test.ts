import { describe, expect, it } from "vitest";

import { getSiteLanguage } from "./siteLanguage";

// docs/DECISIONS.md § Frontend › "Site language": one module is the single
// source of the site language; today it returns "uk".
describe("getSiteLanguage", () => {
  it("test_returns_uk", () => {
    expect(getSiteLanguage()).toBe("uk");
  });
});
