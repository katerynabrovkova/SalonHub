/**
 * Guest manage page, server half (docs/DECISIONS.md "Frontend > Guest manage
 * page"): the page sends no Referrer header, and the assistant widget does not
 * show on it.
 */
import { describe, expect, it, vi } from "vitest";

import { isAssistantPath } from "@/lib/assistant/isAssistantPath";

vi.mock("next/headers", () => ({
  headers: vi.fn(),
}));

import { metadata } from "./page";

describe("guest manage page", () => {
  it("test_metadata_sets_no_referrer_and_the_assistant_widget_is_not_shown", async () => {
    // Already true today: the widget allowlist is exact-match only.
    expect(isAssistantPath("/appointments/42/manage")).toBe(false);

    expect(metadata.referrer).toBe("no-referrer");
  });
});
