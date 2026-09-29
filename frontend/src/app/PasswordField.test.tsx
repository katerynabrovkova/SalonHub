// @vitest-environment jsdom
import { act, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { type FormEvent, useState } from "react";
import { afterEach, describe, expect, test, vi } from "vitest";

import PasswordField, { type PasswordFieldProps } from "./PasswordField";

type HarnessProps = Omit<PasswordFieldProps, "value" | "onChange">;

// PasswordField is controlled, so tests render it through a stateful harness
// the same way the pages hold the value in their own useState.
function Field(props: HarnessProps) {
  const [value, setValue] = useState("");
  return <PasswordField {...props} value={value} onChange={setValue} />;
}

const DEFAULT_PROPS: HarnessProps = {
  id: "password",
  label: "Пароль",
  autoComplete: "current-password",
};

function getInput() {
  return screen.getByLabelText(/^пароль$/i);
}

afterEach(() => {
  vi.useRealTimers();
});

describe("PasswordField", () => {
  test("test_starts_hidden", () => {
    render(<Field {...DEFAULT_PROPS} />);

    expect(getInput()).toHaveAttribute("type", "password");
    expect(screen.getByRole("button", { name: "Показати" })).toBeInTheDocument();
  });

  test("test_toggle_shows_then_hides", async () => {
    const user = userEvent.setup();
    render(<Field {...DEFAULT_PROPS} />);

    await user.click(screen.getByRole("button", { name: "Показати" }));
    expect(getInput()).toHaveAttribute("type", "text");
    expect(screen.getByRole("button", { name: "Сховати" })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Сховати" }));
    expect(getInput()).toHaveAttribute("type", "password");
    expect(screen.getByRole("button", { name: "Показати" })).toBeInTheDocument();
  });

  test("test_toggle_never_submits_the_form", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn((event: FormEvent) => event.preventDefault());
    render(
      <form onSubmit={onSubmit}>
        <Field {...DEFAULT_PROPS} />
      </form>,
    );

    const toggle = screen.getByRole("button", { name: "Показати" });
    expect(toggle).toHaveAttribute("type", "button");

    await user.click(toggle);
    await user.click(screen.getByRole("button", { name: "Сховати" }));

    expect(onSubmit).not.toHaveBeenCalled();
  });

  test("test_stays_shown_after_60_seconds", () => {
    // fireEvent, not userEvent: Testing Library's async wrapper waits on a
    // setTimeout(0) and only advances fake timers when a global `jest` exists,
    // so under vi.useFakeTimers() every `await user.click` hangs. Do not switch
    // this back to userEvent.
    vi.useFakeTimers();
    render(<Field {...DEFAULT_PROPS} />);

    fireEvent.click(screen.getByRole("button", { name: "Показати" }));
    act(() => {
      vi.advanceTimersByTime(60_000);
    });

    expect(getInput()).toHaveAttribute("type", "text");
    expect(screen.getByRole("button", { name: "Сховати" })).toBeInTheDocument();
  });

  test("test_stays_shown_after_the_input_loses_focus", async () => {
    const user = userEvent.setup();
    render(
      <>
        <Field {...DEFAULT_PROPS} />
        <button type="button">outside</button>
      </>,
    );

    await user.click(screen.getByRole("button", { name: "Показати" }));
    await user.click(getInput());
    expect(getInput()).toHaveFocus();
    await user.click(screen.getByRole("button", { name: "outside" }));
    expect(getInput()).not.toHaveFocus();

    expect(getInput()).toHaveAttribute("type", "text");
    expect(screen.getByRole("button", { name: "Сховати" })).toBeInTheDocument();
  });

  test("test_toggle_aria_controls_is_the_input_id", () => {
    render(<Field {...DEFAULT_PROPS} />);

    const input = getInput();
    expect(input).toHaveAttribute("id", "password");
    expect(screen.getByRole("button", { name: "Показати" })).toHaveAttribute(
      "aria-controls",
      input.id,
    );
  });

  test("test_autocomplete_is_passed_through_and_unchanged_by_toggling", async () => {
    const user = userEvent.setup();
    render(<Field {...DEFAULT_PROPS} autoComplete="new-password" />);

    expect(getInput()).toHaveAttribute("autocomplete", "new-password");
    await user.click(screen.getByRole("button", { name: "Показати" }));
    expect(getInput()).toHaveAttribute("autocomplete", "new-password");
    await user.click(screen.getByRole("button", { name: "Сховати" }));
    expect(getInput()).toHaveAttribute("autocomplete", "new-password");
  });

  test("test_hint_is_linked_through_aria_describedby_only_when_given", () => {
    const { unmount } = render(<Field {...DEFAULT_PROPS} hint="Щонайменше 8 символів" />);

    const describedBy = getInput().getAttribute("aria-describedby");
    expect(describedBy).not.toBeNull();
    expect(document.getElementById(describedBy as string)).toHaveTextContent(
      "Щонайменше 8 символів",
    );
    unmount();

    render(<Field {...DEFAULT_PROPS} />);
    expect(getInput()).not.toHaveAttribute("aria-describedby");
  });

  test("test_hint_sits_between_the_label_and_the_input", () => {
    render(<Field {...DEFAULT_PROPS} hint="Щонайменше 8 символів" />);

    const label = screen.getByText("Пароль", { selector: "label" });
    const hint = screen.getByText("Щонайменше 8 символів");
    const input = getInput();
    const FOLLOWING = Node.DOCUMENT_POSITION_FOLLOWING;

    expect(label.compareDocumentPosition(hint) & FOLLOWING).toBe(FOLLOWING);
    expect(hint.compareDocumentPosition(input) & FOLLOWING).toBe(FOLLOWING);
  });

  test("test_required_is_passed_to_the_input_only_when_given", () => {
    const { unmount } = render(<Field {...DEFAULT_PROPS} required />);
    expect(getInput()).toBeRequired();
    unmount();

    render(<Field {...DEFAULT_PROPS} />);
    expect(getInput()).not.toBeRequired();
  });

  test("test_two_fields_toggle_independently", async () => {
    const user = userEvent.setup();
    render(
      <form>
        <Field id="password" label="Пароль" autoComplete="new-password" />
        <Field id="confirm-password" label="Підтвердіть пароль" autoComplete="new-password" />
      </form>,
    );

    const password = getInput();
    const confirm = screen.getByLabelText(/^підтвердіть пароль$/i);
    const toggleFor = (input: HTMLElement) =>
      screen
        .getAllByRole("button")
        .find((button) => button.getAttribute("aria-controls") === input.id) as HTMLElement;

    await user.click(toggleFor(password));
    expect(password).toHaveAttribute("type", "text");
    expect(confirm).toHaveAttribute("type", "password");
    expect(toggleFor(password)).toHaveTextContent("Сховати");
    expect(toggleFor(confirm)).toHaveTextContent("Показати");

    await user.click(toggleFor(confirm));
    await user.click(toggleFor(password));
    expect(password).toHaveAttribute("type", "password");
    expect(confirm).toHaveAttribute("type", "text");
  });
});
