// @vitest-environment jsdom
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

// docs/DECISIONS.md § "Stage 17 sub-step order", 17.7a: expired
// conversation. Driven through the widget with the real sessionId module and
// real sessionStorage, so the stored id and the "expired" flag are read
// straight from storage; only the network (assistantApi) and the router
// pathname are mocked.
const nav = vi.hoisted(() => ({ pathname: "/services" }));

vi.mock("next/navigation", () => ({
  usePathname: () => nav.pathname,
  useSearchParams: () => new URLSearchParams(""),
}));

vi.mock("@/lib/assistant/assistantApi", () => ({
  sendAssistantMessage: vi.fn(),
  getAssistantHistory: vi.fn(),
}));

// Imported after the mocks above so the mocked modules are what the widget sees.
import { ApiError } from "@/lib/api/errors";
import type { AssistantEvent, AssistantHistoryMessage } from "@/lib/assistant/assistantApi";
import { getAssistantHistory, sendAssistantMessage } from "@/lib/assistant/assistantApi";

import AssistantWidget from "./AssistantWidget";

const SESSION_KEY = "salonhub:assistant-session";
const EXPIRED_KEY = "salonhub:assistant-expired";
const EXPIRED = "Попередня розмова завершилась";
const STRIPE = "Відкрити асистентку";
const MINIMISE = "Згорнути";
const INPUT = "Повідомлення";
const SEND = "Надіслати";
const LIVE_DOT = "assistant-live-dot";

const OLD: AssistantHistoryMessage[] = [
  { role: "user", content: "Привіт" },
  { role: "assistant", content: "Вітаю!" },
];

const mockedHistory = vi.mocked(getAssistantHistory);
const mockedSend = vi.mocked(sendAssistantMessage);

let visibility: DocumentVisibilityState = "visible";
Object.defineProperty(document, "visibilityState", {
  configurable: true,
  get: () => visibility,
});
Object.defineProperty(document, "hidden", {
  configurable: true,
  get: () => visibility === "hidden",
});

function stubDesktopMatchMedia() {
  window.matchMedia = vi.fn(
    (query: string) =>
      ({
        matches: /min-width/.test(query),
        media: query,
        onchange: null,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
        addListener: vi.fn(),
        removeListener: vi.fn(),
        dispatchEvent: vi.fn(),
      }) as unknown as MediaQueryList,
  );
}

beforeEach(() => {
  sessionStorage.clear();
  nav.pathname = "/services";
  visibility = "visible";
  stubDesktopMatchMedia();
  mockedHistory.mockReset();
  mockedSend.mockReset();
  mockedHistory.mockResolvedValue([]);
});

function renderWidget() {
  const user = userEvent.setup();
  return { user, ...render(<AssistantWidget salonName="Bella" slug="bella-demo" />) };
}

async function openChat(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole("button", { name: STRIPE }));
  return screen.getByRole("dialog");
}

async function waitForRestoredConversation() {
  expect(await screen.findByTestId(LIVE_DOT)).toBeInTheDocument();
}

function tick() {
  return new Promise((resolve) => setTimeout(resolve, 0));
}

async function* streamOf(events: AssistantEvent[]): AsyncGenerator<AssistantEvent> {
  for (const event of events) {
    yield event;
  }
}

/** One full turn whose response carries `sessionId` and ends with `done`. */
function replyWith(sessionId: string, text: string) {
  mockedSend.mockResolvedValueOnce({
    sessionId,
    events: streamOf([{ type: "text", text }, { type: "done" }]),
  });
}

async function send(user: ReturnType<typeof userEvent.setup>, text: string) {
  await user.type(screen.getByRole("textbox", { name: INPUT }), text);
  await user.click(screen.getByRole("button", { name: SEND }));
}

function isBefore(a: Node, b: Node): boolean {
  return Boolean(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING);
}

function dividers() {
  return screen.queryAllByRole("separator");
}

async function setVisibility(state: DocumentVisibilityState) {
  visibility = state;
  await act(async () => {
    document.dispatchEvent(new Event("visibilitychange"));
  });
}

describe("AssistantWidget expired conversation across full loads (17.7a)", () => {
  it("test_mount_with_stored_id_and_empty_history_clears_the_id_and_sets_the_flag", async () => {
    sessionStorage.setItem(SESSION_KEY, "stored-id");
    const { user } = renderWidget();

    await waitFor(() => expect(sessionStorage.getItem(EXPIRED_KEY)).not.toBeNull());
    expect(sessionStorage.getItem(SESSION_KEY)).toBeNull();

    await openChat(user);
    expect(screen.getByText(EXPIRED)).toBeInTheDocument();
    expect(sessionStorage.getItem(EXPIRED_KEY)).toBeNull();
  });

  it("test_expiry_found_on_an_excluded_path_shows_the_notice_once_on_an_allowed_path", async () => {
    sessionStorage.setItem(SESSION_KEY, "stored-id");
    nav.pathname = "/login";
    const first = renderWidget();
    await waitFor(() => expect(sessionStorage.getItem(EXPIRED_KEY)).not.toBeNull());
    expect(sessionStorage.getItem(SESSION_KEY)).toBeNull();
    first.unmount();

    nav.pathname = "/services";
    const second = renderWidget();
    await openChat(second.user);
    expect(await screen.findByText(EXPIRED)).toBeInTheDocument();
    expect(sessionStorage.getItem(EXPIRED_KEY)).toBeNull();
    second.unmount();

    renderWidget();
    // 17.7d: the chat comes back open after a full reload.
    await screen.findByRole("dialog");
    await tick();
    expect(screen.queryByText(EXPIRED)).not.toBeInTheDocument();
  });

  it("test_flag_survives_several_full_loads_until_the_chat_is_opened", async () => {
    sessionStorage.setItem(SESSION_KEY, "stored-id");
    const first = renderWidget();
    await waitFor(() => expect(sessionStorage.getItem(EXPIRED_KEY)).not.toBeNull());
    first.unmount();

    const second = renderWidget();
    await tick();
    second.unmount();
    expect(sessionStorage.getItem(EXPIRED_KEY)).not.toBeNull();

    const third = renderWidget();
    await openChat(third.user);
    expect(await screen.findByText(EXPIRED)).toBeInTheDocument();
  });
});

describe("AssistantWidget expired conversation on a loaded page (17.7a)", () => {
  it("test_opening_a_chat_with_a_conversation_rechecks_history_and_marks_an_expired_one_with_a_divider", async () => {
    sessionStorage.setItem(SESSION_KEY, "stored-id");
    mockedHistory.mockResolvedValueOnce(OLD).mockResolvedValueOnce([]);
    const { user } = renderWidget();
    await waitForRestoredConversation();

    await openChat(user);

    const divider = await screen.findByRole("separator");
    expect(divider).toHaveTextContent(EXPIRED);
    expect(mockedHistory).toHaveBeenCalledTimes(2);
    expect(mockedHistory).toHaveBeenNthCalledWith(2, "bella-demo", "stored-id");
    expect(screen.getByText("Привіт")).toBeInTheDocument();
    expect(isBefore(screen.getByText("Вітаю!"), divider)).toBe(true);
    expect(screen.getAllByText(EXPIRED)).toHaveLength(1);
    expect(sessionStorage.getItem(SESSION_KEY)).toBeNull();
    expect(sessionStorage.getItem(EXPIRED_KEY)).toBeNull();
  });

  it("test_opening_a_chat_with_a_live_conversation_changes_nothing", async () => {
    sessionStorage.setItem(SESSION_KEY, "stored-id");
    mockedHistory.mockResolvedValue(OLD);
    const { user } = renderWidget();
    await waitForRestoredConversation();

    await openChat(user);

    await waitFor(() => expect(mockedHistory).toHaveBeenCalledTimes(2));
    await tick();
    expect(screen.getAllByText("Привіт")).toHaveLength(1);
    expect(screen.getAllByText("Вітаю!")).toHaveLength(1);
    expect(dividers()).toHaveLength(0);
    expect(screen.queryByText(EXPIRED)).not.toBeInTheDocument();
    expect(sessionStorage.getItem(SESSION_KEY)).toBe("stored-id");
  });

  it("test_opening_a_chat_without_a_conversation_makes_no_history_request", async () => {
    const { user } = renderWidget();

    await openChat(user);
    await tick();

    expect(mockedHistory).not.toHaveBeenCalled();
  });

  it("test_tab_becoming_visible_with_the_chat_open_rechecks_history", async () => {
    sessionStorage.setItem(SESSION_KEY, "stored-id");
    mockedHistory.mockResolvedValue(OLD);
    const { user } = renderWidget();
    await waitForRestoredConversation();
    await openChat(user);
    await waitFor(() => expect(mockedHistory).toHaveBeenCalledTimes(2));
    await tick();

    mockedHistory.mockResolvedValueOnce([]);
    await setVisibility("hidden");
    await setVisibility("visible");

    const divider = await screen.findByRole("separator");
    expect(divider).toHaveTextContent(EXPIRED);
    expect(mockedHistory).toHaveBeenCalledTimes(3);
    expect(isBefore(screen.getByText("Вітаю!"), divider)).toBe(true);
    expect(sessionStorage.getItem(SESSION_KEY)).toBeNull();
  });

  it("test_tab_becoming_visible_with_the_chat_collapsed_makes_no_request", async () => {
    sessionStorage.setItem(SESSION_KEY, "stored-id");
    mockedHistory.mockResolvedValue(OLD);
    renderWidget();
    await waitForRestoredConversation();

    await setVisibility("hidden");
    await setVisibility("visible");
    await tick();

    expect(mockedHistory).toHaveBeenCalledTimes(1);
  });

  it("test_send_returning_a_different_session_id_puts_a_divider_above_the_new_message", async () => {
    sessionStorage.setItem(SESSION_KEY, "stored-id");
    mockedHistory.mockResolvedValue(OLD);
    const { user } = renderWidget();
    await waitForRestoredConversation();
    await openChat(user);
    await waitFor(() => expect(mockedHistory).toHaveBeenCalledTimes(2));
    await tick();

    replyWith("new-id", "Нова відповідь");
    await send(user, "Нове питання");

    const divider = await screen.findByRole("separator");
    await screen.findByText("Нова відповідь");
    expect(divider).toHaveTextContent(EXPIRED);
    expect(isBefore(screen.getByText("Вітаю!"), divider)).toBe(true);
    expect(isBefore(divider, screen.getByText("Нове питання"))).toBe(true);
    expect(isBefore(screen.getByText("Нове питання"), screen.getByText("Нова відповідь"))).toBe(
      true,
    );
    expect(screen.getAllByText(EXPIRED)).toHaveLength(1);
    expect(sessionStorage.getItem(SESSION_KEY)).toBe("new-id");
  });

  it("test_two_expiries_give_two_dividers_in_order", async () => {
    sessionStorage.setItem(SESSION_KEY, "stored-id");
    mockedHistory.mockResolvedValueOnce(OLD).mockResolvedValueOnce([]);
    const { user } = renderWidget();
    await waitForRestoredConversation();
    await openChat(user);
    await screen.findByRole("separator");

    // The first gap was already marked on open; the send that starts the
    // new conversation (no stored id) must not add a second divider for it.
    replyWith("id-2", "Друга відповідь");
    await send(user, "Друге питання");
    await screen.findByText("Друга відповідь");
    await waitFor(() => expect(sessionStorage.getItem(SESSION_KEY)).toBe("id-2"));
    expect(dividers()).toHaveLength(1);

    await user.click(screen.getByRole("button", { name: MINIMISE }));
    mockedHistory.mockResolvedValueOnce([]);
    await openChat(user);

    await waitFor(() => expect(dividers()).toHaveLength(2));
    const [first, second] = dividers();
    expect(isBefore(screen.getByText("Вітаю!"), first)).toBe(true);
    expect(isBefore(first, screen.getByText("Друге питання"))).toBe(true);
    expect(isBefore(screen.getByText("Друга відповідь"), second)).toBe(true);
    expect(mockedHistory).toHaveBeenLastCalledWith("bella-demo", "id-2");
  });

  it("test_first_conversation_never_shows_a_divider", async () => {
    const { user } = renderWidget();
    await openChat(user);

    replyWith("server-id", "Вітаю!");
    await send(user, "Привіт");
    await screen.findByText("Вітаю!");
    await waitFor(() => expect(sessionStorage.getItem(SESSION_KEY)).toBe("server-id"));

    replyWith("server-id", "Так");
    await send(user, "Ще питання");
    await screen.findByText("Так");

    expect(dividers()).toHaveLength(0);
    expect(screen.queryByText(EXPIRED)).not.toBeInTheDocument();
  });
});

// 17.7a: only empty history means expired. A recheck that fails (a non-OK
// response or a lost connection), on open or on tab return, changes nothing:
// the conversation is still held, so the next open checks again.
describe("AssistantWidget failed recheck (17.7a)", () => {
  type User = ReturnType<typeof userEvent.setup>;

  const TRIGGERS = [
    {
      trigger: "open",
      failedCalls: 2,
      // Opening is the failing recheck itself.
      run: async (user: User, failNext: () => void) => {
        failNext();
        await openChat(user);
      },
    },
    {
      trigger: "tab_return",
      failedCalls: 3,
      // Opened with a live recheck first, then the tab-return one fails.
      run: async (user: User, failNext: () => void) => {
        await openChat(user);
        await waitFor(() => expect(mockedHistory).toHaveBeenCalledTimes(2));
        await tick();
        failNext();
        await setVisibility("hidden");
        await setVisibility("visible");
      },
    },
  ];

  const ERRORS = [
    { error: "api_error", make: () => new ApiError(500, "internal_error", "Boom") },
    { error: "network_error", make: () => new TypeError("Failed to fetch") },
  ];

  const CASES = TRIGGERS.flatMap((trigger) =>
    ERRORS.map((error) => ({ ...trigger, ...error, name: `${trigger.trigger}_${error.error}` })),
  );

  it.each(CASES)("test_failed_recheck_keeps_the_conversation_($name)", async ({ run, make, failedCalls }) => {
    sessionStorage.setItem(SESSION_KEY, "stored-id");
    mockedHistory.mockResolvedValue(OLD);
    const { user } = renderWidget();
    await waitForRestoredConversation();

    await run(user, () => mockedHistory.mockRejectedValueOnce(make()));
    // The failing recheck was made: mount + open, plus the tab return.
    await waitFor(() => expect(mockedHistory).toHaveBeenCalledTimes(failedCalls));
    await tick();
    await tick();

    expect(dividers()).toHaveLength(0);
    expect(screen.queryByText(EXPIRED)).not.toBeInTheDocument();
    expect(screen.getByText("Привіт")).toBeInTheDocument();
    expect(screen.getByText("Вітаю!")).toBeInTheDocument();
    expect(sessionStorage.getItem(SESSION_KEY)).toBe("stored-id");
    expect(sessionStorage.getItem(EXPIRED_KEY)).toBeNull();

    await user.click(screen.getByRole("button", { name: MINIMISE }));
    expect(screen.getByTestId(LIVE_DOT)).toBeInTheDocument();
    await openChat(user);
    await waitFor(() => expect(mockedHistory).toHaveBeenCalledTimes(failedCalls + 1));
  });
});
