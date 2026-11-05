import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { end, isPending, isVisible, start, subscribe } from "./pendingNavigation";

// 17.6c (docs/DECISIONS.md, "Stage 17 sub-step order"): the store behind the
// site-wide navigation progress. Pending turns on at start(); the visible
// state follows only after 150 ms, so a fast navigation shows nothing; a
// 10 s safety timeout ends a navigation that never reports its end.

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  end();
  vi.useRealTimers();
});

describe("pendingNavigation", () => {
  test("test_start_then_end", () => {
    expect(isPending()).toBe(false);

    start();
    expect(isPending()).toBe(true);

    end();
    expect(isPending()).toBe(false);
    expect(isVisible()).toBe(false);
  });

  test("test_end_after_visible_hides_it", () => {
    start();
    vi.advanceTimersByTime(150);
    expect(isVisible()).toBe(true);

    end();
    expect(isPending()).toBe(false);
    expect(isVisible()).toBe(false);
  });

  test("test_visible_only_after_150_ms", () => {
    start();
    expect(isVisible()).toBe(false);

    vi.advanceTimersByTime(149);
    expect(isVisible()).toBe(false);

    vi.advanceTimersByTime(1);
    expect(isVisible()).toBe(true);
  });

  test("test_end_before_150_ms_never_becomes_visible", () => {
    const seen: boolean[] = [];
    const unsubscribe = subscribe(() => {
      seen.push(isVisible());
    });

    start();
    vi.advanceTimersByTime(100);
    end();
    vi.advanceTimersByTime(10_000);

    expect(isVisible()).toBe(false);
    expect(seen).not.toContain(true);
    unsubscribe();
  });

  test("test_start_is_idempotent_and_keeps_the_show_delay", () => {
    start();
    vi.advanceTimersByTime(100);
    start();
    vi.advanceTimersByTime(50);

    // A second start() must not restart the 150 ms delay.
    expect(isVisible()).toBe(true);
  });

  test("test_start_is_idempotent_and_keeps_the_safety_timeout", () => {
    start();
    vi.advanceTimersByTime(6_000);
    start();
    vi.advanceTimersByTime(4_000);

    // A second start() must not restart the 10 s safety timeout.
    expect(isPending()).toBe(false);
  });

  test("test_one_end_ends_after_several_starts", () => {
    start();
    start();
    end();

    expect(isPending()).toBe(false);
  });

  test("test_safety_timeout_ends_it_after_10_seconds", () => {
    start();
    vi.advanceTimersByTime(9_999);
    expect(isPending()).toBe(true);
    expect(isVisible()).toBe(true);

    vi.advanceTimersByTime(1);
    expect(isPending()).toBe(false);
    expect(isVisible()).toBe(false);
  });

  test("test_subscribers_hear_visible_on_and_off", () => {
    const seen: boolean[] = [];
    const unsubscribe = subscribe(() => {
      seen.push(isVisible());
    });

    start();
    vi.advanceTimersByTime(150);
    end();

    expect(seen).toContain(true);
    expect(seen.at(-1)).toBe(false);
    unsubscribe();
  });

  test("test_unsubscribe_stops_notifications", () => {
    const listener = vi.fn();
    const unsubscribe = subscribe(listener);
    unsubscribe();

    start();
    vi.advanceTimersByTime(150);
    end();

    expect(listener).not.toHaveBeenCalled();
  });
});
