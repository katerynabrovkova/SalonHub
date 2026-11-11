// @vitest-environment jsdom
/**
 * Booking step 4 guest success (docs/DECISIONS.md "Frontend > Booking flow",
 * "Booking > Guest token", decided 11.11.2026): the token is stored under the
 * pay page's key for this id before navigating, and the pushed URL carries
 * only `#appointment_id=<id>`, never the token.
 */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const pushMock = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: pushMock }),
}));

const createGuestBookingMock = vi.fn();
vi.mock("@/lib/booking/createGuestBooking", () => ({
  createGuestBooking: (...args: unknown[]) => createGuestBookingMock(...args),
}));

vi.mock("@/lib/assistant/sessionId", () => ({
  readSessionId: vi.fn(() => null),
}));

import { guestPayTokenKey } from "@/lib/security/fragmentTokenScript";

import { BookingContactInfoProvider } from "./BookingContactInfoContext";
import ContactInfoForm from "./ContactInfoForm";

const SUCCESS_RESULT = {
  appointment: {
    id: 42,
    status: "pending_payment",
    startDatetime: "2026-08-17T09:00:00+03:00",
    endDatetime: "2026-08-17T10:15:00+03:00",
  },
  guestToken: "tok-abc",
};

beforeEach(() => {
  pushMock.mockReset();
  createGuestBookingMock.mockReset();
  window.sessionStorage.clear();
});

afterEach(() => {
  vi.restoreAllMocks();
  window.sessionStorage.clear();
});

describe("ContactInfoForm pay token", () => {
  it("test_success_stores_the_token_then_pushes_the_pay_page_with_only_the_id", async () => {
    const user = userEvent.setup();
    createGuestBookingMock.mockResolvedValueOnce(SUCCESS_RESULT);
    // What the pay page will find when it mounts: read at push time.
    let storedAtPush: string | null = "not pushed";
    pushMock.mockImplementation(() => {
      storedAtPush = window.sessionStorage.getItem(guestPayTokenKey(42));
    });

    render(
      <BookingContactInfoProvider>
        <ContactInfoForm
          slug="bella-demo"
          entry="service"
          service="5"
          specialist="any"
          startDatetime="2026-08-17T09:00:00+03:00"
        />
      </BookingContactInfoProvider>,
    );

    await user.type(screen.getByLabelText("Ім'я"), "Alice");
    await user.type(screen.getByLabelText("Email"), "alice@example.com");
    await user.type(screen.getByLabelText("Телефон"), "+10000000000");
    await user.click(screen.getByRole("button", { name: "Підтвердити запис" }));

    await waitFor(() => expect(pushMock).toHaveBeenCalledTimes(1));
    expect(pushMock).toHaveBeenCalledWith("/booking/pay#appointment_id=42");
    expect(JSON.stringify(pushMock.mock.calls)).not.toContain("tok-abc");
    expect(storedAtPush).toBe("tok-abc");
  });

  it("test_storing_fails_pushes_the_pay_page_with_the_token_in_the_fragment", async () => {
    const user = userEvent.setup();
    createGuestBookingMock.mockResolvedValueOnce(SUCCESS_RESULT);
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new DOMException("The operation is insecure.", "SecurityError");
    });

    render(
      <BookingContactInfoProvider>
        <ContactInfoForm
          slug="bella-demo"
          entry="service"
          service="5"
          specialist="any"
          startDatetime="2026-08-17T09:00:00+03:00"
        />
      </BookingContactInfoProvider>,
    );

    await user.type(screen.getByLabelText("Ім'я"), "Alice");
    await user.type(screen.getByLabelText("Email"), "alice@example.com");
    await user.type(screen.getByLabelText("Телефон"), "+10000000000");
    await user.click(screen.getByRole("button", { name: "Підтвердити запис" }));

    await waitFor(() => expect(pushMock).toHaveBeenCalledTimes(1));
    expect(pushMock).toHaveBeenCalledWith("/booking/pay#appointment_id=42&token=tok-abc");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});
