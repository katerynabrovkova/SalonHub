// @vitest-environment jsdom
import { renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

// Same mock shape as the existing call-site tests (only `useRouter`, no
// `usePathname` / `useSearchParams`): the hook must keep working under it,
// so those tests need no change when the call sites switch to it.
const pushMock = vi.fn();
const replaceMock = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: pushMock, replace: replaceMock }),
}));

import { end, isPending } from "./pendingNavigation";
import { useNavigatingRouter } from "./useNavigatingRouter";

// 17.6c (docs/DECISIONS.md, "Stage 17 sub-step order"): push/replace call
// start() before the router when the target differs from the current URL.
// "Before" is checked by reading the real store from inside the router mock.

let pendingAtPush: boolean[];
let pendingAtReplace: boolean[];

beforeEach(() => {
  window.history.replaceState(null, "", "/booking?step=3");
  pendingAtPush = [];
  pendingAtReplace = [];
  pushMock.mockReset();
  replaceMock.mockReset();
  pushMock.mockImplementation(() => {
    pendingAtPush.push(isPending());
  });
  replaceMock.mockImplementation(() => {
    pendingAtReplace.push(isPending());
  });
});

afterEach(() => {
  end();
});

describe("useNavigatingRouter", () => {
  test("test_push_to_other_url_starts_before_router_push", () => {
    const { result } = renderHook(() => useNavigatingRouter());

    result.current.push("/booking?step=4");

    expect(pushMock).toHaveBeenCalledTimes(1);
    expect(pushMock).toHaveBeenCalledWith("/booking?step=4");
    expect(pendingAtPush).toEqual([true]);
  });

  test("test_replace_to_other_url_starts_before_router_replace", () => {
    const { result } = renderHook(() => useNavigatingRouter());

    result.current.replace("/login");

    expect(replaceMock).toHaveBeenCalledTimes(1);
    expect(replaceMock).toHaveBeenCalledWith("/login");
    expect(pendingAtReplace).toEqual([true]);
  });

  test("test_push_to_current_url_does_not_start", () => {
    const { result } = renderHook(() => useNavigatingRouter());

    result.current.push("/booking?step=3");

    expect(pushMock).toHaveBeenCalledWith("/booking?step=3");
    expect(pendingAtPush).toEqual([false]);
    expect(isPending()).toBe(false);
  });

  test("test_replace_to_current_url_does_not_start", () => {
    const { result } = renderHook(() => useNavigatingRouter());

    result.current.replace("/booking?step=3");

    expect(replaceMock).toHaveBeenCalledWith("/booking?step=3");
    expect(isPending()).toBe(false);
  });

  test("test_push_to_current_url_with_other_hash_does_not_start", () => {
    // A hash-only change does not change pathname or search params, so
    // NavigationProgress would never see it end; same rule as the click
    // classifier's hash-only links.
    const { result } = renderHook(() => useNavigatingRouter());

    result.current.push("/booking?step=3#details");

    expect(pushMock).toHaveBeenCalledWith("/booking?step=3#details");
    expect(isPending()).toBe(false);
  });

  test("test_arguments_are_forwarded_unchanged", () => {
    // The existing call-site tests assert toHaveBeenCalledWith(url) on the
    // mocked router, so the hook must not add arguments of its own.
    const { result } = renderHook(() => useNavigatingRouter());

    result.current.push("/services");
    result.current.push("/specialists", { scroll: false });

    expect(pushMock.mock.calls).toEqual([["/services"], ["/specialists", { scroll: false }]]);
    expect(pushMock.mock.calls[0]).toHaveLength(1);
  });
});
