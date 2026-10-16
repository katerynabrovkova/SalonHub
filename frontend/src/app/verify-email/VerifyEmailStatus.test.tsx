// @vitest-environment jsdom
import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const apiRequestMock = vi.fn();
vi.mock("@/lib/api/client", () => ({
  apiRequest: (...args: unknown[]) => apiRequestMock(...args),
}));

// docs/DECISIONS.md § "Item 13 decisions (verification reminder)", point 6:
// the page reads the session from AuthContext.
const refreshMock = vi.fn();
const mockedUseAuth = vi.fn();
vi.mock("@/app/AuthContext", () => ({
  useAuth: () => mockedUseAuth(),
}));

import VerifyEmailStatus from "./VerifyEmailStatus";

const CLIENT_ME = {
  email: "alice@example.com",
  role: "client" as const,
  name: null,
  phone: null,
  email_verified: true,
};

function setHash(hash: string) {
  window.location.hash = hash;
}

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

beforeEach(() => {
  apiRequestMock.mockReset();
  refreshMock.mockReset();
  refreshMock.mockResolvedValue(undefined);
  mockedUseAuth.mockReset();
  mockAuth(null);
  window.location.hash = "";
});

describe("VerifyEmailStatus", () => {
  it("test_valid_token_verifies_and_shows_success", async () => {
    setHash("#token=tok-abc");
    apiRequestMock.mockResolvedValueOnce(undefined);

    render(<VerifyEmailStatus slug="bella-demo" />);

    expect(await screen.findByText("Пошту підтверджено.")).toBeInTheDocument();
    expect(apiRequestMock).toHaveBeenCalledTimes(1);
    expect(apiRequestMock).toHaveBeenCalledWith("bella-demo", "/auth/verify-email/", {
      method: "POST",
      body: JSON.stringify({ token: "tok-abc" }),
    });

    const loginLink = screen.getByRole("link", { name: "Увійти" });
    expect(loginLink).toHaveAttribute("href", "/login");
  });

  it("test_failed_verification_shows_error_state", async () => {
    setHash("#token=tok-abc");
    apiRequestMock.mockRejectedValueOnce(new Error("Request failed."));

    render(<VerifyEmailStatus slug="bella-demo" />);

    expect(
      await screen.findByText("Посилання недійсне або застаріле."),
    ).toBeInTheDocument();
  });

  it("test_missing_token_shows_error_without_calling_api", async () => {
    render(<VerifyEmailStatus slug="bella-demo" />);

    expect(
      await screen.findByText("Посилання недійсне або застаріле."),
    ).toBeInTheDocument();
    expect(apiRequestMock).not.toHaveBeenCalled();
  });

  it("test_malformed_hash_shows_error_without_calling_api", async () => {
    setHash("#garbage");

    render(<VerifyEmailStatus slug="bella-demo" />);

    expect(
      await screen.findByText("Посилання недійсне або застаріле."),
    ).toBeInTheDocument();
    expect(apiRequestMock).not.toHaveBeenCalled();
  });

  it("test_loading_message_renders_before_request_resolves", async () => {
    setHash("#token=tok-abc");
    let resolveVerify: (value: unknown) => void = () => {};
    const pendingVerify = new Promise((resolve) => {
      resolveVerify = resolve;
    });
    apiRequestMock.mockReturnValueOnce(pendingVerify);

    render(<VerifyEmailStatus slug="bella-demo" />);

    expect(screen.getByText("Підтверджуємо пошту...")).toBeInTheDocument();

    resolveVerify(undefined);
    expect(await screen.findByText("Пошту підтверджено.")).toBeInTheDocument();
  });

  it("test_does_not_fire_more_than_one_request_on_rerender", async () => {
    setHash("#token=tok-abc");
    apiRequestMock.mockResolvedValueOnce(undefined);

    const { rerender } = render(<VerifyEmailStatus slug="bella-demo" />);
    await screen.findByText("Пошту підтверджено.");

    rerender(<VerifyEmailStatus slug="bella-demo" />);

    expect(apiRequestMock).toHaveBeenCalledTimes(1);
  });
});

describe("VerifyEmailStatus after verification (item 13, point 6)", () => {
  it("test_success_calls_refresh_once", async () => {
    setHash("#token=tok-abc");
    apiRequestMock.mockResolvedValueOnce(undefined);

    render(<VerifyEmailStatus slug="bella-demo" />);

    await screen.findByText("Пошту підтверджено.");
    await waitFor(() => expect(refreshMock).toHaveBeenCalledTimes(1));
  });

  it("test_failure_does_not_call_refresh", async () => {
    setHash("#token=tok-abc");
    apiRequestMock.mockRejectedValueOnce(new Error("Request failed."));

    render(<VerifyEmailStatus slug="bella-demo" />);

    await screen.findByText("Посилання недійсне або застаріле.");
    expect(refreshMock).not.toHaveBeenCalled();
  });

  // A logged-in visitor is not asked to log in: the link reads "Продовжити"
  // (refined 16.10.2026).
  it("test_success_link_reads_continue_and_goes_to_client_when_a_client_is_logged_in", async () => {
    mockAuth(CLIENT_ME);
    setHash("#token=tok-abc");
    apiRequestMock.mockResolvedValueOnce(undefined);

    render(<VerifyEmailStatus slug="bella-demo" />);

    await screen.findByText("Пошту підтверджено.");
    expect(screen.getByRole("link", { name: "Продовжити" })).toHaveAttribute("href", "/client");
    expect(screen.queryByRole("link", { name: "Увійти" })).not.toBeInTheDocument();
  });

  // Mirrors the login page's redirect: an admin goes to /admin.
  it("test_success_link_reads_continue_and_goes_to_admin_when_an_admin_is_logged_in", async () => {
    mockAuth({ ...CLIENT_ME, email: "admin@example.com", role: "admin" as const });
    setHash("#token=tok-abc");
    apiRequestMock.mockResolvedValueOnce(undefined);

    render(<VerifyEmailStatus slug="bella-demo" />);

    await screen.findByText("Пошту підтверджено.");
    expect(screen.getByRole("link", { name: "Продовжити" })).toHaveAttribute("href", "/admin");
    expect(screen.queryByRole("link", { name: "Увійти" })).not.toBeInTheDocument();
  });

  it("test_success_link_reads_log_in_and_goes_to_login_when_nobody_is_logged_in", async () => {
    setHash("#token=tok-abc");
    apiRequestMock.mockResolvedValueOnce(undefined);

    render(<VerifyEmailStatus slug="bella-demo" />);

    await screen.findByText("Пошту підтверджено.");
    expect(screen.getByRole("link", { name: "Увійти" })).toHaveAttribute("href", "/login");
    expect(screen.queryByRole("link", { name: "Продовжити" })).not.toBeInTheDocument();
  });
});
