// @vitest-environment jsdom
import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { end, start } from "@/lib/navigation/pendingNavigation";

import { useBookingContactInfo } from "./BookingContactInfoContext";
import BookingLayout from "./layout";

// 17.6d (docs/DECISIONS.md, "Stage 17 sub-step order"): while the pending
// navigation is visible (after 150 ms), the step is wrapped with inert,
// aria-busy="true" and a dim style. Drives the real pendingNavigation store.
//
// fireEvent, not userEvent, under fake timers (see CLAUDE.md and
// PasswordField.test.tsx's test_stays_shown_after_60_seconds).

// A step that reads and writes the contact-info provider, so a remount of
// the provider (lost state) would show up.
function Step() {
  const { customerName, setCustomerName } = useBookingContactInfo();
  return (
    <div>
      <p>Крок: {customerName || "порожньо"}</p>
      <button type="button" onClick={() => setCustomerName("Олена")}>
        Ввести ім&apos;я
      </button>
    </div>
  );
}

function renderLayout() {
  const result = render(
    <BookingLayout>
      <Step />
    </BookingLayout>,
  );
  return { ...result, wrapper: () => result.container.firstElementChild as HTMLElement };
}

function advance(ms: number) {
  act(() => {
    vi.advanceTimersByTime(ms);
  });
}

function expectNotBusy(wrapper: HTMLElement) {
  expect(wrapper).not.toHaveAttribute("inert");
  expect(wrapper).not.toHaveAttribute("aria-busy");
  expect(wrapper).not.toHaveClass("opacity-50");
}

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  act(() => {
    end();
  });
  vi.useRealTimers();
});

describe("BookingLayout", () => {
  test("test_not_dimmed_or_inert_before_150_ms", () => {
    const { wrapper } = renderLayout();
    expectNotBusy(wrapper());

    act(() => {
      start();
    });
    advance(149);

    expectNotBusy(wrapper());
  });

  test("test_visible_pending_makes_the_step_inert_busy_and_dimmed", () => {
    const { wrapper } = renderLayout();

    act(() => {
      start();
    });
    advance(150);

    expect(wrapper()).toHaveAttribute("inert");
    expect(wrapper()).toHaveAttribute("aria-busy", "true");
    expect(wrapper()).toHaveClass("opacity-50");
    expect(wrapper()).toContainElement(screen.getByText(/^Крок:/));
  });

  test("test_navigation_end_removes_inert_busy_and_dim", () => {
    const { wrapper } = renderLayout();
    act(() => {
      start();
    });
    advance(150);
    expect(wrapper()).toHaveAttribute("inert");

    act(() => {
      end();
    });

    expectNotBusy(wrapper());
  });

  test("test_children_and_contact_info_provider_render_in_both_states", () => {
    renderLayout();
    fireEvent.click(screen.getByRole("button", { name: "Ввести ім'я" }));
    expect(screen.getByText("Крок: Олена")).toBeInTheDocument();

    act(() => {
      start();
    });
    advance(150);
    expect(screen.getByText("Крок: Олена")).toBeInTheDocument();

    act(() => {
      end();
    });
    expect(screen.getByText("Крок: Олена")).toBeInTheDocument();
  });
});
