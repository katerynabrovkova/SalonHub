// @vitest-environment jsdom
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

const pushMock = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: pushMock }),
}));

import DateTimeSelectionGrid from "./DateTimeSelectionGrid";

const DATE_FROM = "2026-08-17";

beforeEach(() => {
  pushMock.mockReset();
});

describe("DateTimeSelectionGrid", () => {
  it("test_day_strip_renders_14_days_with_correct_disabled_state", () => {
    render(
      <DateTimeSelectionGrid
        availabilityByDay={{ "2026-08-17": ["2026-08-17T09:00:00+03:00"] }}
        dateFrom={DATE_FROM}
        minDateFrom={DATE_FROM}
        entry="service"
        service="5"
        specialist="any"
      />,
    );

    const dayButtons = screen.getAllByRole("button").filter((button) =>
      /^\d{4}-\d{2}-\d{2}$/.test(button.textContent ?? ""),
    );
    expect(dayButtons).toHaveLength(14);

    expect(screen.getByRole("button", { name: "2026-08-17" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "2026-08-18" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "2026-08-30" })).toBeDisabled();
  });

  it("test_clicking_a_disabled_day_does_not_select_it_or_show_slots", async () => {
    const user = userEvent.setup();
    render(
      <DateTimeSelectionGrid
        availabilityByDay={{ "2026-08-17": ["2026-08-17T09:00:00+03:00"] }}
        dateFrom={DATE_FROM}
        minDateFrom={DATE_FROM}
        entry="service"
        service="5"
        specialist="any"
      />,
    );

    await user.click(screen.getByRole("button", { name: "2026-08-18" }));

    expect(screen.getByRole("button", { name: "2026-08-18" })).toHaveAttribute(
      "aria-pressed",
      "false",
    );
    expect(screen.queryByRole("button", { name: "09:00" })).not.toBeInTheDocument();
  });

  it("test_selecting_an_enabled_day_shows_its_slots", async () => {
    const user = userEvent.setup();
    render(
      <DateTimeSelectionGrid
        availabilityByDay={{
          "2026-08-17": ["2026-08-17T09:00:00+03:00", "2026-08-17T09:15:00+03:00"],
          "2026-08-18": ["2026-08-18T14:00:00+03:00"],
        }}
        dateFrom={DATE_FROM}
        minDateFrom={DATE_FROM}
        entry="service"
        service="5"
        specialist="any"
      />,
    );

    expect(screen.queryByRole("button", { name: "09:00" })).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "2026-08-17" }));

    expect(screen.getByRole("button", { name: "09:00" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "09:15" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "14:00" })).not.toBeInTheDocument();
  });

  it("test_selecting_a_slot_navigates_with_encoded_slot_and_preserved_params", async () => {
    const user = userEvent.setup();
    render(
      <DateTimeSelectionGrid
        availabilityByDay={{ "2026-08-17": ["2026-08-17T09:00:00+03:00"] }}
        dateFrom={DATE_FROM}
        minDateFrom={DATE_FROM}
        entry="specialist"
        service="5"
        specialist="12"
      />,
    );

    await user.click(screen.getByRole("button", { name: "2026-08-17" }));
    await user.click(screen.getByRole("button", { name: "09:00" }));

    expect(pushMock).toHaveBeenCalledWith(
      "/booking?entry=specialist&service=5&specialist=12&step=4&slot=2026-08-17T09%3A00%3A00%2B03%3A00",
    );
  });

  it("test_next_window_link_advances_date_from_by_14_days_and_preserves_params", () => {
    render(
      <DateTimeSelectionGrid
        availabilityByDay={{}}
        dateFrom={DATE_FROM}
        minDateFrom={DATE_FROM}
        entry="service"
        service="5"
        specialist="any"
      />,
    );

    const link = screen.getByRole("link", { name: /далі/i });

    expect(link).toHaveAttribute(
      "href",
      "/booking?entry=service&service=5&specialist=any&step=3&date_from=2026-08-31",
    );
  });

  it("test_previous_window_link_goes_back_14_days_and_preserves_params", () => {
    render(
      <DateTimeSelectionGrid
        availabilityByDay={{}}
        dateFrom="2026-09-01"
        minDateFrom="2026-08-01"
        entry="specialist"
        service="5"
        specialist="12"
      />,
    );

    const link = screen.getByRole("link", { name: /назад/i });

    expect(link).toHaveAttribute(
      "href",
      "/booking?entry=specialist&service=5&specialist=12&step=3&date_from=2026-08-18",
    );
  });

  it("test_previous_window_link_clamps_to_min_date_from", () => {
    render(
      <DateTimeSelectionGrid
        availabilityByDay={{}}
        dateFrom="2026-08-05"
        minDateFrom="2026-08-01"
        entry="service"
        service="5"
        specialist="any"
      />,
    );

    const link = screen.getByRole("link", { name: /назад/i });

    expect(link).toHaveAttribute(
      "href",
      "/booking?entry=service&service=5&specialist=any&step=3&date_from=2026-08-01",
    );
  });

  it("test_no_previous_window_link_when_date_from_equals_min_date_from", () => {
    render(
      <DateTimeSelectionGrid
        availabilityByDay={{}}
        dateFrom="2026-08-01"
        minDateFrom="2026-08-01"
        entry="service"
        service="5"
        specialist="any"
      />,
    );

    expect(screen.queryByRole("link", { name: /назад/i })).not.toBeInTheDocument();
  });
});
