// @vitest-environment jsdom
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, RenewalUnsureError } from "@/lib/api/errors";
import type { MyAppointment } from "@/lib/booking/getMyAppointments";

// Mock the api/client.ts function boundary, not raw fetch -- getMyAppointments
// is a thin wrapper over apiRequest, so mocking here covers both the list
// fetch and the direct cancel POST this page also makes (same convention as
// login/page.test.tsx).
vi.mock("@/lib/api/client", () => ({
  apiRequest: vi.fn(),
}));

// ClientAvatarMenu (item 7) calls useRouter() for its logout redirect --
// same mock shape as login/page.test.tsx, just replace instead of push.
const replaceMock = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: replaceMock }),
}));

// The pay action (docs/DECISIONS.md § Stage 15 planning, item 14 design
// details) goes through its own helper, mocked at that boundary so these
// tests pin what the card does with the helper's result, independent of how
// the helper talks to apiRequest (covered by payForAppointment.test.ts).
vi.mock("@/lib/booking/payForAppointment", () => ({
  payForAppointment: vi.fn(),
}));

// Imported after the mocks above so the mocked modules are what the page sees.
import { apiRequest } from "@/lib/api/client";
import { payForAppointment } from "@/lib/booking/payForAppointment";
import { AuthProvider } from "@/app/AuthContext";
import ClientDashboardPage from "./page";

const mockedApiRequest = vi.mocked(apiRequest);
const mockedPay = vi.mocked(payForAppointment);

// ClientAvatarMenu (item 7) reads useAuth(), so this page now needs a real
// AuthProvider wrapping it, not just the bare component -- same reasoning
// login/page.test.tsx/register's tests already wrap their pages.
const ME = {
  email: "alice@example.com",
  role: "client" as const,
  name: "Alice",
  phone: "+10000000000",
  email_verified: true,
};

function renderDashboard() {
  return render(
    <AuthProvider>
      <ClientDashboardPage />
    </AuthProvider>,
  );
}

const DAY_MS = 24 * 60 * 60 * 1000;
const FUTURE = new Date(Date.now() + 3 * DAY_MS).toISOString();
const PAST = new Date(Date.now() - 3 * DAY_MS).toISOString();

let nextId = 1;

function buildAppointment(overrides: Partial<MyAppointment> = {}): MyAppointment {
  const id = nextId++;
  return {
    id,
    status: "confirmed",
    start_datetime: FUTURE,
    end_datetime: FUTURE,
    specialist: { id: 100, name: "Jane" },
    service: { id: 200, name: "Manicure" },
    cancelled_at: null,
    cancelled_by: null,
    cancellation_reason: "",
    service_price_at_booking: "500.00",
    deposit_percentage_at_booking: "20.00",
    payment_status: null,
    payment_amount: null,
    amount_due_at_visit: null,
    ...overrides,
  };
}

function emptyPage() {
  return { count: 0, next: null, previous: null, results: [] };
}

function pageOf(appointments: MyAppointment[]) {
  return { count: appointments.length, next: null, previous: null, results: appointments };
}

/**
 * Routes every mocked apiRequest call by its `path` (and method for the
 * cancel POST), same discipline as login/page.test.tsx's mockApiRoutes --
 * keeps tests correct regardless of exact call ordering.
 */
function mockApiRoutes({
  list,
  cancel,
}: {
  list?: () => unknown;
  cancel?: (id: number) => unknown;
} = {}) {
  mockedApiRequest.mockImplementation((...args) => {
    const [, path, options] = args as [string, string, RequestInit | undefined];
    if (path === "/auth/me/") {
      return Promise.resolve(ME);
    }
    if (path === "/appointments/mine/") {
      return Promise.resolve(list ? list() : emptyPage());
    }
    const cancelMatch = /^\/appointments\/(\d+)\/cancel\/$/.exec(path);
    if (cancelMatch && (options?.method ?? "GET").toUpperCase() === "POST") {
      const id = Number(cancelMatch[1]);
      return cancel
        ? Promise.resolve(cancel(id))
        : Promise.reject(new Error(`unexpected cancel call for ${id}`));
    }
    throw new Error(`unexpected apiRequest call: ${path}`);
  });
}

beforeEach(() => {
  nextId = 1;
  mockedApiRequest.mockReset();
  mockedPay.mockReset();
  replaceMock.mockReset();
});

/** How many times the page has fetched `appointments/mine/` so far. */
function listFetchCount(): number {
  return mockedApiRequest.mock.calls.filter(([, path]) => path === "/appointments/mine/").length;
}

/** The card (`<li>`) whose service name is `serviceName`. */
async function findCard(serviceName: string): Promise<HTMLElement> {
  const nameEl = await screen.findByText(serviceName);
  const card = nameEl.closest("li");
  expect(card).not.toBeNull();
  return card as HTMLElement;
}

function payResponse(providerData: string | null) {
  return {
    payment: { id: 900, status: "pending", amount: "100.00", currency: "UAH" },
    provider_data: providerData,
  };
}

const AWAITING_CONFIRMATION = /Очікуємо підтвердження оплати/;
const CONNECTION_FAILED = "Не вдалося з'єднатися. Перевірте інтернет і спробуйте ще раз.";
const PAYMENT_SERVICE_DOWN = "Сервіс оплати тимчасово не відповідає. Спробуйте за кілька хвилин.";

describe("ClientDashboardPage", () => {
  // --- 1. Section split ------------------------------------------------

  it("test_splits_appointments_into_upcoming_and_past_sections", async () => {
    const upcomingConfirmed = buildAppointment({ status: "confirmed", start_datetime: FUTURE });
    const upcomingPending = buildAppointment({ status: "pending_payment", start_datetime: FUTURE });
    const completedPast = buildAppointment({ status: "completed", start_datetime: PAST });
    // Cancelled but with a FUTURE start_datetime -- must still land in
    // "Минулі", never "Найближчі" (docs/DECISIONS.md § Stage 15 planning,
    // item 4: "a cancelled appointment always shows under Минулі
    // regardless of its start_datetime").
    const cancelledFutureDated = buildAppointment({
      status: "cancelled",
      start_datetime: FUTURE,
    });
    mockApiRoutes({
      list: () =>
        pageOf([upcomingConfirmed, upcomingPending, completedPast, cancelledFutureDated]),
    });

    renderDashboard();

    await waitFor(() => expect(screen.getByText("Найближчі")).toBeInTheDocument());

    const upcomingHeading = screen.getByRole("heading", { name: "Найближчі" });
    const pastHeading = screen.getByRole("heading", { name: "Минулі" });
    const upcomingSection = upcomingHeading.closest("section");
    const pastSection = pastHeading.closest("section");
    expect(upcomingSection).not.toBeNull();
    expect(pastSection).not.toBeNull();

    // Two confirmed/pending_payment + future-dated cards under Найближчі.
    expect(upcomingSection?.querySelectorAll("li").length).toBe(2);
    // completed (past) + cancelled-but-future-dated under Минулі.
    expect(pastSection?.querySelectorAll("li").length).toBe(2);
    expect(pastSection?.textContent).toContain("Скасовано");
  });

  // --- 2. Status badges --------------------------------------------------

  it.each([
    ["confirmed", "Підтверджено"],
    ["pending_payment", "Очікує оплати"],
    ["completed", "Завершено"],
    ["cancelled", "Скасовано"],
    ["expired", "Термін минув"],
  ] as const)("test_status_badge_%s_renders_as_%s", async (status, badgeText) => {
    mockApiRoutes({
      list: () => pageOf([buildAppointment({ status, start_datetime: PAST })]),
    });

    renderDashboard();

    await waitFor(() => expect(screen.getByText(badgeText)).toBeInTheDocument());
  });

  // --- 3. Payment-line variants -------------------------------------------

  it("test_payment_line_confirmed_succeeded_shows_amount_due_at_visit", async () => {
    mockApiRoutes({
      list: () =>
        pageOf([
          buildAppointment({
            status: "confirmed",
            start_datetime: FUTURE,
            payment_status: "succeeded",
            payment_amount: "100.00",
            amount_due_at_visit: "400.00",
          }),
        ]),
    });

    renderDashboard();

    await waitFor(() =>
      expect(screen.getByText("Оплата при візиті: 400.00 ₴")).toBeInTheDocument(),
    );
  });

  it("test_payment_line_completed_shows_no_line_even_with_a_succeeded_payment", async () => {
    mockApiRoutes({
      list: () =>
        pageOf([
          buildAppointment({
            status: "completed",
            start_datetime: PAST,
            payment_status: "succeeded",
            payment_amount: "100.00",
            amount_due_at_visit: "400.00",
          }),
        ]),
    });

    renderDashboard();

    await waitFor(() => expect(screen.getByText("Завершено")).toBeInTheDocument());
    expect(screen.queryByText(/Оплата при візиті/)).not.toBeInTheDocument();
    expect(screen.queryByText(/₴/)).not.toBeInTheDocument();
  });

  it("test_payment_line_cancelled_succeeded_shows_deposit_withheld", async () => {
    mockApiRoutes({
      list: () =>
        pageOf([
          buildAppointment({
            status: "cancelled",
            start_datetime: PAST,
            payment_status: "succeeded",
            payment_amount: "100.00",
          }),
        ]),
    });

    renderDashboard();

    await waitFor(() =>
      expect(screen.getByText("Депозит утримано: 100.00 ₴")).toBeInTheDocument(),
    );
  });

  it("test_payment_line_cancelled_refund_pending_shows_processing_message", async () => {
    mockApiRoutes({
      list: () =>
        pageOf([
          buildAppointment({
            status: "cancelled",
            start_datetime: PAST,
            payment_status: "refund_pending",
            payment_amount: "100.00",
          }),
        ]),
    });

    renderDashboard();

    await waitFor(() =>
      expect(screen.getByText("Повернення обробляється")).toBeInTheDocument(),
    );
  });

  it("test_payment_line_cancelled_refunded_shows_refunded_amount", async () => {
    mockApiRoutes({
      list: () =>
        pageOf([
          buildAppointment({
            status: "cancelled",
            start_datetime: PAST,
            payment_status: "refunded",
            payment_amount: "100.00",
          }),
        ]),
    });

    renderDashboard();

    await waitFor(() => expect(screen.getByText("Повернено: 100.00 ₴")).toBeInTheDocument());
  });

  // --- 4. Cancel action ----------------------------------------------------

  it("test_cancel_success_refetches_and_the_item_moves_to_minuli_as_cancelled", async () => {
    const user = userEvent.setup();
    const appointment = buildAppointment({ status: "confirmed", start_datetime: FUTURE });
    let cancelled = false;
    mockApiRoutes({
      list: () =>
        pageOf([cancelled ? { ...appointment, status: "cancelled" as const } : appointment]),
      cancel: (id) => {
        cancelled = true;
        return { ...appointment, id, status: "cancelled" };
      },
    });

    renderDashboard();

    await waitFor(() => expect(screen.getByText("Підтверджено")).toBeInTheDocument());

    await user.click(screen.getByRole("button", { name: "Скасувати" }));

    await waitFor(() => expect(screen.getByText("Скасовано")).toBeInTheDocument());
    // Section reflects the refetched (now cancelled) state -- no longer
    // treated as upcoming, no lingering "Підтверджено" badge.
    expect(screen.queryByText("Підтверджено")).not.toBeInTheDocument();
    const pastSection = screen.getByRole("heading", { name: "Минулі" }).closest("section");
    expect(pastSection?.textContent).toContain("Скасовано");
  });

  it("test_cancel_409_shows_error_message_without_crashing", async () => {
    const user = userEvent.setup();
    const appointment = buildAppointment({ status: "confirmed", start_datetime: FUTURE });
    mockApiRoutes({
      list: () => pageOf([appointment]),
      cancel: () => {
        throw new ApiError(409, "invalid_state_transition", "Conflict.");
      },
    });

    renderDashboard();
    await waitFor(() => expect(screen.getByText("Підтверджено")).toBeInTheDocument());

    await user.click(screen.getByRole("button", { name: "Скасувати" }));

    await waitFor(() =>
      expect(screen.getByText("Це бронювання вже не можна скасувати.")).toBeInTheDocument(),
    );
    // Still rendered, still confirmed -- no crash, no silent data loss.
    expect(screen.getByText("Підтверджено")).toBeInTheDocument();
  });

  it("test_cancel_404_shows_error_message_without_crashing", async () => {
    const user = userEvent.setup();
    const appointment = buildAppointment({ status: "confirmed", start_datetime: FUTURE });
    mockApiRoutes({
      list: () => pageOf([appointment]),
      cancel: () => {
        throw new ApiError(404, "not_found", "Not found.");
      },
    });

    renderDashboard();
    await waitFor(() => expect(screen.getByText("Підтверджено")).toBeInTheDocument());

    await user.click(screen.getByRole("button", { name: "Скасувати" }));

    await waitFor(() =>
      expect(screen.getByText("Бронювання не знайдено.")).toBeInTheDocument(),
    );
    expect(screen.getByText("Підтверджено")).toBeInTheDocument();
  });

  // --- 5. Pay action (item 14 design details, P3) ------------------------

  it("test_pay_button_shows_only_on_the_pending_payment_card", async () => {
    mockApiRoutes({
      list: () =>
        pageOf([
          buildAppointment({ status: "pending_payment", service: { id: 1, name: "Pending" } }),
          buildAppointment({ status: "confirmed", service: { id: 2, name: "Confirmed" } }),
          buildAppointment({
            status: "cancelled",
            start_datetime: PAST,
            service: { id: 3, name: "Cancelled" },
          }),
          buildAppointment({
            status: "expired",
            start_datetime: PAST,
            service: { id: 4, name: "Expired" },
          }),
        ]),
    });

    renderDashboard();

    const pending = await findCard("Pending");
    expect(within(pending).getByRole("button", { name: "Оплатити" })).toBeInTheDocument();
    for (const name of ["Confirmed", "Cancelled", "Expired"]) {
      const card = await findCard(name);
      expect(within(card).queryByRole("button", { name: "Оплатити" })).not.toBeInTheDocument();
    }
  });

  it("test_pay_click_calls_the_helper_once_with_the_id_and_disables_the_button_in_flight", async () => {
    const user = userEvent.setup();
    const appointment = buildAppointment({ status: "pending_payment" });
    mockApiRoutes({ list: () => pageOf([appointment]) });
    let resolvePay: (value: ReturnType<typeof payResponse>) => void = () => {};
    mockedPay.mockImplementation(
      () =>
        new Promise((resolve) => {
          resolvePay = resolve;
        }),
    );

    renderDashboard();
    const card = await findCard("Manicure");
    const button = within(card).getByRole("button", { name: "Оплатити" });

    await user.click(button);

    await waitFor(() => expect(button).toBeDisabled());
    expect(mockedPay).toHaveBeenCalledTimes(1);
    expect(mockedPay).toHaveBeenCalledWith(expect.any(String), appointment.id);

    resolvePay(payResponse(null));
    await waitFor(() => expect(within(card).getByText(AWAITING_CONFIRMATION)).toBeInTheDocument());
  });

  it("test_pay_success_refetches_the_list", async () => {
    const user = userEvent.setup();
    mockApiRoutes({ list: () => pageOf([buildAppointment({ status: "pending_payment" })]) });
    mockedPay.mockResolvedValue(payResponse(null));

    renderDashboard();
    const card = await findCard("Manicure");
    const fetchesBefore = listFetchCount();

    await user.click(within(card).getByRole("button", { name: "Оплатити" }));

    await waitFor(() => expect(listFetchCount()).toBe(fetchesBefore + 1));
  });

  it("test_pay_success_with_provider_data_shows_a_link_to_it_on_the_card", async () => {
    const user = userEvent.setup();
    const invoiceUrl = "https://secure.example-psp.test/invoice/abc123";
    const appointment = buildAppointment({ status: "pending_payment" });
    let paid = false;
    mockApiRoutes({
      // After paying, the list reflects the PENDING Payment -- the link must
      // survive that refetch, since only the pay response carries it.
      list: () => pageOf([paid ? { ...appointment, payment_status: "pending" as const } : appointment]),
    });
    mockedPay.mockImplementation(async () => {
      paid = true;
      return payResponse(invoiceUrl);
    });

    renderDashboard();
    const card = await findCard("Manicure");

    await user.click(within(card).getByRole("button", { name: "Оплатити" }));

    await waitFor(() => {
      const links = within(card).queryAllByRole("link");
      expect(links.some((link) => link.getAttribute("href") === invoiceUrl)).toBe(true);
    });
  });

  it.each([
    ["javascript_url", "javascript:alert(1)"],
    ["http_url", "http://insecure.example-psp.test/invoice/abc123"],
    ["not_a_url", "not a url at all"],
    ["empty_string", ""],
  ])(
    "test_pay_success_with_non_https_provider_data_%s_renders_no_link",
    async (_label, providerData) => {
      const user = userEvent.setup();
      // Built once, outside `list`: the refetch must return the SAME id.
      const appointment = buildAppointment({ status: "pending_payment" });
      mockApiRoutes({ list: () => pageOf([appointment]) });
      mockedPay.mockResolvedValue(payResponse(providerData));

      renderDashboard();
      const card = await findCard("Manicure");

      await user.click(within(card).getByRole("button", { name: "Оплатити" }));

      await waitFor(() =>
        expect(within(card).getByText(AWAITING_CONFIRMATION)).toBeInTheDocument(),
      );
      expect(within(card).queryAllByRole("link")).toHaveLength(0);
      expect(document.querySelector('a[href^="javascript:"]')).toBeNull();
    },
  );

  it.each([
    ["without_provider_data", null],
    ["with_https_provider_data", "https://secure.example-psp.test/invoice/abc123"],
  ])(
    "test_pay_state_is_not_rendered_once_the_refetched_card_is_confirmed_%s",
    async (_label, providerData) => {
    const user = userEvent.setup();
    const appointment = buildAppointment({ status: "pending_payment" });
    let paid = false;
    // The webhook confirmed the booking between the pay call and the
    // refetch: stale per-card pay state must not linger on a confirmed card.
    mockApiRoutes({
      list: () =>
        pageOf([
          paid
            ? { ...appointment, status: "confirmed" as const, payment_status: "succeeded" as const }
            : appointment,
        ]),
    });
    mockedPay.mockImplementation(async () => {
      paid = true;
      return payResponse(providerData);
    });

    renderDashboard();
    const card = await findCard("Manicure");

    await user.click(within(card).getByRole("button", { name: "Оплатити" }));

    await waitFor(() => expect(screen.getByText("Підтверджено")).toBeInTheDocument());
    const refetchedCard = await findCard("Manicure");
    expect(within(refetchedCard).queryByText(AWAITING_CONFIRMATION)).not.toBeInTheDocument();
    expect(within(refetchedCard).queryAllByRole("link")).toHaveLength(0);
    },
  );

  it("test_pay_success_without_provider_data_shows_awaiting_confirmation_on_the_card", async () => {
    const user = userEvent.setup();
    // The list keeps reporting no Payment, so the message can only come from
    // the pay response itself, not from test 7's payment_status rule. Built
    // once, outside `list`: the refetch must return the SAME id.
    const appointment = buildAppointment({ status: "pending_payment" });
    mockApiRoutes({ list: () => pageOf([appointment]) });
    mockedPay.mockResolvedValue(payResponse(null));

    renderDashboard();
    const card = await findCard("Manicure");
    expect(within(card).queryByText(AWAITING_CONFIRMATION)).not.toBeInTheDocument();

    await user.click(within(card).getByRole("button", { name: "Оплатити" }));

    await waitFor(() => expect(within(card).getByText(AWAITING_CONFIRMATION)).toBeInTheDocument());
  });

  it("test_pending_payment_card_with_a_pending_payment_shows_awaiting_confirmation_and_the_button", async () => {
    mockApiRoutes({
      list: () =>
        pageOf([
          buildAppointment({
            status: "pending_payment",
            payment_status: "pending",
            payment_amount: "100.00",
          }),
        ]),
    });

    renderDashboard();
    const card = await findCard("Manicure");

    expect(within(card).getByText(AWAITING_CONFIRMATION)).toBeInTheDocument();
    expect(within(card).getByRole("button", { name: "Оплатити" })).toBeInTheDocument();
    expect(mockedPay).not.toHaveBeenCalled();
  });

  it("test_pay_409_shows_unavailable_message_and_refetches_the_list", async () => {
    const user = userEvent.setup();
    const appointment = buildAppointment({ status: "pending_payment" });
    let conflicted = false;
    mockApiRoutes({
      list: () => pageOf([conflicted ? { ...appointment, status: "expired" as const } : appointment]),
    });
    mockedPay.mockImplementation(async () => {
      conflicted = true;
      throw new ApiError(409, "invalid_state_transition", "Conflict.");
    });

    renderDashboard();
    const card = await findCard("Manicure");
    const fetchesBefore = listFetchCount();

    await user.click(within(card).getByRole("button", { name: "Оплатити" }));

    await waitFor(() => expect(listFetchCount()).toBe(fetchesBefore + 1));
    // The refetch moves the (now expired) card to "Минулі", so look it up
    // again rather than reusing the pre-click element.
    await waitFor(() => {
      const refetchedCard = screen.getByText("Manicure").closest("li") as HTMLElement;
      expect(
        within(refetchedCard).getByText("Це бронювання більше недоступне для оплати."),
      ).toBeInTheDocument();
    });
  });

  it("test_pay_404_shows_not_found_on_the_card", async () => {
    const user = userEvent.setup();
    mockApiRoutes({ list: () => pageOf([buildAppointment({ status: "pending_payment" })]) });
    mockedPay.mockRejectedValue(new ApiError(404, "not_found", "Not found."));

    renderDashboard();
    const card = await findCard("Manicure");

    await user.click(within(card).getByRole("button", { name: "Оплатити" }));

    await waitFor(() =>
      expect(within(card).getByText("Бронювання не знайдено.")).toBeInTheDocument(),
    );
  });

  it.each([
    // Reached the server, which (or whose payment provider) failed.
    [
      "502",
      () => new ApiError(502, "payment_provider_error", "Bad gateway."),
      PAYMENT_SERVICE_DOWN,
    ],
    // Never reached the server: fetch itself threw.
    ["network_error", () => new TypeError("Failed to fetch"), CONNECTION_FAILED],
    // apiRequest's S2 silent renewal couldn't tell whether the session
    // survived -- a connectivity problem from the user's point of view.
    ["renewal_unsure", () => new RenewalUnsureError(), CONNECTION_FAILED],
  ] as const)(
    "test_pay_%s_shows_its_error_message_on_the_card",
    async (_label, makeError, expectedMessage) => {
      const user = userEvent.setup();
      mockApiRoutes({ list: () => pageOf([buildAppointment({ status: "pending_payment" })]) });
      mockedPay.mockRejectedValue(makeError());

      renderDashboard();
      const card = await findCard("Manicure");

      await user.click(within(card).getByRole("button", { name: "Оплатити" }));

      await waitFor(() => expect(within(card).getByText(expectedMessage)).toBeInTheDocument());
    },
  );

  it("test_pay_error_on_one_card_does_not_show_on_another", async () => {
    const user = userEvent.setup();
    const first = buildAppointment({ status: "pending_payment", service: { id: 1, name: "First" } });
    const second = buildAppointment({
      status: "pending_payment",
      service: { id: 2, name: "Second" },
    });
    mockApiRoutes({ list: () => pageOf([first, second]) });
    mockedPay.mockRejectedValue(new ApiError(502, "payment_provider_error", "Bad gateway."));

    renderDashboard();
    const firstCard = await findCard("First");
    const secondCard = await findCard("Second");

    await user.click(within(firstCard).getByRole("button", { name: "Оплатити" }));

    await waitFor(() =>
      expect(within(firstCard).getByText(PAYMENT_SERVICE_DOWN)).toBeInTheDocument(),
    );
    expect(within(secondCard).queryByText(PAYMENT_SERVICE_DOWN)).not.toBeInTheDocument();
    expect(screen.getAllByText(PAYMENT_SERVICE_DOWN)).toHaveLength(1);
  });

  // --- 6. Empty state --------------------------------------------------

  it("test_empty_state_renders_when_there_are_no_appointments", async () => {
    mockApiRoutes({ list: emptyPage });

    renderDashboard();

    await waitFor(() =>
      expect(screen.getByText("У вас поки немає жодного запису.")).toBeInTheDocument(),
    );
    expect(screen.getByRole("link", { name: "Забронювати візит" })).toHaveAttribute(
      "href",
      "/services",
    );
    expect(screen.queryByText("Найближчі")).not.toBeInTheDocument();
    expect(screen.queryByText("Минулі")).not.toBeInTheDocument();
  });
});
