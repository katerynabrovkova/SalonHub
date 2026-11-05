// @vitest-environment jsdom
import { describe, expect, it, test } from "vitest";

import { shouldTrackClick } from "./shouldTrackClick";

// 17.6c (docs/DECISIONS.md, "Stage 17 sub-step order"): the pure classifier
// behind the capture-phase click listener. Tracks plain left clicks on
// same-origin links to a different URL; everything the browser handles
// itself, or that does not change the route, is ignored.

const CURRENT = "http://localhost:3000/booking?step=3";

function link(href: string, attributes: Record<string, string> = {}): HTMLAnchorElement {
  const anchor = document.createElement("a");
  anchor.setAttribute("href", href);
  for (const [name, value] of Object.entries(attributes)) {
    anchor.setAttribute(name, value);
  }
  return anchor;
}

function click(init: MouseEventInit = {}): MouseEvent {
  return new MouseEvent("click", { button: 0, bubbles: true, cancelable: true, ...init });
}

describe("shouldTrackClick", () => {
  test("test_plain_left_click_on_same_origin_link_to_other_url_is_tracked", () => {
    expect(shouldTrackClick(click(), link("/booking?step=4"), CURRENT)).toBe(true);
  });

  test("test_plain_left_click_to_other_path_is_tracked", () => {
    expect(shouldTrackClick(click(), link("/services"), CURRENT)).toBe(true);
  });

  test("test_absolute_same_origin_link_is_tracked", () => {
    expect(shouldTrackClick(click(), link("http://localhost:3000/services"), CURRENT)).toBe(true);
  });

  test("test_target_self_is_tracked", () => {
    expect(shouldTrackClick(click(), link("/services", { target: "_self" }), CURRENT)).toBe(true);
  });

  it.each([
    { name: "ctrl", init: { ctrlKey: true } },
    { name: "meta", init: { metaKey: true } },
    { name: "shift", init: { shiftKey: true } },
    { name: "alt", init: { altKey: true } },
  ])("test_modifier_click_is_not_tracked_($name)", ({ init }) => {
    expect(shouldTrackClick(click(init), link("/services"), CURRENT)).toBe(false);
  });

  test("test_middle_click_is_not_tracked", () => {
    expect(shouldTrackClick(click({ button: 1 }), link("/services"), CURRENT)).toBe(false);
  });

  test("test_target_blank_is_not_tracked", () => {
    expect(shouldTrackClick(click(), link("/services", { target: "_blank" }), CURRENT)).toBe(
      false,
    );
  });

  test("test_download_link_is_not_tracked", () => {
    expect(shouldTrackClick(click(), link("/services", { download: "" }), CURRENT)).toBe(false);
  });

  test("test_external_link_is_not_tracked", () => {
    expect(shouldTrackClick(click(), link("https://example.com/services"), CURRENT)).toBe(false);
  });

  test("test_hash_only_link_is_not_tracked", () => {
    expect(shouldTrackClick(click(), link("#details"), CURRENT)).toBe(false);
  });

  test("test_same_url_with_other_hash_is_not_tracked", () => {
    expect(shouldTrackClick(click(), link("/booking?step=3#details"), CURRENT)).toBe(false);
  });

  test("test_same_url_link_is_not_tracked", () => {
    expect(shouldTrackClick(click(), link("/booking?step=3"), CURRENT)).toBe(false);
  });
});
