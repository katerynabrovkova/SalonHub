// @vitest-environment jsdom
import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const apiRequestMock = vi.fn();
vi.mock("@/lib/api/client", () => ({
  apiRequest: (...args: unknown[]) => apiRequestMock(...args),
}));

import VerifyEmailStatus from "./VerifyEmailStatus";

function setHash(hash: string) {
  window.location.hash = hash;
}

beforeEach(() => {
  apiRequestMock.mockReset();
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
