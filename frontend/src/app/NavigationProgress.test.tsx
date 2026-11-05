// @vitest-environment jsdom
import { act, fireEvent, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

// Mutable location read by the mocked hooks, so a test can finish a
// navigation by changing it and calling rerender(). useSearchParams returns
// the same object until the search string changes, as Next's does.
const nav = vi.hoisted(() => ({
  pathname: "/booking",
  search: "step=3",
  params: null as URLSearchParams | null,
  paramsFor: "",
}));

vi.mock("next/navigation", () => ({
  usePathname: () => nav.pathname,
  useSearchParams: () => {
    if (nav.params === null || nav.paramsFor !== nav.search) {
      nav.params = new URLSearchParams(nav.search);
      nav.paramsFor = nav.search;
    }
    return nav.params;
  },
}));

import { end, isPending } from "@/lib/navigation/pendingNavigation";

import NavigationProgress from "./NavigationProgress";

// 17.6c (docs/DECISIONS.md, "Stage 17 sub-step order"): a capture-phase
// click listener starts the pending state; a pathname or search-params
// change ends it. Visible only after 150 ms: a thin aria-hidden bar and an
// <html data-navigating> attribute.
//
// fireEvent, not userEvent, under fake timers (see CLAUDE.md and
// PasswordField.test.tsx's test_stays_shown_after_60_seconds).

const anchors: HTMLAnchorElement[] = [];

// A link outside the component, the way page content is. Its own bubble
// listener cancels the default action like next/link does, so jsdom never
// attempts a real navigation.
function addLink(href: string): HTMLAnchorElement {
  const anchor = document.createElement("a");
  anchor.setAttribute("href", href);
  anchor.textContent = href;
  anchor.addEventListener("click", (event) => event.preventDefault());
  document.body.appendChild(anchor);
  anchors.push(anchor);
  return anchor;
}

function bar(container: HTMLElement): Element | null {
  return container.querySelector('[aria-hidden="true"]');
}

function advance(ms: number) {
  act(() => {
    vi.advanceTimersByTime(ms);
  });
}

beforeEach(() => {
  vi.useFakeTimers();
  nav.pathname = "/booking";
  nav.search = "step=3";
  window.history.replaceState(null, "", "/booking?step=3");
});

afterEach(() => {
  act(() => {
    end();
  });
  vi.useRealTimers();
  for (const anchor of anchors.splice(0)) {
    anchor.remove();
  }
  document.documentElement.removeAttribute("data-navigating");
});

describe("NavigationProgress", () => {
  test("test_tracked_click_shows_bar_and_attribute_only_after_150_ms", () => {
    const { container } = render(<NavigationProgress />);
    expect(bar(container)).toBeNull();

    fireEvent.click(addLink("/booking?step=4"));
    advance(149);
    expect(bar(container)).toBeNull();
    expect(document.documentElement).not.toHaveAttribute("data-navigating");

    advance(1);
    expect(bar(container)).not.toBeNull();
    expect(document.documentElement).toHaveAttribute("data-navigating");
  });

  test("test_click_inside_a_link_is_tracked", () => {
    const { container } = render(<NavigationProgress />);
    const inner = document.createElement("span");
    addLink("/services").appendChild(inner);

    fireEvent.click(inner);
    advance(150);

    expect(bar(container)).not.toBeNull();
    expect(document.documentElement).toHaveAttribute("data-navigating");
  });

  test("test_pathname_change_hides_bar_and_removes_attribute", () => {
    const { container, rerender } = render(<NavigationProgress />);
    fireEvent.click(addLink("/services"));
    advance(150);
    expect(bar(container)).not.toBeNull();

    nav.pathname = "/services";
    nav.search = "";
    rerender(<NavigationProgress />);

    expect(document.documentElement).not.toHaveAttribute("data-navigating");
    expect(isPending()).toBe(false);
    advance(200);
    expect(bar(container)).toBeNull();
  });

  test("test_search_params_change_hides_bar_and_removes_attribute", () => {
    const { container, rerender } = render(<NavigationProgress />);
    fireEvent.click(addLink("/booking?step=4"));
    advance(150);
    expect(bar(container)).not.toBeNull();

    nav.search = "step=4";
    rerender(<NavigationProgress />);

    expect(document.documentElement).not.toHaveAttribute("data-navigating");
    expect(isPending()).toBe(false);
    advance(200);
    expect(bar(container)).toBeNull();
  });

  // Progress look (decided 05.11.2026): a running bar grows by CSS; when the
  // URL changes it stays ~200 ms in a finishing state (fill and fade), then
  // is removed. The <html> attribute follows the pending state, not the fade.
  test("test_visible_bar_is_running", () => {
    const { container } = render(<NavigationProgress />);
    fireEvent.click(addLink("/services"));
    advance(150);

    expect(bar(container)).toHaveAttribute("data-state", "running");
  });

  test("test_url_change_keeps_bar_finishing_for_200_ms_then_removes_it", () => {
    const { container, rerender } = render(<NavigationProgress />);
    fireEvent.click(addLink("/services"));
    advance(150);

    nav.pathname = "/services";
    nav.search = "";
    rerender(<NavigationProgress />);
    expect(bar(container)).toHaveAttribute("data-state", "finishing");

    advance(199);
    expect(bar(container)).toHaveAttribute("data-state", "finishing");

    advance(1);
    expect(bar(container)).toBeNull();
  });

  test("test_url_change_removes_attribute_at_once_not_after_the_fade", () => {
    const { container, rerender } = render(<NavigationProgress />);
    fireEvent.click(addLink("/booking?step=4"));
    advance(150);
    expect(document.documentElement).toHaveAttribute("data-navigating");

    nav.search = "step=4";
    rerender(<NavigationProgress />);

    expect(document.documentElement).not.toHaveAttribute("data-navigating");
    expect(bar(container)).not.toBeNull();
  });

  test("test_url_change_before_visible_shows_nothing", () => {
    const { container, rerender } = render(<NavigationProgress />);
    fireEvent.click(addLink("/services"));
    advance(100);

    nav.pathname = "/services";
    nav.search = "";
    rerender(<NavigationProgress />);

    expect(bar(container)).toBeNull();
    advance(200);
    expect(bar(container)).toBeNull();
  });

  test("test_rerender_without_location_change_keeps_it", () => {
    const { container, rerender } = render(<NavigationProgress />);
    fireEvent.click(addLink("/booking?step=4"));
    advance(150);

    rerender(<NavigationProgress />);

    expect(bar(container)).not.toBeNull();
    expect(document.documentElement).toHaveAttribute("data-navigating");
  });

  test("test_same_url_click_does_nothing", () => {
    const { container } = render(<NavigationProgress />);

    fireEvent.click(addLink("/booking?step=3"));
    advance(1_000);

    expect(isPending()).toBe(false);
    expect(bar(container)).toBeNull();
    expect(document.documentElement).not.toHaveAttribute("data-navigating");
  });

  test("test_ctrl_click_does_nothing", () => {
    const { container } = render(<NavigationProgress />);

    fireEvent.click(addLink("/services"), { ctrlKey: true });
    advance(1_000);

    expect(isPending()).toBe(false);
    expect(bar(container)).toBeNull();
    expect(document.documentElement).not.toHaveAttribute("data-navigating");
  });

  test("test_unmount_removes_the_click_listener", () => {
    const { unmount } = render(<NavigationProgress />);
    unmount();

    fireEvent.click(addLink("/services"));
    advance(1_000);

    expect(isPending()).toBe(false);
    expect(document.documentElement).not.toHaveAttribute("data-navigating");
  });
});
