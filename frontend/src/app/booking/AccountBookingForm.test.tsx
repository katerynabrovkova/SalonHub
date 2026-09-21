// @vitest-environment jsdom
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useEffect } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const pushMock = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: pushMock }),
}));

const createAccountBookingMock = vi.fn();
vi.mock("@/lib/booking/createAccountBooking", () => ({
  createAccountBooking: (...args: unknown[]) => createAccountBookingMock(...args),
}));

const refreshMock = vi.fn();
const mockedUseAuth = vi.fn();
vi.mock("@/app/AuthContext", () => ({
  useAuth: () => mockedUseAuth(),
}));

import { ApiError } from "@/lib/api/errors";

import AccountBookingForm from "./AccountBookingForm";
import { BookingContactInfoProvider, useBookingContactInfo } from "./BookingContactInfoContext";

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
    start_datetime: "2026-08-17T09:00:00+03:00",
    end_datetime: "2026-08-17T10:15:00+03:00",
  },
};

const ME = {
  email: "alice@example.com",
  role: "client" as const,
  name: null,
  phone: null,
  email_verified: true,
};

/** Seeds the shared context, same pattern as ContactInfoForm.test.tsx's
 * ContextSeed -- proves the unlinked form reads name/phone from context, not
 * local state of its own. */
function ContextSeed({ name, phone }: { name: string; phone: string }) {
  const { setCustomerName, setCustomerPhone } = useBookingContactInfo();
  useEffect(() => {
    setCustomerName(name);
    setCustomerPhone(phone);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  return null;
}

function renderForm(linked: boolean, seed?: { name: string; phone: string }) {
  return render(
    <BookingContactInfoProvider>
      {seed !== undefined ? <ContextSeed name={seed.name} phone={seed.phone} /> : null}
      <AccountBookingForm {...BASE_PROPS} linked={linked} />
    </BookingContactInfoProvider>,
  );
}

beforeEach(() => {
  pushMock.mockReset();
  createAccountBookingMock.mockReset();
  refreshMock.mockReset();
  mockedUseAuth.mockReset();
  mockedUseAuth.mockReturnValue({ me: ME, loading: false, login: vi.fn(), logout: vi.fn(), refresh: refreshMock });
});

describe("AccountBookingForm", () => {
  it("test_linked_renders_account_email_line_hold_line_and_button_with_no_name_or_phone_inputs", async () => {
    renderForm(true);

    expect(
      screen.getByText("Запис на ім'я вашого облікового запису: alice@example.com"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Місце тримається 15 хвилин. Оплатити запис можна в особистому кабінеті."),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Підтвердити запис" })).toBeInTheDocument();
    expect(screen.queryByLabelText("Ім'я")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Телефон")).not.toBeInTheDocument();
  });

  it("test_unlinked_renders_readonly_email_and_prefilled_name_and_phone_inputs_and_hold_line", async () => {
    renderForm(false, { name: "Alice", phone: "+10000000000" });

    await waitFor(() => expect(screen.getByLabelText("Ім'я")).toHaveValue("Alice"));
    expect(screen.getByLabelText("Телефон")).toHaveValue("+10000000000");
    const emailInput = screen.getByLabelText("Email");
    expect(emailInput).toHaveValue("alice@example.com");
    expect(emailInput).toHaveAttribute("readonly");
    expect(
      screen.getByText("Email береться з вашого облікового запису."),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Місце тримається 15 хвилин. Оплатити запис можна в особистому кабінеті."),
    ).toBeInTheDocument();
  });

  it("test_linked_submit_calls_create_account_booking_then_refresh_then_push_in_order", async () => {
    const user = userEvent.setup();
    const callOrder: string[] = [];
    createAccountBookingMock.mockImplementationOnce(async () => {
      callOrder.push("create");
      return SUCCESS_RESULT;
    });
    refreshMock.mockImplementationOnce(async () => {
      callOrder.push("refresh");
    });
    pushMock.mockImplementationOnce((url: string) => {
      callOrder.push(`push:${url}`);
    });

    renderForm(true);
    await user.click(screen.getByRole("button", { name: "Підтвердити запис" }));

    await waitFor(() => expect(callOrder).toEqual(["create", "refresh", "push:/client"]));
    expect(createAccountBookingMock).toHaveBeenCalledWith({
      slug: "bella-demo",
      service: "5",
      specialist: "any",
      startDatetime: "2026-08-17T09:00:00+03:00",
    });
    const [callArgs] = createAccountBookingMock.mock.calls[0];
    expect("contact" in (callArgs as object)).toBe(false);
  });

  it("test_unlinked_submit_calls_create_account_booking_with_contact_from_context", async () => {
    const user = userEvent.setup();
    createAccountBookingMock.mockResolvedValueOnce(SUCCESS_RESULT);

    renderForm(false, { name: "Alice", phone: "+10000000000" });
    await waitFor(() => expect(screen.getByLabelText("Ім'я")).toHaveValue("Alice"));
    await user.click(screen.getByRole("button", { name: "Підтвердити запис" }));

    await waitFor(() =>
      expect(createAccountBookingMock).toHaveBeenCalledWith({
        slug: "bella-demo",
        service: "5",
        specialist: "any",
        startDatetime: "2026-08-17T09:00:00+03:00",
        contact: { name: "Alice", phone: "+10000000000" },
      }),
    );
  });

  it("test_unlinked_empty_name_or_phone_does_not_call_create_account_booking", async () => {
    const user = userEvent.setup();
    renderForm(false, { name: "", phone: "" });

    expect(screen.getByLabelText("Ім'я")).toBeRequired();
    expect(screen.getByLabelText("Телефон")).toBeRequired();

    await user.click(screen.getByRole("button", { name: "Підтвердити запис" }));

    expect(createAccountBookingMock).not.toHaveBeenCalled();
  });

  it("test_409_slot_unavailable_pushes_step3_url_refresh_not_called_context_unchanged", async () => {
    const user = userEvent.setup();
    createAccountBookingMock.mockRejectedValueOnce(
      new ApiError(409, "SLOT_NO_LONGER_AVAILABLE", "This slot is no longer available."),
    );

    renderForm(false, { name: "Alice", phone: "+10000000000" });
    await waitFor(() => expect(screen.getByLabelText("Ім'я")).toHaveValue("Alice"));
    await user.click(screen.getByRole("button", { name: "Підтвердити запис" }));

    await waitFor(() =>
      expect(pushMock).toHaveBeenCalledWith(
        "/booking?entry=service&service=5&specialist=any&step=3&date_from=2026-08-17",
      ),
    );
    expect(refreshMock).not.toHaveBeenCalled();
    expect(screen.getByLabelText("Ім'я")).toHaveValue("Alice");
    expect(screen.getByLabelText("Телефон")).toHaveValue("+10000000000");
  });

  it("test_400_slot_not_offered_pushes_step3_url_refresh_not_called_context_unchanged", async () => {
    const user = userEvent.setup();
    // Real wire code for a slot already taken by another booking.
    createAccountBookingMock.mockRejectedValueOnce(
      new ApiError(400, "SLOT_NOT_OFFERED", "This slot is not currently offered."),
    );

    renderForm(false, { name: "Alice", phone: "+10000000000" });
    await waitFor(() => expect(screen.getByLabelText("Ім'я")).toHaveValue("Alice"));
    await user.click(screen.getByRole("button", { name: "Підтвердити запис" }));

    await waitFor(() =>
      expect(pushMock).toHaveBeenCalledWith(
        "/booking?entry=service&service=5&specialist=any&step=3&date_from=2026-08-17",
      ),
    );
    expect(refreshMock).not.toHaveBeenCalled();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.getByLabelText("Ім'я")).toHaveValue("Alice");
    expect(screen.getByLabelText("Телефон")).toHaveValue("+10000000000");
  });

  it("test_400_other_code_shows_generic_message_without_navigating", async () => {
    const user = userEvent.setup();
    createAccountBookingMock.mockRejectedValueOnce(
      new ApiError(400, "invalid", "Invalid input."),
    );

    renderForm(true);
    await user.click(screen.getByRole("button", { name: "Підтвердити запис" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Something went wrong. Please try again.",
    );
    expect(pushMock).not.toHaveBeenCalled();
  });

  it("test_403_email_not_verified_shows_verify_message_no_navigation_refresh_not_called", async () => {
    const user = userEvent.setup();
    createAccountBookingMock.mockRejectedValueOnce(
      new ApiError(403, "email_not_verified", "Please verify your email before booking directly from your account."),
    );

    renderForm(true);
    await user.click(screen.getByRole("button", { name: "Підтвердити запис" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Спершу підтвердіть пошту, щоб бронювати з облікового запису.",
    );
    expect(pushMock).not.toHaveBeenCalled();
    expect(refreshMock).not.toHaveBeenCalled();
  });

  it("test_403_other_code_shows_generic_message_not_verify_message", async () => {
    const user = userEvent.setup();
    createAccountBookingMock.mockRejectedValueOnce(
      new ApiError(403, "permission_denied", "Forbidden."),
    );

    renderForm(true);
    await user.click(screen.getByRole("button", { name: "Підтвердити запис" }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Something went wrong. Please try again.");
    expect(alert).not.toHaveTextContent("Спершу підтвердіть пошту, щоб бронювати з облікового запису.");
    expect(pushMock).not.toHaveBeenCalled();
  });

  it("test_other_failure_shows_generic_message_no_navigation", async () => {
    const user = userEvent.setup();
    createAccountBookingMock.mockRejectedValueOnce(
      new ApiError(500, "internal_error", "Boom."),
    );

    renderForm(true);
    await user.click(screen.getByRole("button", { name: "Підтвердити запис" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Something went wrong. Please try again.",
    );
    expect(pushMock).not.toHaveBeenCalled();

    createAccountBookingMock.mockRejectedValueOnce(new Error("network down"));
    await user.click(screen.getByRole("button", { name: "Підтвердити запис" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Something went wrong. Please try again.",
    );
    expect(pushMock).not.toHaveBeenCalled();
    expect(createAccountBookingMock).toHaveBeenCalledTimes(2);
  });

  it("test_double_click_while_pending_calls_create_account_booking_once", async () => {
    const user = userEvent.setup();
    let resolveCreate: (value: unknown) => void = () => {};
    const pendingCreate = new Promise((resolve) => {
      resolveCreate = resolve;
    });
    createAccountBookingMock.mockReturnValueOnce(pendingCreate);

    renderForm(true);
    const button = screen.getByRole("button", { name: "Підтвердити запис" });
    await user.click(button);
    await waitFor(() => expect(button).toBeDisabled());
    await user.click(button);

    expect(createAccountBookingMock).toHaveBeenCalledTimes(1);

    resolveCreate(SUCCESS_RESULT);
    await waitFor(() => expect(pushMock).toHaveBeenCalledWith("/client"));
  });
});
