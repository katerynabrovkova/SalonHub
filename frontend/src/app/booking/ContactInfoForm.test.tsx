// @vitest-environment jsdom
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useEffect } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const pushMock = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: pushMock }),
}));

const createGuestBookingMock = vi.fn();
vi.mock("@/lib/booking/createGuestBooking", () => ({
  createGuestBooking: (...args: unknown[]) => createGuestBookingMock(...args),
}));

import { ApiError } from "@/lib/api/errors";

import { BookingContactInfoProvider, useBookingContactInfo } from "./BookingContactInfoContext";
import ContactInfoForm from "./ContactInfoForm";

const BASE_PROPS = {
  slug: "bella-demo",
  entry: "service" as const,
  service: "5",
  specialist: "any",
  startDatetime: "2026-08-17T09:00:00+03:00",
};

const SUCCESS_RESULT = {
  appointment: {
    id: 42,
    status: "pending_payment",
    startDatetime: "2026-08-17T09:00:00+03:00",
    endDatetime: "2026-08-17T10:15:00+03:00",
  },
  guestToken: "tok-abc",
};

/** Seeds the shared context via its own setters, the same way a real
 * step-3-round-trip would leave values behind, before ContactInfoForm reads
 * them — proves the form reads from context, not from local state of its
 * own. */
function ContextSeed({ name, email, phone }: { name: string; email: string; phone: string }) {
  const { setCustomerName, setCustomerEmail, setCustomerPhone } = useBookingContactInfo();
  useEffect(() => {
    setCustomerName(name);
    setCustomerEmail(email);
    setCustomerPhone(phone);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  return null;
}

async function fillForm(user: ReturnType<typeof userEvent.setup>) {
  await user.type(screen.getByLabelText("Ім'я"), "Alice");
  await user.type(screen.getByLabelText("Email"), "alice@example.com");
  await user.type(screen.getByLabelText("Телефон"), "+10000000000");
}

beforeEach(() => {
  pushMock.mockReset();
  createGuestBookingMock.mockReset();
});

describe("ContactInfoForm", () => {
  it("test_form_renders_reading_current_context_values", async () => {
    render(
      <BookingContactInfoProvider>
        <ContextSeed name="Alice" email="alice@example.com" phone="+10000000000" />
        <ContactInfoForm {...BASE_PROPS} />
      </BookingContactInfoProvider>,
    );

    await waitFor(() => expect(screen.getByLabelText("Ім'я")).toHaveValue("Alice"));
    expect(screen.getByLabelText("Email")).toHaveValue("alice@example.com");
    expect(screen.getByLabelText("Телефон")).toHaveValue("+10000000000");
  });

  it("test_submit_calls_create_guest_booking_with_correctly_mapped_params", async () => {
    const user = userEvent.setup();
    createGuestBookingMock.mockResolvedValueOnce(SUCCESS_RESULT);

    render(
      <BookingContactInfoProvider>
        <ContactInfoForm {...BASE_PROPS} />
      </BookingContactInfoProvider>,
    );

    await fillForm(user);
    await user.click(screen.getByRole("button", { name: "Підтвердити запис" }));

    expect(createGuestBookingMock).toHaveBeenCalledWith({
      slug: "bella-demo",
      service: "5",
      specialist: "any",
      startDatetime: "2026-08-17T09:00:00+03:00",
      customerName: "Alice",
      customerEmail: "alice@example.com",
      customerPhone: "+10000000000",
    });
  });

  it("test_success_redirects_to_booking_pay_with_appointment_id_and_token_fragment", async () => {
    const user = userEvent.setup();
    createGuestBookingMock.mockResolvedValueOnce(SUCCESS_RESULT);

    render(
      <BookingContactInfoProvider>
        <ContactInfoForm {...BASE_PROPS} />
      </BookingContactInfoProvider>,
    );

    await fillForm(user);
    await user.click(screen.getByRole("button", { name: "Підтвердити запис" }));

    await waitFor(() =>
      expect(pushMock).toHaveBeenCalledWith("/booking/pay#appointment_id=42&token=tok-abc"),
    );
  });

  it("test_409_slot_unavailable_navigates_to_step3_preserving_params_and_leaves_context_intact", async () => {
    const user = userEvent.setup();
    createGuestBookingMock.mockRejectedValueOnce(
      new ApiError(409, "SLOT_NO_LONGER_AVAILABLE", "This slot is no longer available."),
    );

    render(
      <BookingContactInfoProvider>
        <ContactInfoForm {...BASE_PROPS} />
      </BookingContactInfoProvider>,
    );

    await fillForm(user);
    await user.click(screen.getByRole("button", { name: "Підтвердити запис" }));

    await waitFor(() =>
      expect(pushMock).toHaveBeenCalledWith(
        "/booking?entry=service&service=5&specialist=any&step=3&date_from=2026-08-17",
      ),
    );

    // The persistence claim, exercised directly: nothing in the 409 path
    // clears the shared context, so the same fields (read back from
    // context, not component-local state) are still populated afterward.
    expect(screen.getByLabelText("Ім'я")).toHaveValue("Alice");
    expect(screen.getByLabelText("Email")).toHaveValue("alice@example.com");
    expect(screen.getByLabelText("Телефон")).toHaveValue("+10000000000");
  });

  it("test_non_409_error_shows_message_without_navigating", async () => {
    const user = userEvent.setup();
    createGuestBookingMock.mockRejectedValueOnce(new Error("network down"));

    render(
      <BookingContactInfoProvider>
        <ContactInfoForm {...BASE_PROPS} />
      </BookingContactInfoProvider>,
    );

    await fillForm(user);
    await user.click(screen.getByRole("button", { name: "Підтвердити запис" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Something went wrong. Please try again.",
    );
    expect(pushMock).not.toHaveBeenCalled();
    expect(screen.getByLabelText("Ім'я")).toHaveValue("Alice");

    // Same outcome for an ApiError that isn't the specific conflict code —
    // proves the branch checks `code`, not just a 409 status.
    createGuestBookingMock.mockRejectedValueOnce(
      new ApiError(409, "SOME_OTHER_CONFLICT", "Something else conflicted."),
    );
    await user.click(screen.getByRole("button", { name: "Підтвердити запис" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Something went wrong. Please try again.",
    );
    expect(pushMock).not.toHaveBeenCalled();
  });

  it("test_submit_button_disabled_while_pending", async () => {
    const user = userEvent.setup();
    let resolveCreate: (value: unknown) => void = () => {};
    const pendingCreate = new Promise((resolve) => {
      resolveCreate = resolve;
    });
    createGuestBookingMock.mockReturnValueOnce(pendingCreate);

    render(
      <BookingContactInfoProvider>
        <ContactInfoForm {...BASE_PROPS} />
      </BookingContactInfoProvider>,
    );

    await fillForm(user);
    await user.click(screen.getByRole("button", { name: "Підтвердити запис" }));

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Підтвердити запис" })).toBeDisabled();
    });

    // Clean up the dangling promise so it doesn't leak into other tests.
    resolveCreate(SUCCESS_RESULT);
  });
});
