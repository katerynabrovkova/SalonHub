// @vitest-environment jsdom
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// docs/DECISIONS.md § "Item 13 decisions (verification reminder)", points
// 3-5. Mockups: Stage 15 design canvas, row "Item 13".

const refreshMock = vi.fn();
const mockedUseAuth = vi.fn();
vi.mock("@/app/AuthContext", () => ({
  useAuth: () => mockedUseAuth(),
}));

vi.mock("@/lib/api/client", () => ({
  apiRequest: vi.fn(),
}));

// Imported after the mocks above so the mocked modules are what the component sees.
import { apiRequest } from "@/lib/api/client";
import { ApiError } from "@/lib/api/errors";

import VerificationBanner from "./VerificationBanner";

const mockedApiRequest = vi.mocked(apiRequest);

const EMAIL = "alice@example.com";

const UNVERIFIED_ME = {
  email: EMAIL,
  role: "client" as const,
  name: null,
  phone: null,
  email_verified: false,
};

const VERIFIED_ME = { ...UNVERIFIED_ME, email_verified: true };

const BANNER_LABEL = "Підтвердження пошти";
const TITLE = "Підтвердіть пошту";
const TEXT = `Ми надіслали лист на ${EMAIL}. Після підтвердження тут з'являться ваші записи, і ви зможете бронювати з акаунта.`;
const RESEND_BUTTON = "Надіслати лист ще раз";
const PENDING_BUTTON = "Надсилаємо...";
const SENT_MESSAGE = `Лист надіслано на ${EMAIL}.`;
const THROTTLED_MESSAGE = "Забагато спроб. Спробуйте пізніше.";
const CONNECTION_MESSAGE = "Не вдалося з'єднатися. Перевірте інтернет і спробуйте ще раз.";
const GENERIC_SEND_MESSAGE = "Не вдалося надіслати. Спробуйте ще раз.";

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

function queryBanner() {
  return screen.queryByRole("region", { name: BANNER_LABEL });
}

/** Sets `document.visibilityState` and fires `visibilitychange`. */
function changeVisibility(state: DocumentVisibilityState) {
  Object.defineProperty(document, "visibilityState", {
    configurable: true,
    get: () => state,
  });
  act(() => {
    document.dispatchEvent(new Event("visibilitychange"));
  });
}

async function clickResend() {
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: RESEND_BUTTON }));
  return user;
}

beforeEach(() => {
  refreshMock.mockReset();
  refreshMock.mockResolvedValue(undefined);
  mockedUseAuth.mockReset();
  mockedApiRequest.mockReset();
  mockAuth(UNVERIFIED_ME);
});

afterEach(() => {
  // Drops the instance property set by changeVisibility, so jsdom's own
  // getter applies again.
  Reflect.deleteProperty(document, "visibilityState");
});

describe("VerificationBanner", () => {
  it("test_renders_title_text_with_the_account_email_and_the_resend_button", () => {
    render(<VerificationBanner />);

    const banner = queryBanner();
    expect(banner).toBeInTheDocument();
    expect(screen.getByText(TITLE)).toBeInTheDocument();
    expect(screen.getByText(TEXT)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: RESEND_BUTTON })).toBeInTheDocument();
  });

  it("test_has_no_close_or_dismiss_control", () => {
    render(<VerificationBanner />);

    const buttons = screen.getAllByRole("button");
    expect(buttons).toHaveLength(1);
    expect(buttons[0]).toHaveAccessibleName(RESEND_BUTTON);
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
  });

  it("test_renders_nothing_for_a_verified_account", () => {
    mockAuth(VERIFIED_ME);

    const { container } = render(<VerificationBanner />);

    expect(container).toBeEmptyDOMElement();
  });

  it("test_resend_posts_the_account_email_once", async () => {
    mockedApiRequest.mockResolvedValueOnce(undefined);
    render(<VerificationBanner />);

    await clickResend();

    await waitFor(() => expect(mockedApiRequest).toHaveBeenCalledTimes(1));
    const [, path, options] = mockedApiRequest.mock.calls[0] as [string, string, RequestInit];
    expect(path).toBe("/auth/resend-verification/");
    expect(options.method).toBe("POST");
    expect(JSON.parse(options.body as string)).toEqual({ email: EMAIL });
  });

  it("test_button_reads_sending_and_is_disabled_while_pending_and_second_click_sends_nothing", async () => {
    let resolvePost: (value: unknown) => void = () => {};
    const pendingPost = new Promise((resolve) => {
      resolvePost = resolve;
    });
    mockedApiRequest.mockReturnValueOnce(pendingPost);
    render(<VerificationBanner />);

    const user = await clickResend();
    const pendingButton = await screen.findByRole("button", { name: PENDING_BUTTON });
    expect(pendingButton).toBeDisabled();
    expect(screen.queryByRole("button", { name: RESEND_BUTTON })).not.toBeInTheDocument();
    await user.click(pendingButton);

    expect(mockedApiRequest).toHaveBeenCalledTimes(1);

    resolvePost(undefined);
    expect(await screen.findByRole("button", { name: RESEND_BUTTON })).toBeEnabled();
  });

  it("test_202_shows_sent_message_with_the_email_in_a_status", async () => {
    mockedApiRequest.mockResolvedValueOnce(undefined);
    render(<VerificationBanner />);

    await clickResend();

    expect(await screen.findByRole("status")).toHaveTextContent(SENT_MESSAGE);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it.each([
    {
      name: "429",
      error: new ApiError(429, "throttled", "Request was throttled. Expected available in 3600 seconds."),
      text: THROTTLED_MESSAGE,
    },
    {
      name: "TypeError",
      error: new TypeError("Failed to fetch"),
      text: CONNECTION_MESSAGE,
    },
    {
      name: "500",
      error: new ApiError(500, "internal_error", "An unexpected error occurred."),
      text: GENERIC_SEND_MESSAGE,
    },
  ])("test_resend_error_shows_its_fixed_text_in_an_alert_($name)", async ({ error, text }) => {
    mockedApiRequest.mockRejectedValueOnce(error);
    render(<VerificationBanner />);

    await clickResend();

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(text);
    expect(alert).not.toHaveTextContent(error.message);
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it("test_tab_becoming_visible_calls_refresh_once", () => {
    render(<VerificationBanner />);

    changeVisibility("visible");

    expect(refreshMock).toHaveBeenCalledTimes(1);
  });

  it("test_tab_becoming_hidden_does_not_call_refresh", () => {
    render(<VerificationBanner />);

    changeVisibility("hidden");

    expect(refreshMock).not.toHaveBeenCalled();
  });

  it("test_visibilitychange_after_unmount_calls_nothing", () => {
    const { unmount } = render(<VerificationBanner />);
    unmount();

    changeVisibility("visible");

    expect(refreshMock).not.toHaveBeenCalled();
  });

  it("test_visibilitychange_for_a_verified_account_calls_nothing", () => {
    mockAuth(VERIFIED_ME);
    render(<VerificationBanner />);

    changeVisibility("visible");

    expect(refreshMock).not.toHaveBeenCalled();
  });

  // Point 3: the banner is for a client only.
  it("test_unverified_admin_sees_no_banner_and_adds_no_visibilitychange_listener", () => {
    mockAuth({ ...UNVERIFIED_ME, role: "admin" as const });
    const addListenerSpy = vi.spyOn(document, "addEventListener");

    try {
      const { container } = render(<VerificationBanner />);
      changeVisibility("visible");

      expect(container).toBeEmptyDOMElement();
      const visibilityListeners = addListenerSpy.mock.calls.filter(
        ([type]) => type === "visibilitychange",
      );
      expect(visibilityListeners).toHaveLength(0);
      expect(refreshMock).not.toHaveBeenCalled();
    } finally {
      addListenerSpy.mockRestore();
    }
  });

  it("test_banner_disappears_when_refresh_brings_email_verified_true", () => {
    const { rerender } = render(<VerificationBanner />);
    expect(queryBanner()).toBeInTheDocument();

    mockAuth(VERIFIED_ME);
    rerender(<VerificationBanner />);

    expect(queryBanner()).not.toBeInTheDocument();
    expect(screen.queryByText(TITLE)).not.toBeInTheDocument();
  });
});
