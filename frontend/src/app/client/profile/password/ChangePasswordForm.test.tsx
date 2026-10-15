// @vitest-environment jsdom
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

// docs/DECISIONS.md § "Item 9 decisions (change password): ending
// sessions", "Endpoint and page, decided 06.10.2026" points 7-10 and
// "Frontend details, decided 06.10.2026" points 1, 4 and 5.

const logoutMock = vi.fn();
const mockedUseAuth = vi.fn();
vi.mock("@/app/AuthContext", () => ({
  useAuth: () => mockedUseAuth(),
}));

const replaceMock = vi.fn();
const pushMock = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: replaceMock, push: pushMock }),
}));

vi.mock("@/lib/api/client", () => ({
  apiRequest: vi.fn(),
}));

// Imported after the mocks above so the mocked modules are what the component sees.
import { apiRequest } from "@/lib/api/client";
import { ApiError, RenewalUnsureError } from "@/lib/api/errors";

import ChangePasswordForm from "./ChangePasswordForm";

const mockedApiRequest = vi.mocked(apiRequest);

const ME = {
  email: "alice@example.com",
  role: "client" as const,
  name: "Alice",
  phone: "+10000000000",
  email_verified: true,
};

const CURRENT_PASSWORD = "correct-horse-battery";
const NEW_PASSWORD = "staple-new-horse-battery";

const CURRENT_LABEL = "Поточний пароль";
const NEW_LABEL = "Новий пароль";
const CONFIRM_LABEL = "Підтвердіть новий пароль";

const WARNING =
  "Після зміни пароля ви вийдете з акаунта на всіх пристроях, і потрібно буде увійти знову.";
const NEW_PASSWORD_HINT = "Пароль має містити щонайменше 8 символів.";
const SUBMIT_BUTTON = "Змінити пароль";
const SUCCESS_PATH = "/login?notice=password_changed";

const MISMATCH_MESSAGE = "Паролі не збігаються.";
const INVALID_PASSWORD_MESSAGE = "Неправильний поточний пароль.";
const SAME_PASSWORD_MESSAGE = "Новий пароль має відрізнятися від поточного.";
const WEAK_PASSWORD_MESSAGE = "Цей пароль занадто простий. Оберіть інший.";
const THROTTLED_MESSAGE = "Забагато спроб. Спробуйте пізніше.";
const SESSION_ENDED_MESSAGE = "Сесія завершилась. Увійдіть знову.";
const CONNECTION_MESSAGE = "Не вдалося з'єднатися. Перевірте інтернет і спробуйте ще раз.";
const GENERIC_MESSAGE = "Не вдалося змінити пароль. Спробуйте ще раз.";

function mockAuth(me: unknown) {
  mockedUseAuth.mockReturnValue({
    me,
    loading: false,
    loggedOutDeliberately: false,
    login: vi.fn(),
    logout: logoutMock,
    refresh: vi.fn(),
  });
}

/**
 * Renders the form, fills all three fields, submits, and returns the user.
 * The confirmation defaults to the new password.
 */
async function fillAndSubmit(confirmation: string = NEW_PASSWORD) {
  const user = userEvent.setup();
  render(<ChangePasswordForm />);
  await user.type(screen.getByLabelText(CURRENT_LABEL), CURRENT_PASSWORD);
  await user.type(screen.getByLabelText(NEW_LABEL), NEW_PASSWORD);
  await user.type(screen.getByLabelText(CONFIRM_LABEL), confirmation);
  await user.click(screen.getByRole("button", { name: SUBMIT_BUTTON }));
  return user;
}

function expectNoSessionEndOrNavigation() {
  expect(logoutMock).not.toHaveBeenCalled();
  expect(replaceMock).not.toHaveBeenCalled();
  expect(pushMock).not.toHaveBeenCalled();
}

beforeEach(() => {
  logoutMock.mockReset();
  logoutMock.mockResolvedValue(undefined);
  replaceMock.mockReset();
  pushMock.mockReset();
  mockedUseAuth.mockReset();
  mockedApiRequest.mockReset();
  mockAuth(ME);
});

describe("ChangePasswordForm", () => {
  it("test_renders_back_link_heading_warning_fields_hint_and_button", () => {
    render(<ChangePasswordForm />);

    const link = screen.getByRole("link", { name: /Профіль/ });
    expect(link).toHaveAttribute("href", "/client/profile");
    expect(link).toHaveTextContent("← Профіль");

    expect(screen.getByRole("heading", { name: "Змінити пароль" })).toBeInTheDocument();

    const current = screen.getByLabelText(CURRENT_LABEL);
    const newPassword = screen.getByLabelText(NEW_LABEL);
    const confirm = screen.getByLabelText(CONFIRM_LABEL);
    expect(current).toHaveAttribute("type", "password");
    expect(current).toHaveAttribute("autoComplete", "current-password");
    expect(newPassword).toHaveAttribute("type", "password");
    expect(newPassword).toHaveAttribute("autoComplete", "new-password");
    expect(confirm).toHaveAttribute("type", "password");
    expect(confirm).toHaveAttribute("autoComplete", "new-password");

    // The warning sits above the form.
    const warning = screen.getByText(WARNING);
    expect(warning.compareDocumentPosition(current) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();

    const describedBy = newPassword.getAttribute("aria-describedby");
    expect(describedBy).not.toBeNull();
    expect(document.getElementById(describedBy!)).toHaveTextContent(NEW_PASSWORD_HINT);

    expect(screen.getByRole("button", { name: SUBMIT_BUTTON })).toBeInTheDocument();

    // docs/DECISIONS.md § "Item 11 decisions (forgot password)", point 13.
    const forgotLink = screen.getByRole("link", { name: "Забули поточний пароль?" });
    expect(forgotLink).toHaveAttribute("href", "/forgot-password");
  });

  it("test_mismatched_confirmation_shows_message_and_sends_no_request", async () => {
    await fillAndSubmit(`${NEW_PASSWORD}-typo`);

    expect(await screen.findByRole("alert")).toHaveTextContent(MISMATCH_MESSAGE);
    expect(mockedApiRequest).not.toHaveBeenCalled();
    expectNoSessionEndOrNavigation();
  });

  it("test_submit_posts_current_and_new_password_once", async () => {
    mockedApiRequest.mockResolvedValueOnce(undefined);

    await fillAndSubmit();

    await waitFor(() => expect(mockedApiRequest).toHaveBeenCalledTimes(1));
    const [, path, options] = mockedApiRequest.mock.calls[0] as [string, string, RequestInit];
    expect(path).toBe("/auth/me/password-change/");
    expect(options.method).toBe("POST");
    // toEqual on the parsed body: no confirmation field may be sent.
    expect(JSON.parse(options.body as string)).toEqual({
      current_password: CURRENT_PASSWORD,
      new_password: NEW_PASSWORD,
    });
    await waitFor(() => expect(replaceMock).toHaveBeenCalled());
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
    await waitFor(() => expect(replaceMock).toHaveBeenCalled());
  });

  it("test_204_logs_out_then_replaces_to_login_with_notice", async () => {
    let resolveLogout: () => void = () => {};
    logoutMock.mockReturnValueOnce(
      new Promise<void>((resolve) => {
        resolveLogout = resolve;
      }),
    );
    mockedApiRequest.mockResolvedValueOnce(undefined);

    await fillAndSubmit();

    await waitFor(() => expect(logoutMock).toHaveBeenCalledTimes(1));
    // Navigation waits for logout() to finish.
    expect(replaceMock).not.toHaveBeenCalled();

    resolveLogout();

    await waitFor(() => expect(replaceMock).toHaveBeenCalledTimes(1));
    expect(replaceMock).toHaveBeenCalledWith(SUCCESS_PATH);
    expect(logoutMock).toHaveBeenCalledTimes(1);
    expect(pushMock).not.toHaveBeenCalled();
  });

  it("test_invalid_password_clears_only_the_current_password", async () => {
    mockedApiRequest.mockRejectedValueOnce(
      new ApiError(400, "invalid_password", "The password is incorrect."),
    );

    await fillAndSubmit();

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(INVALID_PASSWORD_MESSAGE);
    expect(alert).not.toHaveTextContent("The password is incorrect.");
    expect(screen.getByLabelText(CURRENT_LABEL)).toHaveValue("");
    expect(screen.getByLabelText(NEW_LABEL)).toHaveValue(NEW_PASSWORD);
    expect(screen.getByLabelText(CONFIRM_LABEL)).toHaveValue(NEW_PASSWORD);
    expectNoSessionEndOrNavigation();
  });

  it("test_same_password_shows_message_and_keeps_all_values", async () => {
    mockedApiRequest.mockRejectedValueOnce(
      new ApiError(400, "same_password", "The new password must differ from the current one."),
    );

    await fillAndSubmit();

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(SAME_PASSWORD_MESSAGE);
    expect(alert).not.toHaveTextContent("The new password must differ from the current one.");
    expect(screen.getByLabelText(CURRENT_LABEL)).toHaveValue(CURRENT_PASSWORD);
    expect(screen.getByLabelText(NEW_LABEL)).toHaveValue(NEW_PASSWORD);
    expect(screen.getByLabelText(CONFIRM_LABEL)).toHaveValue(NEW_PASSWORD);
    expectNoSessionEndOrNavigation();
  });

  it("test_400_on_new_password_shows_weak_password_message_and_keeps_all_values", async () => {
    mockedApiRequest.mockRejectedValueOnce(
      new ApiError(400, "invalid", "Request failed.", {
        new_password: ["This password is too common."],
      }),
    );

    await fillAndSubmit();

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(WEAK_PASSWORD_MESSAGE);
    expect(alert).not.toHaveTextContent("This password is too common.");
    expect(alert).not.toHaveTextContent("Request failed.");
    expect(screen.getByLabelText(CURRENT_LABEL)).toHaveValue(CURRENT_PASSWORD);
    expect(screen.getByLabelText(NEW_LABEL)).toHaveValue(NEW_PASSWORD);
    expect(screen.getByLabelText(CONFIRM_LABEL)).toHaveValue(NEW_PASSWORD);
    expectNoSessionEndOrNavigation();
  });

  it.each([
    {
      name: "429",
      error: new ApiError(429, "throttled", "Request was throttled. Expected available in 3600 seconds."),
      serverText: "Request was throttled.",
      message: THROTTLED_MESSAGE,
    },
    {
      name: "401",
      error: new ApiError(401, "not_authenticated", "Authentication credentials were not provided."),
      serverText: "Authentication credentials were not provided.",
      message: SESSION_ENDED_MESSAGE,
    },
    {
      name: "TypeError",
      error: new TypeError("Failed to fetch"),
      serverText: "Failed to fetch",
      message: CONNECTION_MESSAGE,
    },
    {
      name: "RenewalUnsureError",
      error: new RenewalUnsureError(),
      serverText: null,
      message: CONNECTION_MESSAGE,
    },
    {
      name: "500",
      error: new ApiError(500, "internal_error", "An unexpected error occurred."),
      serverText: "An unexpected error occurred.",
      message: GENERIC_MESSAGE,
    },
  ])("test_error_shows_its_fixed_message_($name)", async ({ error, serverText, message }) => {
    mockedApiRequest.mockRejectedValueOnce(error);

    await fillAndSubmit();

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(message);
    if (serverText !== null) {
      expect(alert).not.toHaveTextContent(serverText);
    }
    expectNoSessionEndOrNavigation();
  });
});
