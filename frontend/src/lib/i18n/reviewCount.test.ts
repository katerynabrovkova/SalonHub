import { describe, expect, it } from "vitest";

import { reviewCount } from "./reviewCount";

// docs/DECISIONS.md § "Stage 17 sub-step order", 17.6-pre: Ukrainian plural
// forms for review counts, shared by SpecialistCard and the assistant card.
describe("reviewCount", () => {
  it.each([
    [1, "1 відгук"],
    [2, "2 відгуки"],
    [4, "4 відгуки"],
    [5, "5 відгуків"],
    [11, "11 відгуків"],
    [12, "12 відгуків"],
    [21, "21 відгук"],
    [22, "22 відгуки"],
    [25, "25 відгуків"],
    [111, "111 відгуків"],
  ])("test_count_uses_the_ukrainian_plural_(%i)", (count, text) => {
    expect(reviewCount(count)).toBe(text);
  });
});
