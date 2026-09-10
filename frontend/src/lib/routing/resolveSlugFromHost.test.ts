import { describe, expect, it } from "vitest";

import { resolveSlugFromHost } from "./resolveSlugFromHost";

const PLATFORM_DOMAIN = "salonhub.com";

describe("resolveSlugFromHost", () => {
  describe("dev (localhost:3000 is always the dev apex)", () => {
    it("extracts a plain subdomain slug", () => {
      expect(resolveSlugFromHost("bella.localhost:3000", PLATFORM_DOMAIN)).toBe("bella");
    });

    it("extracts a hyphenated subdomain slug", () => {
      expect(resolveSlugFromHost("bella-demo.localhost:3000", PLATFORM_DOMAIN)).toBe("bella-demo");
    });

    it("returns null for the bare dev apex", () => {
      expect(resolveSlugFromHost("localhost:3000", PLATFORM_DOMAIN)).toBeNull();
    });
  });

  describe("prod", () => {
    it("extracts a subdomain slug under the platform domain", () => {
      expect(resolveSlugFromHost("bella.salonhub.com", PLATFORM_DOMAIN)).toBe("bella");
    });

    it("returns null for the platform apex", () => {
      expect(resolveSlugFromHost("salonhub.com", PLATFORM_DOMAIN)).toBeNull();
    });
  });

  describe("security / anchoring", () => {
    it("rejects a subdomain of a different domain", () => {
      expect(resolveSlugFromHost("bella.evil.com", PLATFORM_DOMAIN)).toBeNull();
    });

    it("rejects the platform domain appearing mid-host", () => {
      expect(
        resolveSlugFromHost("evil.com.salonhub.com.attacker.net", PLATFORM_DOMAIN),
      ).toBeNull();
    });

    it("rejects the platform domain as a prefix of a different domain", () => {
      expect(resolveSlugFromHost("salonhub.com.evil.com", PLATFORM_DOMAIN)).toBeNull();
    });
  });

  describe("edge cases", () => {
    it("returns null for an empty host", () => {
      expect(resolveSlugFromHost("", PLATFORM_DOMAIN)).toBeNull();
    });

    it("treats www as apex-equivalent", () => {
      // Deliberate simplification: "www" is a reserved marketing host, not a
      // salon slug. Treating it as apex-equivalent is intentional for now,
      // not a forgotten case — revisit if a real www-hosted landing app lands.
      expect(resolveSlugFromHost("www.salonhub.com", PLATFORM_DOMAIN)).toBeNull();
    });
  });
});
