// @vitest-environment jsdom
/**
 * `/booking/pay` after the inline script (docs/DECISIONS.md "Booking > Guest
 * token", "Frontend > Booking flow", decided 11.11.2026): the hash keeps only
 * `#appointment_id=<id>` and the token is read from sessionStorage for that
 * id; the hash token is still read when the script could not run.
 */
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const guestApiRequestMock = vi.fn();
vi.mock("@/lib/api/guestClient", () => ({
  guestApiRequest: (...args: unknown[]) => guestApiRequestMock(...args),
}));

import { guestPayTokenKey } from "@/lib/security/fragmentTokenScript";

import PaymentStatus from "./PaymentStatus";

const INVALID_LINK_MESSAGE = "Посилання недійсне або застаріле.";

function setUrl(url: string) {
  window.history.replaceState(null, "", url);
}

function blockStorage() {
  const blocked = () => {
    throw new DOMException("The operation is insecure.", "SecurityError");
  };
  vi.spyOn(Storage.prototype, "getItem").mockImplementation(blocked);
  vi.spyOn(Storage.prototype, "setItem").mockImplementation(blocked);
}

let originalUrl: string;

beforeEach(() => {
  originalUrl = window.location.href;
  guestApiRequestMock.mockReset();
  window.sessionStorage.clear();
});

afterEach(() => {
  vi.restoreAllMocks();
  window.sessionStorage.clear();
  window.history.replaceState(null, "", originalUrl);
});

describe("PaymentStatus with a stored token", () => {
  it("test_id_only_hash_uses_the_token_stored_for_this_id_and_pays_with_it", async () => {
    const user = userEvent.setup();
    setUrl("/booking/pay#appointment_id=42");
    // Another id's token first, so a lookup that ignores the id would find it.
    window.sessionStorage.setItem(guestPayTokenKey(7), "tok-other");
    window.sessionStorage.setItem(guestPayTokenKey(42), "tok-stored");
    guestApiRequestMock.mockResolvedValueOnce({ id: 42, status: "pending_payment" });
    guestApiRequestMock.mockResolvedValueOnce({
      payment: { id: 1, status: "pending", amount: "100.00", currency: "UAH" },
      provider_data: null,
    });

    render(<PaymentStatus slug="bella-demo" />);

    await user.click(await screen.findByRole("button", { name: "Оплатити" }));

    expect(guestApiRequestMock).toHaveBeenNthCalledWith(
      1,
      "bella-demo",
      "/guest/appointments/42/",
      "tok-stored",
    );
    expect(guestApiRequestMock).toHaveBeenNthCalledWith(
      2,
      "bella-demo",
      "/guest/appointments/42/pay/",
      "tok-stored",
      { method: "POST" },
    );
    // The id stays in the URL, so a reload can find the stored token again.
    expect(window.location.hash).toBe("#appointment_id=42");
  });

  it("test_id_only_hash_without_a_token_for_this_id_shows_the_invalid_link_message", async () => {
    setUrl("/booking/pay#appointment_id=42");
    window.sessionStorage.setItem(guestPayTokenKey(7), "tok-other");

    render(<PaymentStatus slug="bella-demo" />);

    expect(await screen.findByText(INVALID_LINK_MESSAGE)).toBeInTheDocument();
    expect(guestApiRequestMock).not.toHaveBeenCalled();
  });

  it("test_storage_blocked_still_reads_the_token_from_the_hash", async () => {
    blockStorage();
    setUrl("/booking/pay#appointment_id=42&token=tok-hash");
    guestApiRequestMock.mockResolvedValueOnce({ id: 42, status: "pending_payment" });

    render(<PaymentStatus slug="bella-demo" />);

    expect(await screen.findByRole("button", { name: "Оплатити" })).toBeInTheDocument();
    expect(guestApiRequestMock).toHaveBeenCalledWith(
      "bella-demo",
      "/guest/appointments/42/",
      "tok-hash",
    );
  });

  it("test_storage_blocked_with_an_id_only_hash_shows_the_invalid_link_message", async () => {
    blockStorage();
    setUrl("/booking/pay#appointment_id=42");

    render(<PaymentStatus slug="bella-demo" />);

    expect(await screen.findByText(INVALID_LINK_MESSAGE)).toBeInTheDocument();
    expect(guestApiRequestMock).not.toHaveBeenCalled();
  });
});
