// @vitest-environment jsdom
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// docs/DECISIONS.md § "Stage 17 sub-step order", 17.7e: the customer's sent
// text is kept in sessionStorage from send until the turn finishes with
// `done`. After a full reload a stored text shows as her message, followed
// by the "cut off" failure block (17.5b) with "Повторити", unless restored
// history already holds it. Driven through the widget with real
// sessionStorage; only the network (assistantApi) and the router are mocked.
// No storage key is assumed: a reload is a fresh render after an unmount, and
// "stored" means some sessionStorage value equals the text.
const nav = vi.hoisted(() => ({ pathname: "/services", search: "" }));

vi.mock("next/navigation", () => ({
  usePathname: () => nav.pathname,
  useSearchParams: () => new URLSearchParams(nav.search),
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
const DRAFT_KEY = "salonhub:assistant-draft";
const STRIPE = "Відкрити асистентку";
const INPUT = "Повідомлення";
const SEND = "Надіслати";
const RETRY = "Повторити";
const CUT_OFF = "Відповідь обірвалась. Спробуйте ще раз.";
const BUSY = "Зараз у мене багато співрозмовниць. Спробуйте, будь ласка, за хвилинку 🤗";
const TEXT = "Чи є вільний час у суботу?";
const OLD: AssistantHistoryMessage[] = [
  { role: "user", content: "Привіт" },
  { role: "assistant", content: "Вітаю!" },
];

const mockedHistory = vi.mocked(getAssistantHistory);
const mockedSend = vi.mocked(sendAssistantMessage);

function stubMatchMedia() {
  window.matchMedia = vi.fn((query: string) => {
    const min = /min-width:\s*(\d+)px/.exec(query);
    return {
      matches: min !== null && 1280 >= Number(min[1]),
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

beforeEach(() => {
  sessionStorage.clear();
  nav.pathname = "/services";
  nav.search = "";
  stubMatchMedia();
  mockedHistory.mockReset();
  mockedSend.mockReset();
  mockedHistory.mockResolvedValue([]);
});

afterEach(() => {
  vi.restoreAllMocks();
});

function renderWidget() {
  const user = userEvent.setup();
  const result = render(<AssistantWidget salonName="Bella" slug="bella-demo" />);
  return { user, ...result };
}

async function openChat(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole("button", { name: STRIPE }));
  return screen.getByRole("dialog");
}

function input() {
  return screen.getByRole("textbox", { name: INPUT });
}

function conversation() {
  return screen.getByRole("list", { name: "Розмова" });
}

/** The text of each conversation item, in order (bubbles and failure block). */
function items() {
  return within(conversation())
    .getAllByRole("listitem")
    .map((item) => item.textContent);
}

/** Every value in sessionStorage, whatever its key. */
function storedValues() {
  const values: string[] = [];
  for (let index = 0; index < sessionStorage.length; index++) {
    const key = sessionStorage.key(index);
    if (key !== null) {
      values.push(sessionStorage.getItem(key) ?? "");
    }
  }
  return values;
}

function tick() {
  return new Promise((resolve) => setTimeout(resolve, 0));
}

async function* streamOf(events: AssistantEvent[]): AsyncGenerator<AssistantEvent> {
  for (const event of events) {
    yield event;
  }
}

/** Streams the events, then never ends: a reply still running at the reload. */
async function* hangingStreamOf(events: AssistantEvent[]): AsyncGenerator<AssistantEvent> {
  yield* streamOf(events);
  await new Promise<never>(() => {});
}

/**
 * Streams `before`, waits until `release()`, then streams `after`: lets a
 * test see storage mid-turn before the same turn reaches `done`.
 */
function replyGated(before: AssistantEvent[], after: AssistantEvent[]) {
  let release = () => {};
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  async function* events(): AsyncGenerator<AssistantEvent> {
    yield* streamOf(before);
    await gate;
    yield* streamOf(after);
  }
  mockedSend.mockResolvedValueOnce({ sessionId: "server-id", events: events() });
  return async () => {
    await act(async () => release());
  };
}

function replyWith(events: AssistantEvent[], sessionId = "server-id") {
  mockedSend.mockResolvedValueOnce({ sessionId, events: streamOf(events) });
}

function replyHanging(events: AssistantEvent[]) {
  mockedSend.mockResolvedValueOnce({ sessionId: "server-id", events: hangingStreamOf(events) });
}

async function sendText(user: ReturnType<typeof userEvent.setup>, text = TEXT) {
  await user.type(input(), text);
  await user.click(screen.getByRole("button", { name: SEND }));
}

/** First page load: opens the chat, sends TEXT and leaves the reply running. */
async function sendAndLeaveRunning() {
  replyHanging([{ type: "text", text: "Зараз перевірю" }]);
  const first = renderWidget();
  await openChat(first.user);
  await sendText(first.user);
  await screen.findByText("Зараз перевірю");
  return first;
}

/** A full reload: the old tree goes, a new one mounts with the same storage. */
async function reload(first: ReturnType<typeof renderWidget>) {
  first.unmount();
  const second = renderWidget();
  // 17.7d: the chat comes back open after a full reload.
  await screen.findByRole("dialog");
  return second;
}

describe("AssistantWidget keeps the sent text until done (17.7e)", () => {
  it("test_sent_text_is_in_storage_while_the_reply_runs", async () => {
    await sendAndLeaveRunning();

    expect(storedValues()).toContain(TEXT);
  });

  it("test_sent_text_is_in_storage_before_any_response_arrives", async () => {
    mockedSend.mockReturnValueOnce(new Promise(() => {}));
    const { user } = renderWidget();
    await openChat(user);

    await sendText(user);

    expect(storedValues()).toContain(TEXT);
  });

  it("test_stored_text_is_the_trimmed_text_that_was_sent", async () => {
    replyHanging([]);
    const { user } = renderWidget();
    await openChat(user);

    await sendText(user, `  ${TEXT}  `);

    await waitFor(() => expect(mockedSend).toHaveBeenCalled());
    expect(mockedSend.mock.calls[0][1]).toBe(TEXT);
    expect(storedValues()).toContain(TEXT);
  });

  it("test_sent_text_is_removed_from_storage_after_done", async () => {
    const finish = replyGated([{ type: "text", text: "Так, є." }], [{ type: "done" }]);
    const { user } = renderWidget();
    await openChat(user);
    await sendText(user);
    await screen.findByText("Так, є.");
    // Precondition: the running turn's text is in storage.
    expect(storedValues()).toContain(TEXT);

    await finish();
    // The session id is stored at `done`, so this waits for the end of the turn.
    await waitFor(() => expect(sessionStorage.getItem(SESSION_KEY)).toBe("server-id"));

    expect(storedValues()).not.toContain(TEXT);
  });

  it.each([
    {
      name: "refused_before_the_stream",
      arrange: () =>
        mockedSend.mockRejectedValueOnce(
          new ApiError(503, "assistant_busy", "The assistant is busy."),
        ),
      failureText: BUSY,
    },
    {
      name: "error_event",
      arrange: () =>
        replyWith([
          { type: "text", text: "Ось що я знайш" },
          { type: "error", code: "assistant_provider_error" },
        ]),
      failureText: CUT_OFF,
    },
    {
      name: "lost_connection",
      arrange: () => mockedSend.mockRejectedValueOnce(new TypeError("Failed to fetch")),
      failureText: "Схоже, зник зв'язок. Перевірте інтернет і спробуйте ще раз.",
    },
    {
      name: "stream_ended_without_done",
      arrange: () => replyWith([{ type: "text", text: "Ось що я знайш" }]),
      failureText: CUT_OFF,
    },
  ])("test_sent_text_stays_in_storage_after_a_failure_($name)", async ({ arrange, failureText }) => {
    arrange();
    const { user } = renderWidget();
    await openChat(user);

    await sendText(user);
    await screen.findByText(failureText);

    expect(storedValues()).toContain(TEXT);
  });

  it("test_sent_text_is_removed_after_a_retry_finishes_with_done", async () => {
    replyWith([
      { type: "text", text: "Ось що я знайш" },
      { type: "error", code: "assistant_provider_error" },
    ]);
    replyWith([{ type: "text", text: "У суботу є 10:00." }, { type: "done" }]);
    const { user } = renderWidget();
    await openChat(user);
    await sendText(user);
    await screen.findByRole("button", { name: RETRY });
    // Precondition: the failed turn left the text in storage.
    expect(storedValues()).toContain(TEXT);

    await user.click(screen.getByRole("button", { name: RETRY }));
    await screen.findByText("У суботу є 10:00.");
    await waitFor(() => expect(sessionStorage.getItem(SESSION_KEY)).toBe("server-id"));

    expect(storedValues()).not.toContain(TEXT);
  });

  it("test_later_send_replaces_the_stored_text_of_a_failed_turn", async () => {
    mockedSend.mockRejectedValueOnce(new ApiError(503, "assistant_busy", "busy"));
    replyHanging([]);
    const { user } = renderWidget();
    await openChat(user);
    await sendText(user);
    await screen.findByText(BUSY);

    await sendText(user, "А в неділю?");

    await waitFor(() => expect(mockedSend).toHaveBeenCalledTimes(2));
    expect(storedValues()).toContain("А в неділю?");
    expect(storedValues()).not.toContain(TEXT);
  });
});

describe("AssistantWidget shows the pending turn after a reload (17.7e)", () => {
  it("test_reload_mid_reply_shows_the_text_and_the_cut_off_block", async () => {
    const first = await sendAndLeaveRunning();

    await reload(first);

    expect(await screen.findByText(TEXT)).toBeInTheDocument();
    expect(screen.getByText(CUT_OFF)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: RETRY })).toBeInTheDocument();
    // Her message, then the failure block, as the list's last item.
    expect(items()).toEqual([TEXT, `${CUT_OFF}${RETRY}`]);
  });

  it("test_reload_mid_reply_shows_the_text_as_her_bubble_not_the_partial_reply", async () => {
    const first = await sendAndLeaveRunning();

    await reload(first);

    await screen.findByText(TEXT);
    expect(screen.queryByText("Зараз перевірю")).not.toBeInTheDocument();
    // The customer bubble is aligned to the end, as every customer message.
    expect(screen.getByText(TEXT).closest("li")).toHaveClass("self-end");
  });

  it.each([
    {
      name: "refused_before_the_stream",
      arrange: () =>
        mockedSend.mockRejectedValueOnce(
          new ApiError(503, "assistant_busy", "The assistant is busy."),
        ),
      failureText: BUSY,
    },
    {
      name: "error_event",
      arrange: () =>
        replyWith([
          { type: "text", text: "Ось що я знайш" },
          { type: "error", code: "assistant_provider_error" },
        ]),
      failureText: CUT_OFF,
    },
  ])(
    "test_reload_after_a_failure_shows_the_text_and_the_cut_off_block_($name)",
    async ({ arrange, failureText }) => {
      arrange();
      const first = renderWidget();
      await openChat(first.user);
      await sendText(first.user);
      await screen.findByText(failureText);

      await reload(first);

      expect(await screen.findByText(TEXT)).toBeInTheDocument();
      // 17.7e: after a reload the block is always the "cut off" row.
      expect(items()).toEqual([TEXT, `${CUT_OFF}${RETRY}`]);
    },
  );

  it("test_reload_shows_the_pending_text_after_restored_history", async () => {
    sessionStorage.setItem(SESSION_KEY, "stored-id");
    mockedHistory.mockResolvedValue(OLD);
    const first = renderWidget();
    await screen.findByTestId("assistant-live-dot");
    await openChat(first.user);
    replyHanging([{ type: "text", text: "Зараз перевірю" }]);
    await sendText(first.user);
    await screen.findByText("Зараз перевірю");

    await reload(first);

    await screen.findByText("Вітаю!");
    await waitFor(() => expect(items()).toEqual(["Привіт", "Вітаю!", TEXT, `${CUT_OFF}${RETRY}`]));
  });

  it("test_pending_text_survives_a_second_reload", async () => {
    const first = await sendAndLeaveRunning();
    const second = await reload(first);
    await screen.findByText(TEXT);

    await reload(second);

    expect(await screen.findByText(TEXT)).toBeInTheDocument();
    expect(items()).toEqual([TEXT, `${CUT_OFF}${RETRY}`]);
  });

  it("test_reload_shows_no_pending_turn_after_done", async () => {
    const finish = replyGated([{ type: "text", text: "Так, є." }], [{ type: "done" }]);
    const first = renderWidget();
    await openChat(first.user);
    await sendText(first.user);
    await screen.findByText("Так, є.");
    // Precondition: the running turn's text is in storage.
    expect(storedValues()).toContain(TEXT);
    await finish();
    await waitFor(() => expect(sessionStorage.getItem(SESSION_KEY)).toBe("server-id"));
    mockedHistory.mockResolvedValue([
      { role: "user", content: TEXT },
      { role: "assistant", content: "Так, є." },
    ]);

    await reload(first);

    await screen.findByText("Так, є.");
    expect(screen.getAllByText(TEXT)).toHaveLength(1);
    expect(screen.queryByRole("button", { name: RETRY })).not.toBeInTheDocument();
  });

  // The draft (17.7b) is separate: the pending text never fills the input.
  it("test_pending_text_does_not_come_back_as_the_draft", async () => {
    const first = await sendAndLeaveRunning();

    await reload(first);

    await screen.findByText(TEXT);
    expect(input()).toHaveValue("");
    expect(sessionStorage.getItem(DRAFT_KEY)).toBeNull();
  });
});

// 17.7e matching rule: the server saves the customer message and the reply
// together as one turn, just before `done` (ai_assistant/orchestrator.py), so
// history never ends with a bare customer message. The stored text is dropped
// when the customer message of the last turn equals it; only the last turn is
// compared.
describe("AssistantWidget drops a pending turn the server already stored (17.7e)", () => {
  it.each([
    {
      name: "history_ends_with_the_turn_of_that_message",
      stored: [
        ...OLD,
        { role: "user" as const, content: TEXT },
        { role: "assistant" as const, content: "У суботу є 10:00." },
      ],
      expected: ["Привіт", "Вітаю!", TEXT, "У суботу є 10:00."],
    },
  ])("test_text_is_shown_once_with_no_failure_block_($name)", async ({ stored, expected }) => {
    sessionStorage.setItem(SESSION_KEY, "stored-id");
    mockedHistory.mockResolvedValue(OLD);
    const first = renderWidget();
    await screen.findByTestId("assistant-live-dot");
    await openChat(first.user);
    replyHanging([{ type: "text", text: "Зараз перевірю" }]);
    await sendText(first.user);
    await screen.findByText("Зараз перевірю");
    // Precondition: the running turn's text is in storage, so the reload
    // really has a pending turn to drop.
    expect(storedValues()).toContain(TEXT);
    mockedHistory.mockResolvedValue(stored);

    await reload(first);

    await screen.findByText("Вітаю!");
    await tick();
    expect(items()).toEqual(expected);
    expect(screen.queryByRole("button", { name: RETRY })).not.toBeInTheDocument();
    expect(screen.queryByText(CUT_OFF)).not.toBeInTheDocument();
  });

  it("test_dropped_text_is_cleared_from_storage", async () => {
    sessionStorage.setItem(SESSION_KEY, "stored-id");
    mockedHistory.mockResolvedValue(OLD);
    const first = renderWidget();
    await screen.findByTestId("assistant-live-dot");
    await openChat(first.user);
    replyHanging([{ type: "text", text: "Зараз перевірю" }]);
    await sendText(first.user);
    await screen.findByText("Зараз перевірю");
    // Precondition: the running turn's text is in storage.
    expect(storedValues()).toContain(TEXT);
    mockedHistory.mockResolvedValue([
      ...OLD,
      { role: "user", content: TEXT },
      { role: "assistant", content: "У суботу є 10:00." },
    ]);

    await reload(first);
    await screen.findByText("У суботу є 10:00.");

    await waitFor(() => expect(storedValues()).not.toContain(TEXT));
  });

  it("test_text_matching_an_earlier_message_only_is_still_shown", async () => {
    sessionStorage.setItem(SESSION_KEY, "stored-id");
    const asked = [
      { role: "user" as const, content: TEXT },
      { role: "assistant" as const, content: "Так, є." },
      ...OLD,
    ];
    mockedHistory.mockResolvedValue(asked);
    const first = renderWidget();
    await screen.findByTestId("assistant-live-dot");
    await openChat(first.user);
    replyHanging([{ type: "text", text: "Зараз перевірю" }]);
    await sendText(first.user);
    await screen.findByText("Зараз перевірю");

    await reload(first);

    await screen.findByText("Вітаю!");
    await waitFor(() =>
      expect(items()).toEqual([TEXT, "Так, є.", "Привіт", "Вітаю!", TEXT, `${CUT_OFF}${RETRY}`]),
    );
  });
});

/**
 * First page load with a live conversation: history OLD, then TEXT is sent
 * and its reply is left running.
 */
async function sendInConversationAndLeaveRunning() {
  sessionStorage.setItem(SESSION_KEY, "stored-id");
  mockedHistory.mockResolvedValue(OLD);
  const first = renderWidget();
  await screen.findByTestId("assistant-live-dot");
  await openChat(first.user);
  replyHanging([{ type: "text", text: "Зараз перевірю" }]);
  await sendText(first.user);
  await screen.findByText("Зараз перевірю");
  return first;
}

/**
 * Waits until the reloaded page's history request has been answered. After
 * sendInConversationAndLeaveRunning and one reload that is the third call:
 * the first mount, the 17.7a re-check on opening the chat, the reload.
 */
async function historyAnswered(calls: number) {
  await waitFor(() => expect(mockedHistory).toHaveBeenCalledTimes(calls));
  await tick();
}

// 17.7e (decided 07.11.2026): a failed history request on reload neither
// shows nor removes the stored text; the next successful history load
// decides by the matching rule.
describe("AssistantWidget pending turn when the history request fails (17.7e)", () => {
  it.each([
    { name: "lost_connection", error: new TypeError("Failed to fetch") },
    { name: "server_error", error: new ApiError(500, "internal_error", "Server error.") },
  ])(
    "test_failed_history_on_reload_shows_no_stored_text_($name)",
    async ({ error }) => {
      const first = await sendInConversationAndLeaveRunning();
      // Precondition: the running turn's text is in storage.
      expect(storedValues()).toContain(TEXT);
      mockedHistory.mockRejectedValueOnce(error);

      await reload(first);
      await historyAnswered(3);

      expect(screen.queryByText(TEXT)).not.toBeInTheDocument();
      expect(screen.queryByText(CUT_OFF)).not.toBeInTheDocument();
      // 17.7h: the history failure block has its own "Повторити"; the stored
      // text is still not shown as her message.
      expect(screen.queryByRole("list", { name: "Розмова" })).not.toBeInTheDocument();
    },
  );

  it("test_failed_history_on_reload_keeps_the_stored_text", async () => {
    const first = await sendInConversationAndLeaveRunning();
    mockedHistory.mockRejectedValueOnce(new TypeError("Failed to fetch"));

    await reload(first);
    await historyAnswered(3);

    expect(storedValues()).toContain(TEXT);
  });

  it("test_next_successful_history_shows_the_stored_text_not_in_the_last_turn", async () => {
    const first = await sendInConversationAndLeaveRunning();
    mockedHistory.mockRejectedValueOnce(new TypeError("Failed to fetch"));
    const second = await reload(first);
    await historyAnswered(3);

    await reload(second);

    await screen.findByText("Вітаю!");
    await waitFor(() => expect(items()).toEqual(["Привіт", "Вітаю!", TEXT, `${CUT_OFF}${RETRY}`]));
  });

  it("test_next_successful_history_drops_the_stored_text_in_the_last_turn", async () => {
    const first = await sendInConversationAndLeaveRunning();
    mockedHistory.mockRejectedValueOnce(new TypeError("Failed to fetch"));
    const second = await reload(first);
    await historyAnswered(3);
    // Precondition: the failed load left the text in storage.
    expect(storedValues()).toContain(TEXT);
    mockedHistory.mockResolvedValue([
      ...OLD,
      { role: "user", content: TEXT },
      { role: "assistant", content: "У суботу є 10:00." },
    ]);

    await reload(second);

    await screen.findByText("У суботу є 10:00.");
    await tick();
    expect(items()).toEqual(["Привіт", "Вітаю!", TEXT, "У суботу є 10:00."]);
    expect(screen.queryByRole("button", { name: RETRY })).not.toBeInTheDocument();
    await waitFor(() => expect(storedValues()).not.toContain(TEXT));
  });
});

// 17.7e (decided 07.11.2026): when the conversation has expired (empty
// history), the stored text goes into the input only if the input is empty;
// otherwise it is dropped and the draft is untouched. Either way it leaves
// storage, and no failure block or "Повторити" shows. Text in the input is
// the 17.7b draft, so it is stored under the draft key.
describe("AssistantWidget pending turn when the conversation expired (17.7e)", () => {
  /** The storage keys whose value equals the text. */
  function keysHolding(text: string) {
    const keys: string[] = [];
    for (let index = 0; index < sessionStorage.length; index++) {
      const key = sessionStorage.key(index);
      if (key !== null && sessionStorage.getItem(key) === text) {
        keys.push(key);
      }
    }
    return keys;
  }

  it("test_expired_conversation_moves_the_stored_text_into_an_empty_input", async () => {
    const first = await sendInConversationAndLeaveRunning();
    mockedHistory.mockResolvedValueOnce([]);

    await reload(first);
    await historyAnswered(3);

    await waitFor(() => expect(input()).toHaveValue(TEXT));
    // Only the draft holds it now; the pending entry is gone.
    expect(keysHolding(TEXT)).toEqual([DRAFT_KEY]);
    expect(screen.queryByText(CUT_OFF)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: RETRY })).not.toBeInTheDocument();
  });

  it("test_expired_conversation_keeps_the_draft_and_drops_the_stored_text", async () => {
    const first = await sendInConversationAndLeaveRunning();
    await first.user.type(input(), "А в неділю?");
    // Precondition: both the draft and the running turn's text are stored.
    expect(sessionStorage.getItem(DRAFT_KEY)).toBe("А в неділю?");
    expect(storedValues()).toContain(TEXT);
    mockedHistory.mockResolvedValueOnce([]);

    await reload(first);
    await historyAnswered(3);

    expect(input()).toHaveValue("А в неділю?");
    expect(sessionStorage.getItem(DRAFT_KEY)).toBe("А в неділю?");
    expect(storedValues()).not.toContain(TEXT);
    expect(screen.queryByText(TEXT)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: RETRY })).not.toBeInTheDocument();
  });
});

describe("AssistantWidget retries the pending turn after a reload (17.7e)", () => {
  it("test_retry_resends_the_stored_text", async () => {
    const first = await sendAndLeaveRunning();
    const { user } = await reload(first);
    await screen.findByRole("button", { name: RETRY });
    replyWith([{ type: "text", text: "У суботу є 10:00." }, { type: "done" }]);

    await user.click(screen.getByRole("button", { name: RETRY }));

    await screen.findByText("У суботу є 10:00.");
    expect(mockedSend).toHaveBeenCalledTimes(2);
    expect(mockedSend.mock.calls[1][0]).toBe("bella-demo");
    expect(mockedSend.mock.calls[1][1]).toBe(TEXT);
  });

  it("test_retry_shows_no_second_customer_bubble", async () => {
    const first = await sendAndLeaveRunning();
    const { user } = await reload(first);
    await screen.findByRole("button", { name: RETRY });
    replyWith([{ type: "text", text: "У суботу є 10:00." }, { type: "done" }]);

    await user.click(screen.getByRole("button", { name: RETRY }));

    await screen.findByText("У суботу є 10:00.");
    expect(items()).toEqual([TEXT, "У суботу є 10:00."]);
  });

  it("test_retry_sends_the_stored_session_id", async () => {
    sessionStorage.setItem(SESSION_KEY, "stored-id");
    mockedHistory.mockResolvedValue(OLD);
    const first = renderWidget();
    await screen.findByTestId("assistant-live-dot");
    await openChat(first.user);
    replyHanging([{ type: "text", text: "Зараз перевірю" }]);
    await sendText(first.user);
    await screen.findByText("Зараз перевірю");
    const { user } = await reload(first);
    await screen.findByRole("button", { name: RETRY });
    replyWith([{ type: "text", text: "У суботу є 10:00." }, { type: "done" }], "stored-id");

    await user.click(screen.getByRole("button", { name: RETRY }));

    await screen.findByText("У суботу є 10:00.");
    expect(mockedSend.mock.calls[1][1]).toBe(TEXT);
    expect(mockedSend.mock.calls[1][2]).toBe("stored-id");
  });

  it("test_successful_retry_clears_the_stored_text", async () => {
    const first = await sendAndLeaveRunning();
    const second = await reload(first);
    await screen.findByRole("button", { name: RETRY });
    replyWith([{ type: "text", text: "У суботу є 10:00." }, { type: "done" }]);

    await second.user.click(screen.getByRole("button", { name: RETRY }));
    await screen.findByText("У суботу є 10:00.");
    await waitFor(() => expect(sessionStorage.getItem(SESSION_KEY)).toBe("server-id"));

    expect(storedValues()).not.toContain(TEXT);
  });

  it("test_retry_leaves_the_draft_alone", async () => {
    const first = await sendAndLeaveRunning();
    const { user } = await reload(first);
    await screen.findByRole("button", { name: RETRY });
    await user.type(input(), "А в неділю?");
    replyWith([{ type: "text", text: "У суботу є 10:00." }, { type: "done" }]);

    await user.click(screen.getByRole("button", { name: RETRY }));

    await screen.findByText("У суботу є 10:00.");
    expect(input()).toHaveValue("А в неділю?");
    expect(mockedSend.mock.calls[1][1]).toBe(TEXT);
  });
});

describe("AssistantWidget pending turn with failing storage (17.7e)", () => {
  function storageThrows(method: "getItem" | "setItem" | "removeItem") {
    vi.spyOn(Storage.prototype, method).mockImplementation(() => {
      throw new DOMException("access denied", "SecurityError");
    });
  }

  it("test_send_and_reply_work_when_storage_writes_throw", async () => {
    replyWith([{ type: "text", text: "Так, є." }, { type: "done" }]);
    const { user } = renderWidget();
    await openChat(user);
    storageThrows("setItem");

    await sendText(user);

    expect(await screen.findByText("Так, є.")).toBeInTheDocument();
    expect(screen.getByText(TEXT)).toBeInTheDocument();
  });

  it("test_done_works_when_storage_removal_throws", async () => {
    const finish = replyGated([{ type: "text", text: "Так, є." }], [{ type: "done" }]);
    const { user } = renderWidget();
    await openChat(user);
    await sendText(user);
    await screen.findByText("Так, є.");
    // Precondition: there is a stored text for `done` to remove.
    expect(storedValues()).toContain(TEXT);
    storageThrows("removeItem");

    await finish();

    await waitFor(() => expect(sessionStorage.getItem(SESSION_KEY)).toBe("server-id"));
    expect(screen.getByText("Так, є.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: RETRY })).not.toBeInTheDocument();
    expect(input()).toBeEnabled();
  });

  it("test_failed_turn_retry_works_when_storage_writes_throw", async () => {
    replyWith([
      { type: "text", text: "Ось що я знайш" },
      { type: "error", code: "assistant_provider_error" },
    ]);
    replyWith([{ type: "text", text: "У суботу є 10:00." }, { type: "done" }]);
    const { user } = renderWidget();
    await openChat(user);
    storageThrows("setItem");
    await sendText(user);
    await screen.findByRole("button", { name: RETRY });

    await user.click(screen.getByRole("button", { name: RETRY }));

    expect(await screen.findByText("У суботу є 10:00.")).toBeInTheDocument();
    expect(mockedSend.mock.calls[1][1]).toBe(TEXT);
  });

  it("test_widget_renders_when_storage_reads_throw_on_reload", async () => {
    const first = await sendAndLeaveRunning();
    // Precondition: a pending text is stored, so the failing read is of it.
    expect(storedValues()).toContain(TEXT);
    first.unmount();
    storageThrows("getItem");

    const { user } = renderWidget();
    // Nothing can be read, so the widget starts collapsed with no pending turn.
    await openChat(user);

    expect(screen.queryByText(TEXT)).not.toBeInTheDocument();
    expect(input()).toBeEnabled();
  });

  it("test_pending_turn_shows_on_reload_when_storage_removal_throws", async () => {
    const first = await sendAndLeaveRunning();
    storageThrows("removeItem");

    await reload(first);

    expect(await screen.findByText(TEXT)).toBeInTheDocument();
    expect(items()).toEqual([TEXT, `${CUT_OFF}${RETRY}`]);
  });

  it("test_dropping_a_stored_turn_works_when_storage_removal_throws", async () => {
    sessionStorage.setItem(SESSION_KEY, "stored-id");
    mockedHistory.mockResolvedValue(OLD);
    const first = renderWidget();
    await screen.findByTestId("assistant-live-dot");
    await openChat(first.user);
    replyHanging([{ type: "text", text: "Зараз перевірю" }]);
    await sendText(first.user);
    await screen.findByText("Зараз перевірю");
    // Precondition: the running turn's text is in storage.
    expect(storedValues()).toContain(TEXT);
    mockedHistory.mockResolvedValue([
      ...OLD,
      { role: "user", content: TEXT },
      { role: "assistant", content: "У суботу є 10:00." },
    ]);
    storageThrows("removeItem");

    await reload(first);

    await screen.findByText("У суботу є 10:00.");
    await tick();
    expect(items()).toEqual(["Привіт", "Вітаю!", TEXT, "У суботу є 10:00."]);
    expect(input()).toBeEnabled();
  });
});
