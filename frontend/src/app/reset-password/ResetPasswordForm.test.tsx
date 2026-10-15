// @vitest-environment jsdom
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { StrictMode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// docs/DECISIONS.md § "Item 11 decisions (forgot password)", points 10-12,
// and "Early link check, decided 15.10.2026", point 3.

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
const CHECKING_MESSAGE = "Перевіряємо посилання...";

const CHECK_PATH = "/auth/password-reset/check/";
const CONFIRM_PATH = "/auth/password-reset/confirm/";

let originalUrl: string;

// apiRequest is routed by path, so the early check and the confirm are set
// up independently. The check answers 204 unless a test says otherwise;
// each confirm takes the next queued response.
let checkResponse: () => Promise<unknown>;
let confirmResponses: (() => Promise<unknown>)[];

function mockCheck(response: () => Promise<unknown>) {
  checkResponse = response;
}

function mockConfirm(response: () => Promise<unknown>) {
  confirmResponses.push(response);
}

function callsTo(path: string) {
  return mockedApiRequest.mock.calls.filter(([, calledPath]) => calledPath === path);
}

/** A promise the test settles by hand; never reported as unhandled. */
function deferred() {
  let resolve: (value: unknown) => void = () => {};
  let reject: (reason: unknown) => void = () => {};
  const promise = new Promise((res, rej) => {
    resolve = res;
    reject = rej;
  });
  promise.catch(() => {});
  return { promise, resolve, reject };
}

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
  // The form appears once the early check has answered.
  await user.type(await screen.findByLabelText(NEW_LABEL), NEW_PASSWORD);
  await user.type(screen.getByLabelText(CONFIRM_LABEL), confirmation);
  await user.click(screen.getByRole("button", { name: SUBMIT_BUTTON }));
  return user;
}

/** Asserts the `callIndex`-th confirm request (not counting the check). */
function expectRequest(callIndex: number) {
  const [slug, path, options] = callsTo(CONFIRM_PATH)[callIndex] as [
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

function expectCheckRequest() {
  const calls = callsTo(CHECK_PATH);
  expect(calls).toHaveLength(1);
  const [slug, , options] = calls[0] as [string, string, RequestInit];
  expect(slug).toBe(SLUG);
  expect(options.method).toBe("POST");
  // toEqual on the parsed body: exactly the link, nothing else.
  expect(JSON.parse(options.body as string)).toEqual({ uid: UID, token: TOKEN });
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
  checkResponse = () => Promise.resolve(undefined);
  confirmResponses = [];
  mockedApiRequest.mockImplementation((...args) => {
    const [, path] = args as [string, string];
    if (path === CHECK_PATH) {
      return checkResponse();
    }
    const next = path === CONFIRM_PATH ? confirmResponses.shift() : undefined;
    return next ? next() : Promise.reject(new Error(`unexpected apiRequest call: ${path}`));
  });
});

afterEach(() => {
  vi.restoreAllMocks();
  window.history.replaceState(null, "", originalUrl);
});

describe("ResetPasswordForm", () => {
  it("test_renders_title_fields_hint_and_button_for_a_valid_link", async () => {
    setUrl(VALID_HASH);
    renderForm();

    const newPassword = await screen.findByLabelText(NEW_LABEL);
    expect(screen.getByRole("heading", { name: TITLE })).toBeInTheDocument();

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
    mockConfirm(() => Promise.resolve(undefined));
    const user = userEvent.setup();

    renderForm();

    await waitFor(() => expect(window.location.hash).toBe(""));
    expect(replaceStateSpy).toHaveBeenCalled();
    expect(window.location.href).not.toContain("#");
    expect(window.location.pathname).toBe("/reset-password");

    // Typing re-renders the form after the hash is gone: it must not turn
    // into the invalid-link state.
    await user.type(await screen.findByLabelText(NEW_LABEL), NEW_PASSWORD);
    await user.type(screen.getByLabelText(CONFIRM_LABEL), NEW_PASSWORD);
    expect(screen.queryByText(INVALID_LINK_MESSAGE)).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: SUBMIT_BUTTON }));

    await waitFor(() => expect(callsTo(CONFIRM_PATH)).toHaveLength(1));
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
    expect(callsTo(CONFIRM_PATH)).toHaveLength(0);
    expectNoSessionEndOrNavigation();
  });

  it("test_submit_posts_uid_token_and_new_password_once", async () => {
    mockConfirm(() => Promise.resolve(undefined));

    await fillAndSubmit();

    await waitFor(() => expect(callsTo(CONFIRM_PATH)).toHaveLength(1));
    expectRequest(0);
    await waitFor(() => expect(replaceMock).toHaveBeenCalled());
  });

  it("test_button_disabled_while_pending_and_second_click_ignored", async () => {
    let resolvePost: (value: unknown) => void = () => {};
    const pendingPost = new Promise((resolve) => {
      resolvePost = resolve;
    });
    mockConfirm(() => pendingPost);

    const user = await fillAndSubmit();
    const button = screen.getByRole("button", { name: SUBMIT_BUTTON });
    await waitFor(() => expect(button).toBeDisabled());
    await user.click(button);

    expect(callsTo(CONFIRM_PATH)).toHaveLength(1);

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
    mockConfirm(() => Promise.resolve(undefined));

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
    mockConfirm(() =>
      Promise.reject(new ApiError(400, "invalid_or_expired_token", "Invalid or expired token.")),
    );

    await fillAndSubmit();

    expect(await screen.findByText(INVALID_LINK_MESSAGE)).toBeInTheDocument();
    expectInvalidLinkState();
    expect(screen.queryByText("Invalid or expired token.")).not.toBeInTheDocument();
    expectNoSessionEndOrNavigation();
  });

  it("test_400_on_new_password_shows_weak_password_message_and_keeps_all_values", async () => {
    mockConfirm(() =>
      Promise.reject(
        new ApiError(400, "invalid", "Request failed.", {
          new_password: ["This password is too common."],
        }),
      ),
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
    mockConfirm(() => Promise.reject(error));

    await fillAndSubmit();

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(message);
    expect(alert).not.toHaveTextContent(serverText);
    expect(screen.getByRole("button", { name: SUBMIT_BUTTON })).toBeInTheDocument();
    expectNoSessionEndOrNavigation();
  });
});

describe("ResetPasswordForm early link check", () => {
  // docs/DECISIONS.md § "Item 11 decisions (forgot password)", "Early link
  // check, decided 15.10.2026", point 3. An incomplete hash sending no check
  // is covered by test_unusable_link_shows_invalid_link_state_and_sends_nothing_
  // above, which asserts no apiRequest call at all.

  it("test_shows_checking_message_and_no_form_while_the_check_is_pending", async () => {
    const check = deferred();
    mockCheck(() => check.promise);
    setUrl(VALID_HASH);

    renderForm();

    expect(await screen.findByText(CHECKING_MESSAGE)).toBeInTheDocument();
    expect(screen.queryByLabelText(NEW_LABEL)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: SUBMIT_BUTTON })).not.toBeInTheDocument();
    expect(screen.queryByText(INVALID_LINK_MESSAGE)).not.toBeInTheDocument();
  });

  it.each([
    { name: "plain", strict: false },
    { name: "StrictMode", strict: true },
  ])("test_check_posts_uid_and_token_once_($name)", async ({ strict }) => {
    setUrl(VALID_HASH);

    render(
      strict ? (
        <StrictMode>
          <ResetPasswordForm slug={SLUG} />
        </StrictMode>
      ) : (
        <ResetPasswordForm slug={SLUG} />
      ),
    );

    await waitFor(() => expect(callsTo(CHECK_PATH)).toHaveLength(1));
    await screen.findByLabelText(NEW_LABEL);
    expectCheckRequest();
    expect(callsTo(CONFIRM_PATH)).toHaveLength(0);
  });

  it("test_check_204_shows_the_form", async () => {
    const check = deferred();
    mockCheck(() => check.promise);
    setUrl(VALID_HASH);
    renderForm();
    await waitFor(() => expect(callsTo(CHECK_PATH)).toHaveLength(1));
    expect(screen.queryByLabelText(NEW_LABEL)).not.toBeInTheDocument();

    check.resolve(undefined);

    expect(await screen.findByLabelText(NEW_LABEL)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: SUBMIT_BUTTON })).toBeInTheDocument();
    expect(screen.queryByText(CHECKING_MESSAGE)).not.toBeInTheDocument();
    expect(screen.queryByText(INVALID_LINK_MESSAGE)).not.toBeInTheDocument();
  });

  it("test_check_invalid_or_expired_token_shows_invalid_link_state", async () => {
    mockCheck(() =>
      Promise.reject(new ApiError(400, "invalid_or_expired_token", "Invalid or expired token.")),
    );
    setUrl(VALID_HASH);

    renderForm();

    expect(await screen.findByText(INVALID_LINK_MESSAGE)).toBeInTheDocument();
    expectInvalidLinkState();
    expect(screen.queryByText(CHECKING_MESSAGE)).not.toBeInTheDocument();
    expect(screen.queryByText("Invalid or expired token.")).not.toBeInTheDocument();
    expect(callsTo(CONFIRM_PATH)).toHaveLength(0);
  });

  it.each([
    { name: "TypeError", error: new TypeError("Failed to fetch") },
    { name: "500", error: new ApiError(500, "internal_error", "An unexpected error occurred.") },
  ])("test_check_failure_still_shows_the_form_($name)", async ({ error }) => {
    const check = deferred();
    mockCheck(() => check.promise);
    setUrl(VALID_HASH);
    renderForm();
    await waitFor(() => expect(callsTo(CHECK_PATH)).toHaveLength(1));
    expect(screen.queryByLabelText(NEW_LABEL)).not.toBeInTheDocument();

    check.reject(error);

    expect(await screen.findByLabelText(NEW_LABEL)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: SUBMIT_BUTTON })).toBeInTheDocument();
    expect(screen.queryByText(INVALID_LINK_MESSAGE)).not.toBeInTheDocument();
    expect(screen.queryByText(CHECKING_MESSAGE)).not.toBeInTheDocument();
  });

  it("test_check_sends_the_original_link_after_the_hash_is_removed", async () => {
    const check = deferred();
    mockCheck(() => check.promise);
    setUrl(VALID_HASH);

    renderForm();

    await waitFor(() => expect(window.location.hash).toBe(""));
    await waitFor(() => expect(callsTo(CHECK_PATH)).toHaveLength(1));
    expectCheckRequest();
  });
});
