// @vitest-environment jsdom
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// docs/DECISIONS.md § "Item 11 decisions (forgot password)", points 10-12.

const logoutMock = vi.fn();
vi.mock("@/app/AuthContext", () => ({
  useAuth: () => ({ logout: logoutMock }),
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
import { ApiError } from "@/lib/api/errors";

import ResetPasswordForm from "./ResetPasswordForm";

const mockedApiRequest = vi.mocked(apiRequest);

const SLUG = "bella-demo";
const UID = "MQ";
const TOKEN = "cz1x2y-0123456789abcdef";
const VALID_HASH = `#uid=${UID}&token=${TOKEN}`;
const NEW_PASSWORD = "staple-new-horse-battery";

const TITLE = "Новий пароль";
const NEW_LABEL = "Новий пароль";
const CONFIRM_LABEL = "Підтвердіть новий пароль";
const NEW_PASSWORD_HINT = "Пароль має містити щонайменше 8 символів.";
const SUBMIT_BUTTON = "Зберегти пароль";
const SUCCESS_PATH = "/login?notice=password_changed";

const INVALID_LINK_MESSAGE = "Посилання недійсне або застаріле.";
const NEW_EMAIL_LINK = "Надіслати новий лист";
const MISMATCH_MESSAGE = "Паролі не збігаються.";
const WEAK_PASSWORD_MESSAGE = "Цей пароль занадто простий. Оберіть інший.";
const CONNECTION_MESSAGE = "Не вдалося з'єднатися. Перевірте інтернет і спробуйте ще раз.";
const GENERIC_MESSAGE = "Не вдалося змінити пароль. Спробуйте ще раз.";

let originalUrl: string;

/** Puts `hash` in the address bar without firing hashchange. */
function setUrl(hash: string) {
  window.history.replaceState(null, "", `/reset-password${hash}`);
}

function renderForm() {
  return render(<ResetPasswordForm slug={SLUG} />);
}

/**
 * Renders with a valid link, fills both fields, submits, and returns the
 * user. The confirmation defaults to the new password.
 */
async function fillAndSubmit(confirmation: string = NEW_PASSWORD) {
  const user = userEvent.setup();
  setUrl(VALID_HASH);
  renderForm();
  await user.type(screen.getByLabelText(NEW_LABEL), NEW_PASSWORD);
  await user.type(screen.getByLabelText(CONFIRM_LABEL), confirmation);
  await user.click(screen.getByRole("button", { name: SUBMIT_BUTTON }));
  return user;
}

function expectRequest(callIndex: number) {
  const [slug, path, options] = mockedApiRequest.mock.calls[callIndex] as [
    string,
    string,
    RequestInit,
  ];
  expect(slug).toBe(SLUG);
  expect(path).toBe("/auth/password-reset/confirm/");
  expect(options.method).toBe("POST");
  // toEqual on the parsed body: no confirmation field may be sent.
  expect(JSON.parse(options.body as string)).toEqual({
    uid: UID,
    token: TOKEN,
    new_password: NEW_PASSWORD,
  });
}

function expectInvalidLinkState() {
  expect(screen.getByText(INVALID_LINK_MESSAGE)).toBeInTheDocument();
  expect(screen.getByRole("link", { name: NEW_EMAIL_LINK })).toHaveAttribute(
    "href",
    "/forgot-password",
  );
  expect(screen.queryByLabelText(NEW_LABEL)).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: SUBMIT_BUTTON })).not.toBeInTheDocument();
}

function expectNoSessionEndOrNavigation() {
  expect(logoutMock).not.toHaveBeenCalled();
  expect(replaceMock).not.toHaveBeenCalled();
  expect(pushMock).not.toHaveBeenCalled();
}

beforeEach(() => {
  originalUrl = window.location.href;
  logoutMock.mockReset();
  logoutMock.mockResolvedValue(undefined);
  replaceMock.mockReset();
  pushMock.mockReset();
  mockedApiRequest.mockReset();
});

afterEach(() => {
  vi.restoreAllMocks();
  window.history.replaceState(null, "", originalUrl);
});

describe("ResetPasswordForm", () => {
  it("test_renders_title_fields_hint_and_button_for_a_valid_link", () => {
    setUrl(VALID_HASH);
    renderForm();

    expect(screen.getByRole("heading", { name: TITLE })).toBeInTheDocument();

    const newPassword = screen.getByLabelText(NEW_LABEL);
    const confirm = screen.getByLabelText(CONFIRM_LABEL);
    expect(newPassword).toHaveAttribute("type", "password");
    expect(confirm).toHaveAttribute("type", "password");

    const describedBy = newPassword.getAttribute("aria-describedby");
    expect(describedBy).not.toBeNull();
    expect(document.getElementById(describedBy!)).toHaveTextContent(NEW_PASSWORD_HINT);

    expect(screen.getByRole("button", { name: SUBMIT_BUTTON })).toBeInTheDocument();
    expect(screen.queryByText(INVALID_LINK_MESSAGE)).not.toBeInTheDocument();
  });

  it("test_removes_the_hash_and_keeps_the_form_and_the_original_link", async () => {
    setUrl(VALID_HASH);
    const replaceStateSpy = vi.spyOn(window.history, "replaceState");
    mockedApiRequest.mockResolvedValueOnce(undefined);
    const user = userEvent.setup();

    renderForm();

    await waitFor(() => expect(window.location.hash).toBe(""));
    expect(replaceStateSpy).toHaveBeenCalled();
    expect(window.location.href).not.toContain("#");
    expect(window.location.pathname).toBe("/reset-password");

    // Typing re-renders the form after the hash is gone: it must not turn
    // into the invalid-link state.
    await user.type(screen.getByLabelText(NEW_LABEL), NEW_PASSWORD);
    await user.type(screen.getByLabelText(CONFIRM_LABEL), NEW_PASSWORD);
    expect(screen.queryByText(INVALID_LINK_MESSAGE)).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: SUBMIT_BUTTON }));

    await waitFor(() => expect(mockedApiRequest).toHaveBeenCalledTimes(1));
    expectRequest(0);
  });

  it.each([
    { name: "no hash", hash: "" },
    { name: "uid only", hash: `#uid=${UID}` },
    { name: "token only", hash: `#token=${TOKEN}` },
  ])("test_unusable_link_shows_invalid_link_state_and_sends_nothing_($name)", async ({ hash }) => {
    setUrl(hash);

    renderForm();

    expect(await screen.findByText(INVALID_LINK_MESSAGE)).toBeInTheDocument();
    expectInvalidLinkState();
    expect(mockedApiRequest).not.toHaveBeenCalled();
  });

  it("test_mismatched_confirmation_shows_message_and_sends_no_request", async () => {
    await fillAndSubmit(`${NEW_PASSWORD}-typo`);

    expect(await screen.findByRole("alert")).toHaveTextContent(MISMATCH_MESSAGE);
    expect(mockedApiRequest).not.toHaveBeenCalled();
    expectNoSessionEndOrNavigation();
  });

  it("test_submit_posts_uid_token_and_new_password_once", async () => {
    mockedApiRequest.mockResolvedValueOnce(undefined);

    await fillAndSubmit();

    await waitFor(() => expect(mockedApiRequest).toHaveBeenCalledTimes(1));
    expectRequest(0);
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

  it("test_invalid_or_expired_token_shows_invalid_link_state", async () => {
    mockedApiRequest.mockRejectedValueOnce(
      new ApiError(400, "invalid_or_expired_token", "Invalid or expired token."),
    );

    await fillAndSubmit();

    expect(await screen.findByText(INVALID_LINK_MESSAGE)).toBeInTheDocument();
    expectInvalidLinkState();
    expect(screen.queryByText("Invalid or expired token.")).not.toBeInTheDocument();
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
    expect(screen.getByLabelText(NEW_LABEL)).toHaveValue(NEW_PASSWORD);
    expect(screen.getByLabelText(CONFIRM_LABEL)).toHaveValue(NEW_PASSWORD);
    expect(screen.getByRole("button", { name: SUBMIT_BUTTON })).toBeInTheDocument();
    expectNoSessionEndOrNavigation();
  });

  it.each([
    {
      name: "TypeError",
      error: new TypeError("Failed to fetch"),
      serverText: "Failed to fetch",
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
    expect(alert).not.toHaveTextContent(serverText);
    expect(screen.getByRole("button", { name: SUBMIT_BUTTON })).toBeInTheDocument();
    expectNoSessionEndOrNavigation();
  });
});
