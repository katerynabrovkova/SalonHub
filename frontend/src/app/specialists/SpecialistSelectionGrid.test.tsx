// @vitest-environment jsdom
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

const pushMock = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: pushMock }),
}));

import type { Specialist } from "@/lib/specialists/getSpecialistsPage";

import SpecialistSelectionGrid from "./SpecialistSelectionGrid";

function specialist(overrides: Partial<Specialist> = {}): Specialist {
  return {
    id: 1,
    salon: 1,
    name: "Olena",
    bio: "Nail artist",
    photo: null,
    is_active: true,
    services: [{ id: 10, name: "Manicure" }],
    average_rating: 4.5,
    review_count: 2,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

beforeEach(() => {
  pushMock.mockReset();
});

describe("SpecialistSelectionGrid", () => {
  it("test_confirm_button_disabled_when_nothing_selected", () => {
    render(<SpecialistSelectionGrid specialists={[specialist()]} serviceId={42} />);

    expect(screen.getByRole("button", { name: /продовж/i })).toBeDisabled();
  });

  it("test_confirm_button_enabled_after_selecting_a_specialist", async () => {
    const user = userEvent.setup();
    render(<SpecialistSelectionGrid specialists={[specialist({ name: "Olena" })]} serviceId={42} />);

    await user.click(screen.getByRole("radio", { name: "Olena" }));

    expect(screen.getByRole("button", { name: /продовж/i })).toBeEnabled();
  });

  it("test_confirm_button_enabled_after_selecting_any_specialist", async () => {
    const user = userEvent.setup();
    render(<SpecialistSelectionGrid specialists={[specialist()]} serviceId={42} />);

    await user.click(screen.getByRole("radio", { name: "Будь-який спеціаліст" }));

    expect(screen.getByRole("button", { name: /продовж/i })).toBeEnabled();
  });

  it("test_selecting_specialist_card_marks_it_selected_without_navigating", async () => {
    const user = userEvent.setup();
    render(<SpecialistSelectionGrid specialists={[specialist({ name: "Olena" })]} serviceId={42} />);

    const radio = screen.getByRole("radio", { name: "Olena" });
    expect(radio).not.toBeChecked();

    await user.click(radio);

    expect(radio).toBeChecked();
    expect(pushMock).not.toHaveBeenCalled();
  });

  it("test_selecting_any_specialist_marks_it_selected_without_navigating", async () => {
    const user = userEvent.setup();
    render(<SpecialistSelectionGrid specialists={[specialist()]} serviceId={42} />);

    const anyRadio = screen.getByRole("radio", { name: "Будь-який спеціаліст" });
    expect(anyRadio).not.toBeChecked();

    await user.click(anyRadio);

    expect(anyRadio).toBeChecked();
    expect(pushMock).not.toHaveBeenCalled();
  });

  it("test_selecting_a_specialist_clears_a_prior_any_selection", async () => {
    const user = userEvent.setup();
    render(<SpecialistSelectionGrid specialists={[specialist({ name: "Olena" })]} serviceId={42} />);

    const anyRadio = screen.getByRole("radio", { name: "Будь-який спеціаліст" });
    const specialistRadio = screen.getByRole("radio", { name: "Olena" });

    await user.click(anyRadio);
    expect(anyRadio).toBeChecked();

    await user.click(specialistRadio);

    expect(specialistRadio).toBeChecked();
    expect(anyRadio).not.toBeChecked();
  });

  it("test_selecting_any_clears_a_prior_specialist_selection", async () => {
    const user = userEvent.setup();
    render(<SpecialistSelectionGrid specialists={[specialist({ name: "Olena" })]} serviceId={42} />);

    const anyRadio = screen.getByRole("radio", { name: "Будь-який спеціаліст" });
    const specialistRadio = screen.getByRole("radio", { name: "Olena" });

    await user.click(specialistRadio);
    expect(specialistRadio).toBeChecked();

    await user.click(anyRadio);

    expect(anyRadio).toBeChecked();
    expect(specialistRadio).not.toBeChecked();
  });

  it("test_confirm_navigates_with_selected_specialist_id", async () => {
    const user = userEvent.setup();
    render(
      <SpecialistSelectionGrid specialists={[specialist({ id: 7, name: "Olena" })]} serviceId={42} />,
    );

    await user.click(screen.getByRole("radio", { name: "Olena" }));
    await user.click(screen.getByRole("button", { name: /продовж/i }));

    expect(pushMock).toHaveBeenCalledWith("/booking?entry=service&service=42&specialist=7&step=3");
  });

  it("test_confirm_navigates_with_any_specialist", async () => {
    const user = userEvent.setup();
    render(<SpecialistSelectionGrid specialists={[specialist()]} serviceId={42} />);

    await user.click(screen.getByRole("radio", { name: "Будь-який спеціаліст" }));
    await user.click(screen.getByRole("button", { name: /продовж/i }));

    expect(pushMock).toHaveBeenCalledWith("/booking?entry=service&service=42&specialist=any&step=3");
  });
});
