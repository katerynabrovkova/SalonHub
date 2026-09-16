// @vitest-environment jsdom
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

const guestApiRequestMock = vi.fn();
vi.mock("@/lib/api/guestClient", () => ({
  guestApiRequest: (...args: unknown[]) => guestApiRequestMock(...args),
}));

import PaymentStatus from "./PaymentStatus";

function setHash(hash: string) {
  window.location.hash = hash;
}

beforeEach(() => {
  guestApiRequestMock.mockReset();
  window.location.hash = "";
});

describe("PaymentStatus", () => {
  it("test_missing_fragment_shows_invalid_link_message", async () => {
    render(<PaymentStatus slug="bella-demo" />);

    expect(await screen.findByText("Посилання недійсне або застаріле.")).toBeInTheDocument();
    expect(guestApiRequestMock).not.toHaveBeenCalled();
  });

  it("test_malformed_fragment_shows_invalid_link_message", async () => {
    setHash("#garbage");
    render(<PaymentStatus slug="bella-demo" />);

    expect(await screen.findByText("Посилання недійсне або застаріле.")).toBeInTheDocument();
    expect(guestApiRequestMock).not.toHaveBeenCalled();
  });

  it("test_loading_message_renders_before_detail_fetch_resolves", async () => {
    setHash("#appointment_id=42&token=tok-abc");
    let resolveDetail: (value: unknown) => void = () => {};
    const pendingDetail = new Promise((resolve) => {
      resolveDetail = resolve;
    });
    guestApiRequestMock.mockReturnValueOnce(pendingDetail);

    render(<PaymentStatus slug="bella-demo" />);

    expect(screen.getByText("Завантаження...")).toBeInTheDocument();

    resolveDetail({ id: 42, status: "pending_payment" });
    expect(await screen.findByRole("button", { name: "Оплатити" })).toBeInTheDocument();
  });

  it("test_valid_fragment_pending_payment_renders_pay_button", async () => {
    setHash("#appointment_id=42&token=tok-abc");
    guestApiRequestMock.mockResolvedValueOnce({ id: 42, status: "pending_payment" });

    render(<PaymentStatus slug="bella-demo" />);

    expect(await screen.findByRole("button", { name: "Оплатити" })).toBeInTheDocument();
    expect(guestApiRequestMock).toHaveBeenCalledWith(
      "bella-demo",
      "/guest/appointments/42/",
      "tok-abc",
    );
  });

  it("test_confirmed_renders_already_paid_no_button", async () => {
    setHash("#appointment_id=42&token=tok-abc");
    guestApiRequestMock.mockResolvedValueOnce({ id: 42, status: "confirmed" });

    render(<PaymentStatus slug="bella-demo" />);

    expect(await screen.findByText("Вже оплачено.")).toBeInTheDocument();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("test_expired_renders_unavailable_message", async () => {
    setHash("#appointment_id=42&token=tok-abc");
    guestApiRequestMock.mockResolvedValueOnce({ id: 42, status: "expired" });

    render(<PaymentStatus slug="bella-demo" />);

    expect(await screen.findByText("Це бронювання більше недоступне.")).toBeInTheDocument();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("test_cancelled_renders_unavailable_message", async () => {
    setHash("#appointment_id=42&token=tok-abc");
    guestApiRequestMock.mockResolvedValueOnce({ id: 42, status: "cancelled" });

    render(<PaymentStatus slug="bella-demo" />);

    expect(await screen.findByText("Це бронювання більше недоступне.")).toBeInTheDocument();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("test_successful_pay_renders_pending_confirmation", async () => {
    const user = userEvent.setup();
    setHash("#appointment_id=42&token=tok-abc");
    guestApiRequestMock.mockResolvedValueOnce({ id: 42, status: "pending_payment" });
    guestApiRequestMock.mockResolvedValueOnce({
      payment: { id: 1, status: "pending", amount: "100.00", currency: "UAH" },
      provider_data: null,
    });

    render(<PaymentStatus slug="bella-demo" />);

    await user.click(await screen.findByRole("button", { name: "Оплатити" }));

    expect(await screen.findByText("Очікуємо підтвердження оплати.")).toBeInTheDocument();
    expect(guestApiRequestMock).toHaveBeenLastCalledWith(
      "bella-demo",
      "/guest/appointments/42/pay/",
      "tok-abc",
      { method: "POST" },
    );
  });

  it("test_failed_detail_fetch_renders_invalid_link_message", async () => {
    setHash("#appointment_id=42&token=tok-abc");
    guestApiRequestMock.mockRejectedValueOnce(new Error("Request failed."));

    render(<PaymentStatus slug="bella-demo" />);

    expect(await screen.findByText("Посилання недійсне або застаріле.")).toBeInTheDocument();
  });

  it("test_pay_button_disabled_while_pending", async () => {
    const user = userEvent.setup();
    setHash("#appointment_id=42&token=tok-abc");
    guestApiRequestMock.mockResolvedValueOnce({ id: 42, status: "pending_payment" });
    let resolvePay: (value: unknown) => void = () => {};
    const pendingPay = new Promise((resolve) => {
      resolvePay = resolve;
    });
    guestApiRequestMock.mockReturnValueOnce(pendingPay);

    render(<PaymentStatus slug="bella-demo" />);

    await user.click(await screen.findByRole("button", { name: "Оплатити" }));

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Оплатити" })).toBeDisabled();
    });

    resolvePay({ payment: { id: 1, status: "pending" }, provider_data: null });
  });
});
