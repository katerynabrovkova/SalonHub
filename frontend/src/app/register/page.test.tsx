// @vitest-environment jsdom
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, test, vi } from "vitest";

import { ApiError } from "@/lib/api/errors";

// Mock the api/client.ts function boundary, not raw fetch -- same seam
// login/page.test.tsx mocks at.
vi.mock("@/lib/api/client", () => ({
  apiRequest: vi.fn(),
}));

// Imported after the mock above so the mocked module is what page.tsx sees.
import { apiRequest } from "@/lib/api/client";
import RegisterPage from "./page";

const mockedApiRequest = vi.mocked(apiRequest);

function findCall(path: string) {
  return mockedApiRequest.mock.calls.find(([, calledPath]) => calledPath.includes(path));
}

async function fillForm(
  user: ReturnType<typeof userEvent.setup>,
  { email = "person@example.com", password = "correct-horse-battery-staple", confirm = password } = {},
) {
  await user.type(screen.getByLabelText(/^email$/i), email);
  await user.type(screen.getByLabelText(/^пароль$/i), password);
  await user.type(screen.getByLabelText(/підтвердіть пароль/i), confirm);
}

beforeEach(() => {
  mockedApiRequest.mockReset();
});

describe("RegisterPage", () => {
  test("test_mismatched_confirm_password_blocks_submission_without_an_api_call", async () => {
    const user = userEvent.setup();

    render(<RegisterPage />);
    await fillForm(user, {
      email: "person@example.com",
      password: "correct-horse-battery-staple",
      confirm: "does-not-match",
    });
    await user.click(screen.getByRole("button", { name: /зареєструватися/i }));

    expect(screen.getByText(/паролі не збігаються/i)).toBeInTheDocument();
    expect(mockedApiRequest).not.toHaveBeenCalled();
  });

  test("test_successful_submit_shows_check_your_email_state", async () => {
    const user = userEvent.setup();
    mockedApiRequest.mockResolvedValueOnce(undefined);

    render(<RegisterPage />);
    await fillForm(user);
    await user.click(screen.getByRole("button", { name: /зареєструватися/i }));

    await waitFor(() => expect(findCall("/auth/register/")).toBeDefined());
    expect(
      screen.getByText(/лист із посиланням для підтвердження вже у вашій поштовій скриньці/i),
    ).toBeInTheDocument();
  });

  test("test_400_password_validation_failure_surfaces_the_server_message", async () => {
    const user = userEvent.setup();
    mockedApiRequest.mockRejectedValueOnce(
      new ApiError(400, "validation_error", "Request failed.", {
        password: ["This password is too short. It must contain at least 8 characters."],
      }),
    );

    render(<RegisterPage />);
    await fillForm(user);
    await user.click(screen.getByRole("button", { name: /зареєструватися/i }));

    await waitFor(() => {
      expect(
        screen.getByText(/this password is too short\. it must contain at least 8 characters\./i),
      ).toBeInTheDocument();
    });

    // Not the page's own generic fallback -- the server's actual message.
    expect(screen.queryByText(/щось пішло не так/i)).not.toBeInTheDocument();
    // Still on the form -- a failed registration must not show the
    // check-your-email state.
    expect(screen.queryByText(/перевірте пошту/i)).not.toBeInTheDocument();
  });

  test("test_429_throttled_shows_the_throttle_message_not_the_raw_backend_detail", async () => {
    const user = userEvent.setup();
    mockedApiRequest.mockRejectedValueOnce(
      new ApiError(429, "throttled", "Request was throttled. Expected available in 3600 seconds."),
    );

    render(<RegisterPage />);
    await fillForm(user);
    await user.click(screen.getByRole("button", { name: /зареєструватися/i }));

    await waitFor(() => {
      expect(screen.getByText(/забагато спроб/i)).toBeInTheDocument();
    });

    // Without the 429 branch this fell through to the raw, untranslated
    // DRF throttle detail (err.message) -- must not leak that instead.
    expect(screen.queryByText(/request was throttled/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/щось пішло не так/i)).not.toBeInTheDocument();
  });

  test("test_resend_shows_the_same_neutral_outcome_on_success", async () => {
    const user = userEvent.setup();
    mockedApiRequest.mockResolvedValueOnce(undefined); // register
    render(<RegisterPage />);
    await fillForm(user);
    await user.click(screen.getByRole("button", { name: /зареєструватися/i }));
    await waitFor(() => expect(findCall("/auth/register/")).toBeDefined());

    mockedApiRequest.mockResolvedValueOnce(undefined); // resend
    await user.click(screen.getByRole("button", { name: /надіслати ще раз/i }));

    await waitFor(() => expect(findCall("/auth/resend-verification/")).toBeDefined());
    expect(
      screen.getByText(/якщо у нас є ваша адреса, ми щойно надіслали новий лист/i),
    ).toBeInTheDocument();
  });

  test("test_resend_shows_the_same_neutral_outcome_on_error", async () => {
    const user = userEvent.setup();
    mockedApiRequest.mockResolvedValueOnce(undefined); // register
    render(<RegisterPage />);
    await fillForm(user);
    await user.click(screen.getByRole("button", { name: /зареєструватися/i }));
    await waitFor(() => expect(findCall("/auth/register/")).toBeDefined());

    mockedApiRequest.mockRejectedValueOnce(new ApiError(500, "unknown_error", "Request failed."));
    await user.click(screen.getByRole("button", { name: /надіслати ще раз/i }));

    await waitFor(() => expect(findCall("/auth/resend-verification/")).toBeDefined());
    // Identical copy to the success case -- no enumeration leak from an
    // error outcome either.
    expect(
      screen.getByText(/якщо у нас є ваша адреса, ми щойно надіслали новий лист/i),
    ).toBeInTheDocument();
  });

  test("test_change_email_link_resets_back_to_the_registration_form", async () => {
    const user = userEvent.setup();
    mockedApiRequest.mockResolvedValueOnce(undefined); // register

    render(<RegisterPage />);
    await fillForm(user, { email: "person@example.com" });
    await user.click(screen.getByRole("button", { name: /зареєструватися/i }));
    await waitFor(() => expect(findCall("/auth/register/")).toBeDefined());
    expect(screen.getByText(/перевірте пошту/i)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /вказали не ту адресу\? змінити email/i }));

    expect(screen.getByRole("button", { name: /^зареєструватися$/i })).toBeInTheDocument();
    // The email field keeps what the user typed -- better UX for fixing a
    // typo than forcing a full retype -- only password/confirm reset.
    expect(screen.getByLabelText(/^email$/i)).toHaveValue("person@example.com");
    expect(screen.queryByText(/перевірте пошту/i)).not.toBeInTheDocument();
  });
});
