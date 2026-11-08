// @vitest-environment jsdom
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// docs/DECISIONS.md § "Stage 17 sub-step order", 17.7c, starter questions
// (decided 06.11.2026 and 08.11.2026): the chips show under the greeting with
// the label "Часті питання" only when the salon has starters, the
// conversation has no messages besides the greeting, and `historyStatus` is
// "none" or "loaded" (an expired conversation included). Hidden while the
// history loads or a "Повторити" retry runs and after a failed load. A tap is
// a send: it sends that text at once, resumes scroll following (17.7i) and
// moves focus like sending (desktop: the input; phone: blur), but keeps the
// draft. Also the textarea's `name="message"` and `autoComplete="off"`.
//
// Driven through the widget with real sessionStorage; only the network
// (assistantApi) and the router are mocked.

const nav = vi.hoisted(() => ({ pathname: "/services" }));

vi.mock("next/navigation", () => ({
  usePathname: () => nav.pathname,
  useSearchParams: () => new URLSearchParams(),
}));

vi.mock("@/lib/assistant/assistantApi", () => ({
  sendAssistantMessage: vi.fn(),
  getAssistantHistory: vi.fn(),
}));

// Imported after the mocks above so the mocked modules are what the widget sees.
import type { AssistantEvent, AssistantHistoryMessage } from "@/lib/assistant/assistantApi";
import { getAssistantHistory, sendAssistantMessage } from "@/lib/assistant/assistantApi";

import AssistantWidget from "./AssistantWidget";

const SESSION_KEY = "salonhub:assistant-session";
const PENDING_KEY = "salonhub:assistant-pending";
const DRAFT_KEY = "salonhub:assistant-draft";

const STRIPE = "Відкрити асистентку";
const INPUT = "Повідомлення";
const SEND = "Надіслати";
const RETRY = "Повторити";
const TO_BOTTOM = "Прокрутити донизу";
const LABEL = "Часті питання";
const GREETING_START = "Вітаю! Я допоможу";
const EXPIRED = "Попередня розмова завершилась";
const LOAD_FAILED = "Не вдалося завантажити розмову.";
const STARTERS = ["Які у вас ціни?", "Записатися на манікюр"];
const DRAFT = "А в неділю?";
const OLD: AssistantHistoryMessage[] = [
  { role: "user", content: "Привіт" },
  { role: "assistant", content: "Вітаю!" },
];

const mockedHistory = vi.mocked(getAssistantHistory);
const mockedSend = vi.mocked(sendAssistantMessage);

let desktop = true;

function stubMatchMedia() {
  window.matchMedia = vi.fn((query: string) => {
    const min = /min-width:\s*(\d+)px/.exec(query);
    const width = desktop ? 1280 : 390;
    return {
      matches: min !== null && width >= Number(min[1]),
      media: query,
      onchange: null,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
    } as unknown as MediaQueryList;
  });
}

function renderWidget(starters: string[] = STARTERS) {
  const user = userEvent.setup();
  return { user, ...render(<AssistantWidget salonName="Bella" slug="bella-demo" starters={starters} />) };
}

async function openChat(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole("button", { name: STRIPE }));
  return screen.getByRole("dialog");
}

function input() {
  return screen.getByRole("textbox", { name: INPUT });
}

/** A plain assertion first, so a missing chip fails as an AssertionError. */
function chip(text: string): HTMLElement {
  const node = screen.queryByRole("button", { name: text });
  expect(node).not.toBeNull();
  return node as HTMLElement;
}

function tick() {
  return new Promise((resolve) => setTimeout(resolve, 0));
}

function chipsShown() {
  expect(screen.queryByText(LABEL)).toBeInTheDocument();
  for (const text of STARTERS) {
    expect(screen.queryByRole("button", { name: text })).toBeInTheDocument();
  }
}

function noChips() {
  expect(screen.queryByText(LABEL)).not.toBeInTheDocument();
  for (const text of STARTERS) {
    expect(screen.queryByRole("button", { name: text })).not.toBeInTheDocument();
  }
}

/** A reply that streams and never finishes, so no end-of-turn effect runs. */
function sendStaysStreaming() {
  async function* events(): AsyncGenerator<AssistantEvent> {
    await new Promise<never>(() => {});
  }
  mockedSend.mockResolvedValueOnce({ sessionId: "server-id", events: events() });
}

/** The next history call stays pending until resolved or rejected. */
function pendingHistory() {
  const handle = {
    resolve: (() => {}) as (messages: AssistantHistoryMessage[]) => void,
    reject: (() => {}) as (error: unknown) => void,
  };
  mockedHistory.mockImplementationOnce(
    () =>
      new Promise<AssistantHistoryMessage[]>((resolve, reject) => {
        handle.resolve = resolve;
        handle.reject = reject;
      }),
  );
  return handle;
}

/** Mount with a stored id, open the chat and let the history answer settle. */
async function openWithStoredId(user: ReturnType<typeof userEvent.setup>) {
  await openChat(user);
  await waitFor(() => expect(mockedHistory).toHaveBeenCalled());
  await act(async () => {
    await tick();
  });
}

beforeEach(() => {
  sessionStorage.clear();
  nav.pathname = "/services";
  desktop = true;
  stubMatchMedia();
  mockedHistory.mockReset();
  mockedSend.mockReset();
  mockedHistory.mockResolvedValue([]);
});

afterEach(() => {
  vi.restoreAllMocks();
});

// --- 1. No starters ------------------------------------------------------------

describe("AssistantWidget starter questions: none configured (17.7c)", () => {
  it("test_empty_starters_render_no_chips_and_no_label", async () => {
    const { user } = renderWidget([]);
    await openChat(user);

    expect(screen.queryByText(LABEL)).not.toBeInTheDocument();
    expect(screen.queryAllByRole("button", { name: STARTERS[0] })).toHaveLength(0);
  });
});

// --- 2. Shown -------------------------------------------------------------------

describe("AssistantWidget starter questions: shown (17.7c)", () => {
  it("test_chips_show_with_the_label_with_no_stored_id", async () => {
    const { user } = renderWidget();
    await openChat(user);

    chipsShown();
  });

  it("test_chips_show_under_the_greeting", async () => {
    const { user } = renderWidget();
    await openChat(user);

    const greeting = screen.getByText((text) => text.startsWith(GREETING_START));
    const label = screen.queryByText(LABEL);
    expect(label).not.toBeNull();
    expect(greeting.compareDocumentPosition(label as Node) & Node.DOCUMENT_POSITION_FOLLOWING).not.toBe(0);
  });

  it("test_chips_show_in_order", async () => {
    const { user } = renderWidget();
    await openChat(user);

    const names = STARTERS.map((text) => screen.queryByRole("button", { name: text }));
    expect(names.every((node) => node !== null)).toBe(true);
    expect(
      (names[0] as Node).compareDocumentPosition(names[1] as Node) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).not.toBe(0);
  });

  it("test_chips_show_after_an_expired_history", async () => {
    sessionStorage.setItem(SESSION_KEY, "stored-id");
    mockedHistory.mockResolvedValueOnce([]);
    const { user } = renderWidget();
    await openWithStoredId(user);

    // Precondition: the expiry notice shows.
    expect(screen.getByText(EXPIRED)).toBeInTheDocument();
    chipsShown();
  });
});

// --- 3. Hidden ------------------------------------------------------------------

describe("AssistantWidget starter questions: hidden (17.7c)", () => {
  it("test_no_chips_while_the_history_loads", async () => {
    sessionStorage.setItem(SESSION_KEY, "stored-id");
    pendingHistory();
    const { user } = renderWidget();
    await openChat(user);

    noChips();
  });

  // Every load fails: opening the chat retries it (17.7h).
  it("test_no_chips_after_a_failed_history_load", async () => {
    sessionStorage.setItem(SESSION_KEY, "stored-id");
    mockedHistory.mockRejectedValue(new TypeError("Failed to fetch"));
    const { user } = renderWidget();
    await openWithStoredId(user);
    // Precondition: the failure block shows.
    expect(screen.queryByText(LOAD_FAILED)).toBeInTheDocument();

    noChips();
  });

  it("test_no_chips_during_a_history_retry", async () => {
    sessionStorage.setItem(SESSION_KEY, "stored-id");
    mockedHistory.mockRejectedValue(new TypeError("Failed to fetch"));
    const { user } = renderWidget();
    await openWithStoredId(user);
    // Precondition: the failure block offers "Повторити".
    const retry = screen.queryByRole("button", { name: RETRY });
    expect(retry).toBeInTheDocument();
    pendingHistory();

    await user.click(retry as HTMLElement);

    // Precondition: the retry is running.
    expect(screen.queryByText(LOAD_FAILED)).not.toBeInTheDocument();
    noChips();
  });

  it("test_no_chips_after_a_non_empty_history", async () => {
    sessionStorage.setItem(SESSION_KEY, "stored-id");
    mockedHistory.mockResolvedValueOnce(OLD);
    const { user } = renderWidget();
    await openWithStoredId(user);
    // Precondition: the history shows.
    expect(screen.getByText("Привіт")).toBeInTheDocument();

    noChips();
  });

  it("test_no_chips_while_a_pending_text_is_shown", async () => {
    sessionStorage.setItem(PENDING_KEY, "Чи є час у суботу?");
    const { user } = renderWidget();
    await openChat(user);
    // Precondition: the pending text and its "Повторити" show.
    expect(screen.getByText("Чи є час у суботу?")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: RETRY })).toBeInTheDocument();

    noChips();
  });

  it("test_no_chips_after_the_first_message_is_sent", async () => {
    const { user } = renderWidget();
    await openChat(user);
    sendStaysStreaming();

    await user.type(input(), "Привіт");
    await user.click(screen.getByRole("button", { name: SEND }));

    // Precondition: the message shows.
    expect(screen.getByText("Привіт")).toBeInTheDocument();
    noChips();
  });
});

// --- 4. A tap -------------------------------------------------------------------

describe("AssistantWidget starter questions: a tap sends (17.7c)", () => {
  it("test_tap_sends_exactly_that_text", async () => {
    const { user } = renderWidget();
    await openChat(user);
    sendStaysStreaming();

    await user.click(chip(STARTERS[1]));

    expect(mockedSend).toHaveBeenCalledTimes(1);
    expect(mockedSend.mock.calls[0][1]).toBe(STARTERS[1]);
    expect(mockedSend.mock.calls[0][2]).toBeNull();
  });

  it("test_tap_shows_the_text_as_her_message_and_hides_the_chips", async () => {
    const { user } = renderWidget();
    await openChat(user);
    sendStaysStreaming();

    await user.click(chip(STARTERS[0]));

    expect(screen.getByText(STARTERS[0])).toBeInTheDocument();
    expect(screen.queryByText(LABEL)).not.toBeInTheDocument();
  });

  it("test_tap_keeps_the_draft_in_the_input_and_in_storage", async () => {
    const { user } = renderWidget();
    await openChat(user);
    await user.type(input(), DRAFT);
    sendStaysStreaming();

    await user.click(chip(STARTERS[0]));

    // Precondition: the tap sent.
    expect(mockedSend).toHaveBeenCalledTimes(1);
    expect(input()).toHaveValue(DRAFT);
    expect(sessionStorage.getItem(DRAFT_KEY)).toBe(DRAFT);
  });

  it("test_tap_focuses_the_input_on_desktop", async () => {
    desktop = true;
    stubMatchMedia();
    const { user } = renderWidget();
    await openChat(user);
    sendStaysStreaming();

    await user.click(chip(STARTERS[0]));

    // Precondition: the tap sent, and the reply is still streaming.
    expect(mockedSend).toHaveBeenCalledTimes(1);
    expect(input()).toHaveFocus();
  });

  it("test_tap_blurs_the_input_on_the_phone", async () => {
    desktop = false;
    stubMatchMedia();
    const { user } = renderWidget();
    await openChat(user);
    // Precondition: opening the chat focused the input.
    expect(input()).toHaveFocus();
    sendStaysStreaming();

    // fireEvent: a click without moving focus, so only the tap can blur it.
    await act(async () => {
      fireEvent.click(chip(STARTERS[0]));
      await tick();
    });

    expect(mockedSend).toHaveBeenCalledTimes(1);
    expect(input()).not.toHaveFocus();
  });
});

// --- 4. A tap resumes scroll following (17.7i) -------------------------------

describe("AssistantWidget starter questions: a tap resumes scroll following (17.7c, 17.7i)", () => {
  const layout = { scrollHeight: 0, clientHeight: 0, scrollTop: 0 };

  function distanceFromEnd() {
    return layout.scrollHeight - layout.clientHeight - layout.scrollTop;
  }

  function stubLayout(list: HTMLElement) {
    layout.scrollHeight = 1000;
    layout.clientHeight = 400;
    layout.scrollTop = 600;
    Object.defineProperty(list, "scrollHeight", { configurable: true, get: () => layout.scrollHeight });
    Object.defineProperty(list, "clientHeight", { configurable: true, get: () => layout.clientHeight });
    Object.defineProperty(list, "scrollTop", {
      configurable: true,
      get: () => layout.scrollTop,
      set: (value: number) => {
        layout.scrollTop = Math.min(Math.max(0, value), layout.scrollHeight - layout.clientHeight);
      },
    });
  }

  it("test_tap_while_scrolled_up_scrolls_to_the_end_and_follows", async () => {
    const { user } = renderWidget();
    await openChat(user);
    const list = screen.getByTestId("assistant-message-list");
    stubLayout(list);
    // At the bottom first, then she scrolls up by hand, 300 px above the end.
    await act(async () => {
      fireEvent.scroll(list);
    });
    layout.scrollTop = 300;
    await act(async () => {
      fireEvent.scroll(list);
    });
    // Precondition: no longer following.
    expect(screen.queryByRole("button", { name: TO_BOTTOM })).toBeInTheDocument();
    sendStaysStreaming();
    layout.scrollHeight += 100;

    await act(async () => {
      fireEvent.click(chip(STARTERS[0]));
      await tick();
    });

    expect(mockedSend).toHaveBeenCalledTimes(1);
    expect(distanceFromEnd()).toBe(0);
    expect(screen.queryByRole("button", { name: TO_BOTTOM })).not.toBeInTheDocument();
  });
});

// --- 5. The textarea ----------------------------------------------------------

describe("AssistantWidget chat textarea attributes (17.7c)", () => {
  it("test_textarea_has_name_message", async () => {
    const { user } = renderWidget();
    await openChat(user);

    expect(input()).toHaveAttribute("name", "message");
  });

  it("test_textarea_has_autocomplete_off", async () => {
    const { user } = renderWidget();
    await openChat(user);

    expect(input()).toHaveAttribute("autocomplete", "off");
  });
});
