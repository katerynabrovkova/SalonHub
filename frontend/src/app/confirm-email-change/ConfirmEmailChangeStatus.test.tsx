// @vitest-environment jsdom
import { render, screen, waitFor } from "@testing-library/react";
import { StrictMode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

// docs/DECISIONS.md § "Item 8 decisions (change email)", "Frontend" and
// "Frontend details, decided 02.10.2026".

const apiRequestMock = vi.fn();
vi.mock("@/lib/api/client", () => ({
  apiRequest: (...args: unknown[]) => apiRequestMock(...args),
}));

const refreshMock = vi.fn();
vi.mock("@/app/AuthContext", () => ({
  useAuth: () => ({ refresh: refreshMock }),
}));

// Imported after the mocks above so the mocked modules are what the component sees.
import { ApiError } from "@/lib/api/errors";

import ConfirmEmailChangeStatus from "./ConfirmEmailChangeStatus";

const LOADING_MESSAGE = "Підтверджуємо нову адресу...";
const SUCCESS_MESSAGE = "Адресу пошти змінено.";
const USE_NEW_ADDRESS_MESSAGE = "Для входу використовуйте нову адресу.";
const UNAVAILABLE_MESSAGE = "Цю адресу не можна використати.";
const INVALID_LINK_MESSAGE = "Посилання недійсне або застаріле.";
const CONNECTION_MESSAGE = "Не вдалося з'єднатися. Перевірте інтернет і спробуйте ще раз.";
const UNEXPECTED_MESSAGE =
  "Не вдалося підтвердити адресу. Оновіть сторінку, щоб спробувати ще раз.";

function setHash(hash: string) {
  window.location.hash = hash;
}

beforeEach(() => {
  apiRequestMock.mockReset();
  refreshMock.mockReset();
  window.location.hash = "";
});

describe("ConfirmEmailChangeStatus", () => {
  it("test_shows_loading_while_pending_and_posts_token_once", async () => {
    setHash("#token=abc");
    apiRequestMock.mockReturnValueOnce(new Promise(() => {}));

    render(<ConfirmEmailChangeStatus slug="bella-demo" />);

    expect(screen.getByText(LOADING_MESSAGE)).toBeInTheDocument();
    await waitFor(() => expect(apiRequestMock).toHaveBeenCalledTimes(1));
    expect(apiRequestMock).toHaveBeenCalledWith("bella-demo", "/auth/email-change/confirm/", {
      method: "POST",
      body: JSON.stringify({ token: "abc" }),
    });
  });

  it("test_204_shows_success_with_profile_link_and_refreshes_once", async () => {
    setHash("#token=abc");
    apiRequestMock.mockResolvedValueOnce(undefined);

    render(<ConfirmEmailChangeStatus slug="bella-demo" />);

    expect(await screen.findByText(SUCCESS_MESSAGE)).toBeInTheDocument();
    expect(screen.getByText(USE_NEW_ADDRESS_MESSAGE)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Перейти до профілю" })).toHaveAttribute(
      "href",
      "/client/profile",
    );
    await waitFor(() => expect(refreshMock).toHaveBeenCalledTimes(1));
  });

  it("test_email_unavailable_shows_unavailable_message_without_refresh", async () => {
    setHash("#token=abc");
    apiRequestMock.mockRejectedValueOnce(
      new ApiError(400, "email_unavailable", "This email address cannot be used."),
    );

    render(<ConfirmEmailChangeStatus slug="bella-demo" />);

    expect(await screen.findByText(UNAVAILABLE_MESSAGE)).toBeInTheDocument();
    expect(refreshMock).not.toHaveBeenCalled();
  });

  it("test_invalid_or_expired_token_shows_invalid_link_message", async () => {
    setHash("#token=abc");
    apiRequestMock.mockRejectedValueOnce(
      new ApiError(400, "invalid_or_expired_token", "Invalid or expired token."),
    );

    render(<ConfirmEmailChangeStatus slug="bella-demo" />);

    expect(await screen.findByText(INVALID_LINK_MESSAGE)).toBeInTheDocument();
  });

  it("test_missing_token_shows_invalid_link_message_without_calling_api", async () => {
    render(<ConfirmEmailChangeStatus slug="bella-demo" />);

    expect(await screen.findByText(INVALID_LINK_MESSAGE)).toBeInTheDocument();
    expect(apiRequestMock).not.toHaveBeenCalled();
  });

  it("test_type_error_shows_connection_message", async () => {
    setHash("#token=abc");
    apiRequestMock.mockRejectedValueOnce(new TypeError("Failed to fetch"));

    render(<ConfirmEmailChangeStatus slug="bella-demo" />);

    expect(await screen.findByText(CONNECTION_MESSAGE)).toBeInTheDocument();
  });

  it("test_500_shows_unexpected_message_not_invalid_link_or_server_text", async () => {
    setHash("#token=abc");
    apiRequestMock.mockRejectedValueOnce(
      new ApiError(500, "internal_error", "An unexpected error occurred."),
    );

    render(<ConfirmEmailChangeStatus slug="bella-demo" />);

    expect(await screen.findByText(UNEXPECTED_MESSAGE)).toBeInTheDocument();
    expect(screen.queryByText(INVALID_LINK_MESSAGE)).not.toBeInTheDocument();
    expect(screen.queryByText("An unexpected error occurred.")).not.toBeInTheDocument();
  });

  it("test_strict_mode_double_mount_sends_one_request", async () => {
    setHash("#token=abc");
    apiRequestMock.mockResolvedValueOnce(undefined);

    render(
      <StrictMode>
        <ConfirmEmailChangeStatus slug="bella-demo" />
      </StrictMode>,
    );

    expect(await screen.findByText(SUCCESS_MESSAGE)).toBeInTheDocument();
    expect(apiRequestMock).toHaveBeenCalledTimes(1);
  });
});
