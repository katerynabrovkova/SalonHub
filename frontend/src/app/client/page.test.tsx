// @vitest-environment jsdom
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/lib/api/errors";
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

// Imported after the mocks above so the mocked modules are what the page sees.
import { apiRequest } from "@/lib/api/client";
import { AuthProvider } from "@/app/AuthContext";
import ClientDashboardPage from "./page";

const mockedApiRequest = vi.mocked(apiRequest);

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
  replaceMock.mockReset();
});

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

  // --- 5. Empty state --------------------------------------------------

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
