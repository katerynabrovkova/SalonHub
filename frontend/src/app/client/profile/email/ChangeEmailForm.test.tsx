// @vitest-environment jsdom
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

// docs/DECISIONS.md § "Item 8 decisions (change email)", "Frontend" and
// "Frontend details, decided 02.10.2026".

const refreshMock = vi.fn();
const mockedUseAuth = vi.fn();
vi.mock("@/app/AuthContext", () => ({
  useAuth: () => mockedUseAuth(),
}));

vi.mock("@/lib/api/client", () => ({
  apiRequest: vi.fn(),
}));

// Imported after the mocks above so the mocked modules are what the component sees.
import { apiRequest } from "@/lib/api/client";
import { ApiError, RenewalUnsureError } from "@/lib/api/errors";

import ChangeEmailForm from "./ChangeEmailForm";

const mockedApiRequest = vi.mocked(apiRequest);

const ME = {
  email: "alice@example.com",
  role: "client" as const,
  name: "Alice",
  phone: "+10000000000",
  email_verified: true,
};

const NEW_EMAIL = "alice.new@example.com";
const PASSWORD = "correct-horse-battery";

const HINT =
  "Ми надішлемо лист із підтвердженням на нову адресу. Поточна адреса лишається робочою для входу, доки ви її не підтвердите.";
const SENT_MESSAGE = `Ми надіслали лист на ${NEW_EMAIL}. Перейдіть за посиланням у листі, щоб підтвердити нову адресу.`;
const RESENT_MESSAGE = "Лист надіслано ще раз.";
const INVALID_PASSWORD_MESSAGE = "Неправильний пароль.";
const SAME_EMAIL_MESSAGE = "Це вже ваша адреса.";
const BAD_EMAIL_MESSAGE = "Перевірте адресу пошти.";
const THROTTLED_MESSAGE = "Забагато спроб. Спробуйте пізніше.";
const CONNECTION_MESSAGE = "Не вдалося з'єднатися. Перевірте інтернет і спробуйте ще раз.";
const SESSION_ENDED_MESSAGE = "Сесія завершилась. Увійдіть знову.";
const GENERIC_SEND_MESSAGE = "Не вдалося надіслати. Спробуйте ще раз.";

const SUBMIT_BUTTON = "Надіслати підтвердження";
const RESEND_BUTTON = "Надіслати ще раз";
const CHANGE_ADDRESS = "Вказали не ту адресу? Змінити";

function mockAuth(me: unknown) {
  mockedUseAuth.mockReturnValue({
    me,
    loading: false,
    loggedOutDeliberately: false,
    login: vi.fn(),
    logout: vi.fn(),
    refresh: refreshMock,
  });
}

/** Renders the form, fills both fields, submits, and returns the user. */
async function fillAndSubmit() {
  const user = userEvent.setup();
  render(<ChangeEmailForm />);
  await user.type(screen.getByLabelText("Нова адреса"), NEW_EMAIL);
  await user.type(screen.getByLabelText("Поточний пароль"), PASSWORD);
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
  expect(path).toBe("/auth/me/email-change/");
  expect(options.method).toBe("POST");
  expect(JSON.parse(options.body as string)).toEqual({
    new_email: NEW_EMAIL,
    password: PASSWORD,
  });
}

function expectSentScreen() {
  expect(screen.getByText(SENT_MESSAGE)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: RESEND_BUTTON })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: SUBMIT_BUTTON })).not.toBeInTheDocument();
}

beforeEach(() => {
  refreshMock.mockReset();
  mockedUseAuth.mockReset();
  mockedApiRequest.mockReset();
  mockAuth(ME);
});

describe("ChangeEmailForm", () => {
  it("test_renders_back_link_heading_current_address_fields_hint_and_button", () => {
    render(<ChangeEmailForm />);

    const link = screen.getByRole("link", { name: /Профіль/ });
    expect(link).toHaveAttribute("href", "/client/profile");
    expect(link).toHaveTextContent("← Профіль");

    expect(screen.getByRole("heading", { name: "Змінити email" })).toBeInTheDocument();

    expect(screen.getByText("Поточна адреса")).toBeInTheDocument();
    expect(screen.getByText(ME.email)).toBeInTheDocument();

    const newEmail = screen.getByLabelText("Нова адреса");
    expect(newEmail).toHaveAttribute("type", "email");

    const password = screen.getByLabelText("Поточний пароль");
    expect(password).toHaveAttribute("type", "password");
    expect(password).toHaveAttribute("autoComplete", "current-password");
    expect(screen.getByRole("button", { name: "Показати" })).toHaveAttribute(
      "aria-controls",
      password.id,
    );

    expect(screen.getByText(HINT)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: SUBMIT_BUTTON })).toBeInTheDocument();
  });

  it("test_submit_posts_new_email_and_password_once", async () => {
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
    expect(screen.queryByLabelText("Нова адреса")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Поточний пароль")).not.toBeInTheDocument();
  });

  it("test_invalid_password_shows_message_and_keeps_form_values", async () => {
    mockedApiRequest.mockRejectedValueOnce(
      new ApiError(400, "invalid_password", "The password is incorrect."),
    );

    await fillAndSubmit();

    expect(await screen.findByRole("alert")).toHaveTextContent(INVALID_PASSWORD_MESSAGE);
    expect(screen.getByLabelText("Нова адреса")).toHaveValue(NEW_EMAIL);
    expect(screen.getByLabelText("Поточний пароль")).toHaveValue(PASSWORD);
    expect(screen.getByRole("button", { name: SUBMIT_BUTTON })).toBeInTheDocument();
  });

  it("test_same_email_shows_message", async () => {
    mockedApiRequest.mockRejectedValueOnce(
      new ApiError(400, "same_email", "This is already your email."),
    );

    await fillAndSubmit();

    expect(await screen.findByRole("alert")).toHaveTextContent(SAME_EMAIL_MESSAGE);
  });

  it("test_400_field_error_on_new_email_shows_check_address_message_not_drf_text", async () => {
    mockedApiRequest.mockRejectedValueOnce(
      new ApiError(400, "invalid", "Request failed.", {
        new_email: ["Enter a valid email address."],
      }),
    );

    await fillAndSubmit();

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(BAD_EMAIL_MESSAGE);
    expect(alert).not.toHaveTextContent("Enter a valid email address.");
  });

  it("test_429_shows_throttled_message", async () => {
    mockedApiRequest.mockRejectedValueOnce(
      new ApiError(429, "throttled", "Request was throttled. Expected available in 3600 seconds."),
    );

    await fillAndSubmit();

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(THROTTLED_MESSAGE);
    expect(alert).not.toHaveTextContent("Request was throttled.");
  });

  it("test_type_error_shows_connection_message", async () => {
    mockedApiRequest.mockRejectedValueOnce(new TypeError("Failed to fetch"));

    await fillAndSubmit();

    expect(await screen.findByRole("alert")).toHaveTextContent(CONNECTION_MESSAGE);
  });

  it("test_renewal_unsure_error_shows_connection_message", async () => {
    mockedApiRequest.mockRejectedValueOnce(new RenewalUnsureError());

    await fillAndSubmit();

    expect(await screen.findByRole("alert")).toHaveTextContent(CONNECTION_MESSAGE);
  });

  it("test_401_shows_session_ended_message", async () => {
    mockedApiRequest.mockRejectedValueOnce(
      new ApiError(401, "not_authenticated", "Authentication credentials were not provided."),
    );

    await fillAndSubmit();

    expect(await screen.findByRole("alert")).toHaveTextContent(SESSION_ENDED_MESSAGE);
  });

  it("test_500_shows_generic_send_message_not_server_text", async () => {
    mockedApiRequest.mockRejectedValueOnce(
      new ApiError(500, "internal_error", "An unexpected error occurred."),
    );

    await fillAndSubmit();

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(GENERIC_SEND_MESSAGE);
    expect(alert).not.toHaveTextContent("An unexpected error occurred.");
  });

  it("test_resend_repeats_same_post_and_shows_resent_message", async () => {
    const user = await reachSentScreen();
    mockedApiRequest.mockResolvedValueOnce(undefined);

    await user.click(screen.getByRole("button", { name: RESEND_BUTTON }));

    expect(await screen.findByText(RESENT_MESSAGE)).toBeInTheDocument();
    expect(mockedApiRequest).toHaveBeenCalledTimes(2);
    expectRequest(0);
    expectRequest(1);
  });

  it("test_resend_429_shows_throttled_message_and_stays_on_sent_screen", async () => {
    const user = await reachSentScreen();
    mockedApiRequest.mockRejectedValueOnce(
      new ApiError(429, "throttled", "Request was throttled. Expected available in 3600 seconds."),
    );

    await user.click(screen.getByRole("button", { name: RESEND_BUTTON }));

    expect(await screen.findByRole("alert")).toHaveTextContent(THROTTLED_MESSAGE);
    expectSentScreen();
  });

  it("test_resend_invalid_password_shows_message_and_returns_to_form", async () => {
    const user = await reachSentScreen();
    mockedApiRequest.mockRejectedValueOnce(
      new ApiError(400, "invalid_password", "The password is incorrect."),
    );

    await user.click(screen.getByRole("button", { name: RESEND_BUTTON }));

    expect(await screen.findByRole("alert")).toHaveTextContent(INVALID_PASSWORD_MESSAGE);
    expect(screen.getByRole("button", { name: SUBMIT_BUTTON })).toBeInTheDocument();
    expect(screen.queryByText(SENT_MESSAGE)).not.toBeInTheDocument();
    expect(screen.getByLabelText("Нова адреса")).toHaveValue(NEW_EMAIL);
    expect(screen.getByLabelText("Поточний пароль")).toHaveValue("");
  });

  it("test_resend_500_shows_generic_send_message_and_stays_on_sent_screen", async () => {
    const user = await reachSentScreen();
    mockedApiRequest.mockRejectedValueOnce(
      new ApiError(500, "internal_error", "An unexpected error occurred."),
    );

    await user.click(screen.getByRole("button", { name: RESEND_BUTTON }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(GENERIC_SEND_MESSAGE);
    expect(alert).not.toHaveTextContent("An unexpected error occurred.");
    expectSentScreen();
  });

  it("test_change_address_returns_to_form_with_new_address_and_empty_password", async () => {
    const user = await reachSentScreen();

    await user.click(screen.getByText(CHANGE_ADDRESS));

    expect(screen.getByLabelText("Нова адреса")).toHaveValue(NEW_EMAIL);
    expect(screen.getByLabelText("Поточний пароль")).toHaveValue("");
    expect(screen.getByRole("button", { name: SUBMIT_BUTTON })).toBeInTheDocument();
    expect(screen.queryByText(SENT_MESSAGE)).not.toBeInTheDocument();
  });
});
