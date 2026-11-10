// @vitest-environment jsdom
/**
 * Guest manage page, client half (docs/DECISIONS.md "Frontend > Guest manage
 * page", "Booking > Guest token"; register "In progress: guest manage page",
 * sub-step (c)). Mirrors booking/pay/PaymentStatus.test.tsx: the guest API
 * client is mocked at its module boundary, the URL is set through history.
 *
 * Pinned design: the component is `ManageAppointment({ slug, appointmentId })`
 * in this folder; the token comes from `#token=...`, is then removed from the
 * address bar (path kept) and kept in sessionStorage under
 * `guest-manage-token:<id>`. Texts are literals copied from the client
 * dashboard (app/client/page.tsx), so a drift on either page fails here.
 *
 * Fake timers fake only `Date` (for the refund-deadline comparison at click
 * time) and clicks use fireEvent, per CLAUDE.md's userEvent/fake-timer trap.
 */
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/lib/api/errors";
import { formatSalonDateTime } from "@/lib/datetime/formatSalonDateTime";
import { formatPrice } from "@/lib/pricing/formatPrice";

const guestApiRequestMock = vi.fn();
vi.mock("@/lib/api/guestClient", () => ({
  guestApiRequest: (...args: unknown[]) => guestApiRequestMock(...args),
}));

import ManageAppointment from "./ManageAppointment";

const PATH = "/appointments/42/manage";
const STORAGE_KEY = "guest-manage-token:42";
const INVALID_LINK_MESSAGE = "Посилання недійсне або застаріле.";
const CANCEL_BUTTON = "Скасувати запис";

// The dashboard's cancel-confirmation texts and deposit-outcome labels.
const CONFIRM_PLAIN = "Скасувати бронювання?";
const CONFIRM_REFUND = "Скасувати бронювання? Передоплату буде повернено.";
const CONFIRM_NO_REFUND =
  "Скасувати бронювання? До візиту менше 24 годин, тому передоплата не повертається.";
const OUTCOME_LABELS = {
  refund_processing: "Повернення обробляється",
  refunded: "Повернено",
  withheld: "Передоплату утримано",
} as const;

// Saturday 14 Nov 2026, 14:00 in the salon (+02:00), 90 minutes.
const START = "2026-11-14T14:00:00+02:00";
const END = "2026-11-14T15:30:00+02:00";
const DEADLINE = "2026-11-13T14:00:00+02:00";
const DEADLINE_MS = new Date(DEADLINE).getTime();

function detail(overrides: Record<string, unknown> = {}) {
  return {
    id: 42,
    status: "confirmed",
    start_datetime: START,
    end_datetime: END,
    specialist: { id: 7, name: "Яна" },
    service: { id: 3, name: "Манікюр" },
    cancelled_at: null,
    cancelled_by: null,
    cancellation_reason: "",
    service_price_at_booking: "120.00",
    deposit_amount: "24.00",
    amount_due_at_visit: "96.00",
    currency: "UAH",
    deposit_outcome: null,
    refund_deadline: DEADLINE,
    ...overrides,
  };
}

/** Testing Library collapses whitespace runs (incl. Intl's U+00A0) to one space. */
function norm(text: string): string {
  return text.replace(/\s+/g, " ").trim();
}

function setUrl(url: string) {
  window.history.replaceState(null, "", url);
}

async function renderPage(appointmentId = 42) {
  return render(<ManageAppointment slug="bella-demo" appointmentId={appointmentId} />);
}

async function renderLoaded(data = detail()) {
  setUrl(`${PATH}#token=tok-abc`);
  guestApiRequestMock.mockResolvedValueOnce(data);
  await renderPage();
  await screen.findByRole("heading", { name: "Ваш запис" });
}

beforeEach(() => {
  guestApiRequestMock.mockReset();
  window.sessionStorage.clear();
  setUrl(PATH);
  vi.spyOn(window, "confirm").mockReturnValue(true);
});

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe("ManageAppointment", () => {
  // --- token -----------------------------------------------------------------

  it("test_reads_the_token_from_the_hash_calls_the_detail_endpoint_and_removes_the_hash", async () => {
    setUrl(`${PATH}#token=tok-abc`);
    guestApiRequestMock.mockResolvedValueOnce(detail());

    await renderPage();

    await screen.findByRole("heading", { name: "Ваш запис" });
    expect(guestApiRequestMock).toHaveBeenCalledWith(
      "bella-demo",
      "/guest/appointments/42/",
      "tok-abc",
    );
    expect(window.location.hash).toBe("");
    expect(window.location.pathname).toBe(PATH);
    expect(window.sessionStorage.getItem(STORAGE_KEY)).toBe("tok-abc");
  });

  it("test_without_a_hash_uses_the_token_stored_for_this_id_only", async () => {
    // Another id's token first, so a lookup that ignores the id would find it.
    window.sessionStorage.setItem("guest-manage-token:7", "tok-other");
    window.sessionStorage.setItem(STORAGE_KEY, "tok-stored");
    guestApiRequestMock.mockResolvedValueOnce(detail());

    await renderPage();

    await screen.findByRole("heading", { name: "Ваш запис" });
    expect(guestApiRequestMock).toHaveBeenCalledTimes(1);
    expect(guestApiRequestMock).toHaveBeenCalledWith(
      "bella-demo",
      "/guest/appointments/42/",
      "tok-stored",
    );
  });

  it("test_no_hash_and_no_stored_token_shows_the_invalid_link_message_without_a_request", async () => {
    window.sessionStorage.setItem("guest-manage-token:7", "tok-other");

    await renderPage();

    expect(await screen.findByText(INVALID_LINK_MESSAGE)).toBeInTheDocument();
    expect(guestApiRequestMock).not.toHaveBeenCalled();
  });

  // --- details ---------------------------------------------------------------

  it("test_confirmed_booking_shows_service_specialist_salon_local_time_and_duration", async () => {
    await renderLoaded();

    expect(screen.getByText(/Манікюр/)).toBeInTheDocument();
    expect(screen.getByText(/Яна/)).toBeInTheDocument();
    expect(screen.getByText(norm(formatSalonDateTime(START)), { exact: false })).toBeInTheDocument();
    expect(screen.getByText(/1 год 30 хв/)).toBeInTheDocument();
  });

  it("test_shows_price_deposit_and_amount_due_from_the_response", async () => {
    await renderLoaded();

    expect(
      screen.getByText(norm(formatPrice("120.00", "UAH")), { exact: false }),
    ).toBeInTheDocument();
    expect(
      screen.getByText(norm(`Передоплата: ${formatPrice("24.00", "UAH")}`)),
    ).toBeInTheDocument();
    expect(
      screen.getByText(norm(`Оплата при візиті: ${formatPrice("96.00", "UAH")}`)),
    ).toBeInTheDocument();
  });

  // --- cancel confirmation -----------------------------------------------------

  it("test_confirmation_before_the_refund_deadline_is_the_dashboard_refund_text", async () => {
    await renderLoaded();
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(DEADLINE_MS - 60 * 60 * 1000);

    fireEvent.click(screen.getByRole("button", { name: CANCEL_BUTTON }));

    expect(window.confirm).toHaveBeenCalledWith(CONFIRM_REFUND);
  });

  it("test_confirmation_after_the_refund_deadline_is_the_dashboard_no_refund_text", async () => {
    await renderLoaded();
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(DEADLINE_MS + 60 * 1000);

    fireEvent.click(screen.getByRole("button", { name: CANCEL_BUTTON }));

    expect(window.confirm).toHaveBeenCalledWith(CONFIRM_NO_REFUND);
  });

  it("test_confirmation_without_a_refund_deadline_is_the_dashboard_plain_text", async () => {
    await renderLoaded(
      detail({ refund_deadline: null, deposit_amount: "24.00", amount_due_at_visit: null }),
    );

    fireEvent.click(screen.getByRole("button", { name: CANCEL_BUTTON }));

    expect(window.confirm).toHaveBeenCalledWith(CONFIRM_PLAIN);
  });

  // --- cancel ----------------------------------------------------------------

  it("test_confirming_sends_the_cancel_request_with_the_token_and_shows_the_outcome", async () => {
    await renderLoaded();
    guestApiRequestMock.mockResolvedValueOnce(
      detail({ status: "cancelled", deposit_outcome: "refund_processing", refund_deadline: null }),
    );

    fireEvent.click(screen.getByRole("button", { name: CANCEL_BUTTON }));

    expect(await screen.findByText("Запис скасовано.")).toBeInTheDocument();
    expect(guestApiRequestMock).toHaveBeenLastCalledWith(
      "bella-demo",
      "/guest/appointments/42/cancel/",
      "tok-abc",
      { method: "POST" },
    );
    expect(
      screen.getByText(
        norm(`${OUTCOME_LABELS.refund_processing}: ${formatPrice("24.00", "UAH")}`),
      ),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: CANCEL_BUTTON })).not.toBeInTheDocument();
  });

  it("test_dismissing_the_confirmation_sends_nothing", async () => {
    await renderLoaded();
    vi.mocked(window.confirm).mockReturnValue(false);

    fireEvent.click(screen.getByRole("button", { name: CANCEL_BUTTON }));

    expect(window.confirm).toHaveBeenCalledTimes(1);
    expect(guestApiRequestMock).toHaveBeenCalledTimes(1); // the detail load only
    expect(screen.getByRole("button", { name: CANCEL_BUTTON })).toBeEnabled();
  });

  // --- states ----------------------------------------------------------------

  it("test_already_cancelled_booking_shows_cancelled_with_its_outcome_and_no_button", async () => {
    await renderLoaded(
      detail({ status: "cancelled", deposit_outcome: "withheld", refund_deadline: null }),
    );

    expect(screen.getByText("Запис скасовано.")).toBeInTheDocument();
    expect(
      screen.getByText(norm(`${OUTCOME_LABELS.withheld}: ${formatPrice("24.00", "UAH")}`)),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: CANCEL_BUTTON })).not.toBeInTheDocument();
  });

  it("test_completed_booking_thanks_for_the_visit_and_has_no_button", async () => {
    await renderLoaded(detail({ status: "completed", refund_deadline: null }));

    expect(screen.getByText("Дякуємо за візит")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: CANCEL_BUTTON })).not.toBeInTheDocument();
  });

  it.each([{ status: "no_show" }, { status: "expired" }])(
    "test_past_booking_shows_the_past_message_and_no_button_($status)",
    async ({ status }) => {
      await renderLoaded(detail({ status, refund_deadline: null, amount_due_at_visit: null }));

      expect(screen.getByText("Цей запис уже минув.")).toBeInTheDocument();
      expect(screen.queryByRole("button", { name: CANCEL_BUTTON })).not.toBeInTheDocument();
    },
  );

  // --- errors ----------------------------------------------------------------

  it("test_invalid_or_expired_token_on_load_shows_the_invalid_link_message_and_a_home_link", async () => {
    setUrl(`${PATH}#token=tok-abc`);
    guestApiRequestMock.mockRejectedValueOnce(
      new ApiError(400, "invalid_or_expired_token", "This link is invalid or has expired."),
    );

    await renderPage();

    expect(await screen.findByText(INVALID_LINK_MESSAGE)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "На головну" })).toHaveAttribute("href", "/");
  });

  it("test_409_on_cancel_reloads_the_detail_and_shows_the_current_state", async () => {
    await renderLoaded();
    guestApiRequestMock.mockRejectedValueOnce(
      new ApiError(409, "invalid_state_transition", "Not valid.", { current_status: "cancelled" }),
    );
    guestApiRequestMock.mockResolvedValueOnce(
      detail({ status: "cancelled", deposit_outcome: "refunded", refund_deadline: null }),
    );

    fireEvent.click(screen.getByRole("button", { name: CANCEL_BUTTON }));

    expect(await screen.findByText("Запис скасовано.")).toBeInTheDocument();
    expect(guestApiRequestMock).toHaveBeenCalledTimes(3);
    expect(guestApiRequestMock).toHaveBeenLastCalledWith(
      "bella-demo",
      "/guest/appointments/42/",
      "tok-abc",
    );
    expect(
      screen.getByText(norm(`${OUTCOME_LABELS.refunded}: ${formatPrice("24.00", "UAH")}`)),
    ).toBeInTheDocument();
  });

  // A 400 on cancel is the token's cancel capability already spent or the
  // link expired: the page reloads the detail and shows what it returns.
  it("test_400_on_cancel_reloads_the_detail_and_shows_the_already_cancelled_state", async () => {
    await renderLoaded();
    guestApiRequestMock.mockRejectedValueOnce(
      new ApiError(400, "invalid_or_expired_token", "This link is invalid or has expired."),
    );
    guestApiRequestMock.mockResolvedValueOnce(
      detail({ status: "cancelled", deposit_outcome: "refund_processing", refund_deadline: null }),
    );

    fireEvent.click(screen.getByRole("button", { name: CANCEL_BUTTON }));

    expect(await screen.findByText("Запис скасовано.")).toBeInTheDocument();
    expect(guestApiRequestMock).toHaveBeenCalledTimes(3);
    expect(guestApiRequestMock).toHaveBeenLastCalledWith(
      "bella-demo",
      "/guest/appointments/42/",
      "tok-abc",
    );
    expect(
      screen.getByText(
        norm(`${OUTCOME_LABELS.refund_processing}: ${formatPrice("24.00", "UAH")}`),
      ),
    ).toBeInTheDocument();
    expect(
      screen.queryByText("Не вдалося скасувати запис. Перевірте інтернет і спробуйте ще раз."),
    ).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: CANCEL_BUTTON })).not.toBeInTheDocument();
  });

  it("test_400_on_cancel_with_an_expired_link_reloads_and_shows_the_invalid_link_message", async () => {
    await renderLoaded();
    const expired = new ApiError(
      400,
      "invalid_or_expired_token",
      "This link is invalid or has expired.",
    );
    guestApiRequestMock.mockRejectedValueOnce(expired);
    guestApiRequestMock.mockRejectedValueOnce(expired);

    fireEvent.click(screen.getByRole("button", { name: CANCEL_BUTTON }));

    expect(await screen.findByText(INVALID_LINK_MESSAGE)).toBeInTheDocument();
    expect(guestApiRequestMock).toHaveBeenCalledTimes(3);
    expect(guestApiRequestMock).toHaveBeenLastCalledWith(
      "bella-demo",
      "/guest/appointments/42/",
      "tok-abc",
    );
    expect(screen.getByRole("link", { name: "На головну" })).toHaveAttribute("href", "/");
    expect(
      screen.queryByText("Не вдалося скасувати запис. Перевірте інтернет і спробуйте ще раз."),
    ).not.toBeInTheDocument();
  });

  it("test_network_error_on_cancel_shows_the_error_and_the_button_works_again", async () => {
    await renderLoaded();
    guestApiRequestMock.mockRejectedValueOnce(new TypeError("Failed to fetch"));

    fireEvent.click(screen.getByRole("button", { name: CANCEL_BUTTON }));

    expect(
      await screen.findByText(
        "Не вдалося скасувати запис. Перевірте інтернет і спробуйте ще раз.",
      ),
    ).toBeInTheDocument();
    const button = screen.getByRole("button", { name: CANCEL_BUTTON });
    await waitFor(() => expect(button).toBeEnabled());

    guestApiRequestMock.mockResolvedValueOnce(
      detail({ status: "cancelled", deposit_outcome: "refund_processing", refund_deadline: null }),
    );
    await act(async () => {
      fireEvent.click(button);
    });

    expect(window.confirm).toHaveBeenCalledTimes(2);
    expect(await screen.findByText("Запис скасовано.")).toBeInTheDocument();
  });
});
