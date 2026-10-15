// @vitest-environment jsdom
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

// docs/DECISIONS.md § "Item 11 decisions (forgot password)", points 8 and 9.

vi.mock("@/lib/api/client", () => ({
  apiRequest: vi.fn(),
}));

// Imported after the mock above so the mocked module is what the component sees.
import { apiRequest } from "@/lib/api/client";
import { ApiError } from "@/lib/api/errors";

import ForgotPasswordForm from "./ForgotPasswordForm";

const mockedApiRequest = vi.mocked(apiRequest);

const EMAIL = "alice@example.com";

const TITLE = "Відновлення пароля";
const INTRO =
  "Вкажіть адресу пошти, з якою ви входите. Ми надішлемо посилання, щоб задати новий пароль.";
const SENT_MESSAGE =
  "Якщо ця адреса зареєстрована, ми надіслали на неї лист із посиланням. Посилання діє 1 годину.";
const RESENT_MESSAGE = "Лист надіслано ще раз.";
const BAD_EMAIL_MESSAGE = "Перевірте адресу пошти.";
const THROTTLED_MESSAGE = "Забагато спроб. Спробуйте пізніше.";
const CONNECTION_MESSAGE = "Не вдалося з'єднатися. Перевірте інтернет і спробуйте ще раз.";
const GENERIC_SEND_MESSAGE = "Не вдалося надіслати. Спробуйте ще раз.";

const EMAIL_LABEL = "Email";
const SUBMIT_BUTTON = "Надіслати посилання";
const RESEND_BUTTON = "Надіслати ще раз";
const CHANGE_ADDRESS = "Вказали не ту адресу? Змінити";

/** Renders the form, fills the address, submits, and returns the user. */
async function fillAndSubmit() {
  const user = userEvent.setup();
  render(<ForgotPasswordForm />);
  await user.type(screen.getByLabelText(EMAIL_LABEL), EMAIL);
  await user.click(screen.getByRole("button", { name: SUBMIT_BUTTON }));
  return user;
}

/** Submits successfully and waits for the sent screen. */
async function reachSentScreen() {
  mockedApiRequest.mockResolvedValueOnce(undefined);
  const user = await fillAndSubmit();
  await screen.findByText(SENT_MESSAGE);
  return user;
}

function expectRequest(callIndex: number) {
  const [, path, options] = mockedApiRequest.mock.calls[callIndex] as [
    string,
    string,
    RequestInit,
  ];
  expect(path).toBe("/auth/password-reset/");
  expect(options.method).toBe("POST");
  expect(JSON.parse(options.body as string)).toEqual({ email: EMAIL });
}

function expectSentScreen() {
  expect(screen.getByText(SENT_MESSAGE)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: RESEND_BUTTON })).toBeInTheDocument();
  expect(screen.queryByLabelText(EMAIL_LABEL)).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: SUBMIT_BUTTON })).not.toBeInTheDocument();
}

// Point 8's errors, shared by the first submit and the resend (point 9).
const SEND_ERRORS: { name: string; error: Error; message: string; serverText: string }[] = [
  {
    name: "429",
    error: new ApiError(
      429,
      "throttled",
      "Request was throttled. Expected available in 3600 seconds.",
    ),
    message: THROTTLED_MESSAGE,
    serverText: "Request was throttled.",
  },
  {
    name: "TypeError",
    error: new TypeError("Failed to fetch"),
    message: CONNECTION_MESSAGE,
    serverText: "Failed to fetch",
  },
  {
    name: "500",
    error: new ApiError(500, "internal_error", "An unexpected error occurred."),
    message: GENERIC_SEND_MESSAGE,
    serverText: "An unexpected error occurred.",
  },
];

beforeEach(() => {
  mockedApiRequest.mockReset();
});

describe("ForgotPasswordForm", () => {
  it("test_renders_back_link_title_intro_email_field_and_button", () => {
    render(<ForgotPasswordForm />);

    const link = screen.getByRole("link", { name: /Вхід/ });
    expect(link).toHaveAttribute("href", "/login");
    expect(link).toHaveTextContent("← Вхід");

    expect(screen.getByRole("heading", { name: TITLE })).toBeInTheDocument();
    expect(screen.getByText(INTRO)).toBeInTheDocument();

    const email = screen.getByLabelText(EMAIL_LABEL);
    expect(email).toHaveAttribute("type", "email");

    expect(screen.getByRole("button", { name: SUBMIT_BUTTON })).toBeInTheDocument();
  });

  it("test_submit_posts_email_once", async () => {
    mockedApiRequest.mockResolvedValueOnce(undefined);

    await fillAndSubmit();

    await waitFor(() => expect(mockedApiRequest).toHaveBeenCalledTimes(1));
    expectRequest(0);
  });

  it("test_button_disabled_while_pending_and_second_click_ignored", async () => {
    let resolvePost: (value: unknown) => void = () => {};
    const pendingPost = new Promise((resolve) => {
      resolvePost = resolve;
    });
    mockedApiRequest.mockReturnValueOnce(pendingPost);

    const user = await fillAndSubmit();
    const button = screen.getByRole("button", { name: SUBMIT_BUTTON });
    await waitFor(() => expect(button).toBeDisabled());
    await user.click(button);

    expect(mockedApiRequest).toHaveBeenCalledTimes(1);

    resolvePost(undefined);
    expect(await screen.findByText(SENT_MESSAGE)).toBeInTheDocument();
  });

  it("test_202_replaces_form_with_sent_message", async () => {
    await reachSentScreen();

    expectSentScreen();
  });

  it("test_400_field_error_on_email_shows_check_address_message_not_drf_text", async () => {
    mockedApiRequest.mockRejectedValueOnce(
      new ApiError(400, "invalid", "Request failed.", {
        email: ["Enter a valid email address."],
      }),
    );

    await fillAndSubmit();

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(BAD_EMAIL_MESSAGE);
    expect(alert).not.toHaveTextContent("Enter a valid email address.");
    expect(screen.getByLabelText(EMAIL_LABEL)).toHaveValue(EMAIL);
  });

  it.each(SEND_ERRORS)(
    "test_submit_error_shows_fixed_message_and_keeps_form_($name)",
    async ({ error, message, serverText }) => {
      mockedApiRequest.mockRejectedValueOnce(error);

      await fillAndSubmit();

      const alert = await screen.findByRole("alert");
      expect(alert).toHaveTextContent(message);
      expect(alert).not.toHaveTextContent(serverText);
      expect(screen.getByLabelText(EMAIL_LABEL)).toHaveValue(EMAIL);
      expect(screen.getByRole("button", { name: SUBMIT_BUTTON })).toBeInTheDocument();
    },
  );

  it("test_resend_repeats_same_post_and_shows_resent_message", async () => {
    const user = await reachSentScreen();
    mockedApiRequest.mockResolvedValueOnce(undefined);

    await user.click(screen.getByRole("button", { name: RESEND_BUTTON }));

    expect(await screen.findByText(RESENT_MESSAGE)).toBeInTheDocument();
    expect(mockedApiRequest).toHaveBeenCalledTimes(2);
    expectRequest(0);
    expectRequest(1);
    expectSentScreen();
  });

  it.each(SEND_ERRORS)(
    "test_resend_error_shows_fixed_message_and_stays_on_sent_screen_($name)",
    async ({ error, message, serverText }) => {
      const user = await reachSentScreen();
      mockedApiRequest.mockRejectedValueOnce(error);

      await user.click(screen.getByRole("button", { name: RESEND_BUTTON }));

      const alert = await screen.findByRole("alert");
      expect(alert).toHaveTextContent(message);
      expect(alert).not.toHaveTextContent(serverText);
      expect(screen.queryByText(RESENT_MESSAGE)).not.toBeInTheDocument();
      expectSentScreen();
    },
  );

  it("test_change_address_returns_to_form_with_address_kept", async () => {
    const user = await reachSentScreen();

    await user.click(screen.getByText(CHANGE_ADDRESS));

    expect(screen.getByLabelText(EMAIL_LABEL)).toHaveValue(EMAIL);
    expect(screen.getByRole("button", { name: SUBMIT_BUTTON })).toBeInTheDocument();
    expect(screen.queryByText(SENT_MESSAGE)).not.toBeInTheDocument();
  });
});
