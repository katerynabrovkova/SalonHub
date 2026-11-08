// @vitest-environment jsdom
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { AssistantEvent } from "@/lib/assistant/assistantApi";

// Mock the engine at its module boundaries (17.4a): no fetch, no storage.
vi.mock("@/lib/assistant/assistantApi", () => ({
  sendAssistantMessage: vi.fn(),
  getAssistantHistory: vi.fn(),
}));

vi.mock("@/lib/assistant/sessionId", () => ({
  readSessionId: vi.fn(),
  storeSessionId: vi.fn(),
  clearSessionId: vi.fn(),
  markConversationExpired: vi.fn(),
  readConversationExpired: vi.fn(),
  clearConversationExpired: vi.fn(),
}));

// Imported after the mocks above so the mocked modules are what the chat sees.
import { ApiError } from "@/lib/api/errors";
import { getAssistantHistory, sendAssistantMessage } from "@/lib/assistant/assistantApi";
import { clearSessionId, readSessionId, storeSessionId } from "@/lib/assistant/sessionId";

import { formatPrice } from "@/lib/pricing/formatPrice";

import AssistantChat, { useAssistantConversation } from "./AssistantChat";

// docs/DECISIONS.md § "Stage 17 sub-step order", 17.4b.
const GREETING =
  "Вітаю! Я допоможу дізнатися ціни, обрати послугу і знайти вільний час у нашому салоні. Що Вас цікавить?";
const EXPIRED = "Попередня розмова завершилась";
const NEUTRAL_ERROR = "Не вдалося отримати відповідь. Спробуйте ще раз.";
const INPUT = "Повідомлення";
const SEND = "Надіслати";

const mockedSend = vi.mocked(sendAssistantMessage);
const mockedHistory = vi.mocked(getAssistantHistory);
const mockedRead = vi.mocked(readSessionId);
const mockedStore = vi.mocked(storeSessionId);
const mockedClear = vi.mocked(clearSessionId);

/** An event stream the test pushes into, one event at a time. */
function eventChannel() {
  const buffer: AssistantEvent[] = [];
  let ended = false;
  let wake: (() => void) | null = null;

  function notify() {
    wake?.();
    wake = null;
  }

  async function* events(): AsyncGenerator<AssistantEvent> {
    while (true) {
      if (buffer.length > 0) {
        yield buffer.shift() as AssistantEvent;
        continue;
      }
      if (ended) {
        return;
      }
      await new Promise<void>((resolve) => {
        wake = resolve;
      });
    }
  }

  return {
    events: events(),
    push(...items: Array<AssistantEvent | { type: string }>) {
      buffer.push(...(items as AssistantEvent[]));
      notify();
    },
    end() {
      ended = true;
      notify();
    },
  };
}

function replyWith(sessionId: string | null = "server-id") {
  const channel = eventChannel();
  mockedSend.mockResolvedValueOnce({ sessionId, events: channel.events });
  return channel;
}

// The conversation lives in a hook so the widget can keep it while the chat
// is minimised or the page is excluded; the tests drive both together.
function Chat({ slug = "bella-demo", open = false }: { slug?: string; open?: boolean }) {
  const conversation = useAssistantConversation(slug, open);
  return <AssistantChat conversation={conversation} />;
}

function renderChat() {
  const user = userEvent.setup();
  return { user, ...render(<Chat />) };
}

async function send(user: ReturnType<typeof userEvent.setup>, text: string) {
  await user.type(screen.getByRole("textbox", { name: INPUT }), text);
  await user.click(screen.getByRole("button", { name: SEND }));
}

beforeEach(() => {
  sessionStorage.clear();
  mockedSend.mockReset();
  mockedHistory.mockReset();
  mockedRead.mockReset();
  mockedStore.mockReset();
  mockedClear.mockReset();
  mockedRead.mockReturnValue(null);
  mockedHistory.mockResolvedValue([]);
});

describe("AssistantChat sending", () => {
  it("test_shows_the_greeting_before_the_first_message", () => {
    renderChat();

    expect(screen.getByText(GREETING)).toBeInTheDocument();
    expect(screen.queryAllByRole("listitem")).toHaveLength(0);
  });

  it("test_sending_shows_the_customer_bubble", async () => {
    replyWith();
    const { user } = renderChat();

    await send(user, "Скільки коштує манікюр?");

    expect(screen.getByText("Скільки коштує манікюр?")).toBeInTheDocument();
    expect(mockedSend).toHaveBeenCalledWith(
      "bella-demo",
      "Скільки коштує манікюр?",
      null,
      expect.any(AbortSignal),
    );
  });

  it("test_sending_passes_the_stored_session_id", async () => {
    mockedRead.mockReturnValue("stored-id");
    mockedHistory.mockResolvedValueOnce([
      { role: "user", content: "Привіт" },
      { role: "assistant", content: "Вітаю!" },
    ]);
    replyWith("stored-id");
    const { user } = renderChat();
    await screen.findByText("Вітаю!");

    await send(user, "Ще питання");

    expect(mockedSend).toHaveBeenCalledWith(
      "bella-demo",
      "Ще питання",
      "stored-id",
      expect.any(AbortSignal),
    );
  });

  it("test_text_deltas_grow_one_assistant_bubble", async () => {
    const channel = replyWith();
    const { user } = renderChat();
    await send(user, "Привіт");

    channel.push({ type: "text", text: "Вітаю" });
    await screen.findByText("Вітаю");
    channel.push({ type: "text", text: ", чим допомогти?" });

    await screen.findByText("Вітаю, чим допомогти?");
    expect(screen.getAllByRole("listitem")).toHaveLength(2);
  });

  // 17.7c, item of 06.11.2026: the input stays enabled; sending is blocked.
  it("test_input_stays_enabled_while_streaming_and_sending_is_blocked_until_done", async () => {
    const channel = replyWith();
    const { user } = renderChat();
    await send(user, "Привіт");

    expect(screen.getByRole("textbox", { name: INPUT })).toBeEnabled();
    expect(screen.getByRole("button", { name: SEND })).toBeDisabled();

    channel.push({ type: "text", text: "Вітаю" }, { type: "done" });
    channel.end();

    await user.type(screen.getByRole("textbox", { name: INPUT }), "Ще");
    await waitFor(() => expect(screen.getByRole("button", { name: SEND })).toBeEnabled());
  });

  it("test_session_id_is_stored_after_done", async () => {
    const channel = replyWith("server-id");
    const { user } = renderChat();
    await send(user, "Привіт");

    channel.push({ type: "text", text: "Вітаю" });
    await screen.findByText("Вітаю");
    expect(mockedStore).not.toHaveBeenCalled();

    channel.push({ type: "done" });
    channel.end();

    await waitFor(() => expect(mockedStore).toHaveBeenCalledWith("server-id"));
    expect(screen.queryByText(NEUTRAL_ERROR)).not.toBeInTheDocument();
  });

  it.each([
    {
      name: "error_event",
      // 17.5b table: text had begun, so the reply was cut off.
      text: "Відповідь обірвалась. Спробуйте ще раз.",
      arrange: () => {
        const channel = replyWith();
        return () => {
          channel.push(
            { type: "text", text: "Вітаю" },
            { type: "error", code: "assistant_provider_error" },
          );
          channel.end();
        };
      },
    },
    {
      name: "api_error_before_stream",
      text: "Зараз у мене багато співрозмовниць. Спробуйте, будь ласка, за хвилинку 🤗",
      arrange: () => {
        mockedSend.mockRejectedValueOnce(
          new ApiError(503, "assistant_busy", "The assistant is busy."),
        );
        return () => undefined;
      },
    },
    {
      name: "stream_ends_without_done",
      text: "Відповідь обірвалась. Спробуйте ще раз.",
      arrange: () => {
        const channel = replyWith();
        return () => {
          channel.push({ type: "text", text: "Вітаю" });
          channel.end();
        };
      },
    },
  ])(
    "test_failed_reply_stores_no_id_shows_its_table_text_and_enables_input_($name)",
    async ({ arrange, text }) => {
      const finish = arrange();
      const { user } = renderChat();
      await send(user, "Привіт");

      finish();

      expect(await screen.findByText(text)).toBeInTheDocument();
      expect(screen.getByRole("textbox", { name: INPUT })).toBeEnabled();
      expect(mockedStore).not.toHaveBeenCalled();
    },
  );

  // Valid proposals are covered by "AssistantChat proposal card" (17.6a).
  it("test_status_and_unknown_events_do_not_change_the_reply_text", async () => {
    const channel = replyWith("server-id");
    const { user } = renderChat();
    await send(user, "Привіт");

    channel.push(
      { type: "text", text: "Ось " },
      { type: "status", step: "check_availability" },
      { type: "mystery" },
      { type: "text", text: "варіанти" },
      { type: "done" },
    );
    channel.end();

    expect(await screen.findByText("Ось варіанти")).toBeInTheDocument();
    await waitFor(() => expect(mockedStore).toHaveBeenCalledWith("server-id"));
    expect(screen.queryByText(NEUTRAL_ERROR)).not.toBeInTheDocument();
  });
});

describe("AssistantChat history", () => {
  it("test_stored_id_restores_history_once_per_mount", async () => {
    mockedRead.mockReturnValue("stored-id");
    mockedHistory.mockResolvedValueOnce([
      { role: "user", content: "Привіт" },
      { role: "assistant", content: "Вітаю!" },
    ]);
    const { rerender } = renderChat();

    expect(await screen.findByText("Вітаю!")).toBeInTheDocument();
    expect(screen.getByText("Привіт")).toBeInTheDocument();

    rerender(<Chat />);
    rerender(<Chat />);

    expect(mockedHistory).toHaveBeenCalledTimes(1);
    expect(mockedHistory).toHaveBeenCalledWith("bella-demo", "stored-id", expect.any(AbortSignal));
  });

  // 17.4b (empty messages skipped) and 17.6a (a malformed stored card is
  // dropped; its message's text still shows). Valid cards: see "AssistantChat
  // proposal card".
  it("test_history_skips_empty_messages_and_drops_a_malformed_card_keeping_its_text", async () => {
    const malformedCard = { service: { id: 7 }, slots: "soon" };
    mockedRead.mockReturnValue("stored-id");
    mockedHistory.mockResolvedValueOnce([
      { role: "user", content: "Манікюр на суботу?" },
      { role: "assistant", content: "Ось варіант", card: malformedCard },
      { role: "assistant", content: "" },
      { role: "assistant", content: "", card: malformedCard },
    ]);
    renderChat();

    expect(await screen.findByText("Ось варіант")).toBeInTheDocument();
    expect(screen.getAllByRole("listitem")).toHaveLength(2);
    expect(screen.queryByRole("link", { name: "Обрати час" })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Інший день або час" })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /Записатися на/ })).not.toBeInTheDocument();
  });

  it("test_stored_id_with_empty_history_shows_the_expired_notice_and_clears_the_id", async () => {
    mockedRead.mockReturnValue("stored-id");
    mockedHistory.mockResolvedValueOnce([]);
    render(<Chat open />);

    const notice = await screen.findByText(EXPIRED);
    const greeting = screen.getByText(GREETING);
    expect(notice.compareDocumentPosition(greeting) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(mockedClear).toHaveBeenCalledTimes(1);
  });

  it("test_failed_history_keeps_the_greeting_and_the_id", async () => {
    mockedRead.mockReturnValue("stored-id");
    mockedHistory.mockRejectedValueOnce(new ApiError(500, "internal_error", "Boom"));
    renderChat();

    await waitFor(() => expect(mockedHistory).toHaveBeenCalledTimes(1));
    expect(screen.getByText(GREETING)).toBeInTheDocument();
    // Let the rejection settle before checking nothing reacted to it.
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(screen.queryByText(EXPIRED)).not.toBeInTheDocument();
    expect(mockedClear).not.toHaveBeenCalled();
  });

  it("test_no_stored_id_makes_no_history_request", async () => {
    renderChat();

    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(mockedHistory).not.toHaveBeenCalled();
    expect(screen.getByText(GREETING)).toBeInTheDocument();
    expect(screen.queryByText(EXPIRED)).not.toBeInTheDocument();
  });
});

describe("AssistantChat input", () => {
  it("test_blank_text_is_not_sent", async () => {
    const { user } = renderChat();

    await user.type(screen.getByRole("textbox", { name: INPUT }), "   ");
    await user.click(screen.getByRole("button", { name: SEND }));

    expect(mockedSend).not.toHaveBeenCalled();
  });

  it("test_input_enforces_2000_characters", () => {
    renderChat();

    expect(screen.getByRole("textbox", { name: INPUT })).toHaveAttribute("maxlength", "2000");
  });

  it("test_unmount_aborts_the_stream", async () => {
    replyWith();
    const { user, unmount } = renderChat();
    await send(user, "Привіт");
    const signal = mockedSend.mock.calls[0][3] as AbortSignal;
    expect(signal.aborted).toBe(false);

    unmount();

    expect(signal.aborted).toBe(true);
  });
});

// docs/DECISIONS.md § "Stage 17 sub-step order", 17.5a, and § "Stage 17
// block D" (working indicator).
describe("AssistantChat statuses", () => {
  const DOTS = "assistant-typing-dots";
  const CHECKING = "Перевіряю вільний час…";
  const PROPOSING = "Готую пропозицію…";

  function isBefore(first: Element, second: Element): boolean {
    return Boolean(first.compareDocumentPosition(second) & Node.DOCUMENT_POSITION_FOLLOWING);
  }

  it("test_pulsing_dots_show_while_streaming_before_any_text_or_status", async () => {
    replyWith();
    const { user } = renderChat();
    expect(screen.queryByTestId(DOTS)).not.toBeInTheDocument();

    await send(user, "Привіт");

    expect(await screen.findByTestId(DOTS)).toBeInTheDocument();
  });

  it("test_status_replaces_the_dots_and_the_first_text_clears_it", async () => {
    const channel = replyWith();
    const { user } = renderChat();
    await send(user, "Чи є час у суботу?");

    channel.push({ type: "status", step: "check_availability" });

    expect(await screen.findByText(CHECKING)).toBeInTheDocument();
    expect(screen.queryByTestId(DOTS)).not.toBeInTheDocument();

    channel.push({ type: "text", text: "Так, є" });

    await screen.findByText("Так, є");
    expect(screen.queryByText(CHECKING)).not.toBeInTheDocument();
    expect(screen.queryByTestId(DOTS)).not.toBeInTheDocument();
  });

  it("test_status_after_text_shows_under_the_text_until_the_next_text", async () => {
    const channel = replyWith();
    const { user } = renderChat();
    await send(user, "Запишіть мене");

    channel.push({ type: "text", text: "Зараз підберу" });
    const reply = await screen.findByText("Зараз підберу");
    channel.push({ type: "status", step: "propose_booking" });

    const status = await screen.findByText(PROPOSING);
    expect(isBefore(reply, status)).toBe(true);

    channel.push({ type: "text", text: " час." });

    await screen.findByText("Зараз підберу час.");
    expect(screen.queryByText(PROPOSING)).not.toBeInTheDocument();
  });

  it("test_unknown_step_keeps_the_dots", async () => {
    const channel = replyWith();
    const { user } = renderChat();
    await send(user, "Привіт");
    await screen.findByTestId(DOTS);

    channel.push({ type: "status", step: "mystery_tool" });
    // Let the event be read before checking that nothing changed.
    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(screen.getByTestId(DOTS)).toBeInTheDocument();
    expect(screen.queryByText("mystery_tool")).not.toBeInTheDocument();
  });

  it("test_nothing_is_shown_after_done", async () => {
    const channel = replyWith();
    const { user } = renderChat();
    await send(user, "Привіт");

    channel.push(
      { type: "status", step: "list_services" },
      { type: "text", text: "Вітаю!" },
      { type: "status", step: "check_availability" },
      { type: "done" },
    );
    channel.end();

    // The id is stored only at `done`, so this waits for the end of the turn.
    await waitFor(() => expect(mockedStore).toHaveBeenCalledWith("server-id"));
    expect(screen.queryByTestId(DOTS)).not.toBeInTheDocument();
    expect(screen.queryByText(CHECKING)).not.toBeInTheDocument();
    expect(screen.queryByText("Дивлюся послуги…")).not.toBeInTheDocument();
  });
});

// docs/DECISIONS.md § "Stage 17 sub-step order", 17.5b and its table.
describe("AssistantChat errors and retry", () => {
  const RETRY = "Повторити";
  const BOOK_YOURSELF = "Записатись самостійно";
  const BUSY = "Зараз у мене багато співрозмовниць. Спробуйте, будь ласка, за хвилинку 🤗";
  const NETWORK = "Схоже, зник зв'язок. Перевірте інтернет і спробуйте ще раз.";
  const CUT_OFF = "Відповідь обірвалась. Спробуйте ще раз.";
  const NOTHING_ARRIVED =
    "Ой, щось пішло не так, і я не встигла відповісти. Спробуйте, будь ласка, ще раз.";

  /** Like eventChannel, plus fail(): the stream read rejects mid-stream. */
  function failingChannel() {
    const buffer: AssistantEvent[] = [];
    let ended = false;
    let failure: unknown = null;
    let wake: (() => void) | null = null;
    function notify() {
      wake?.();
      wake = null;
    }
    async function* events(): AsyncGenerator<AssistantEvent> {
      while (true) {
        if (buffer.length > 0) {
          yield buffer.shift() as AssistantEvent;
          continue;
        }
        if (failure !== null) {
          throw failure;
        }
        if (ended) {
          return;
        }
        await new Promise<void>((resolve) => {
          wake = resolve;
        });
      }
    }
    return {
      events: events(),
      push(...items: AssistantEvent[]) {
        buffer.push(...items);
        notify();
      },
      end() {
        ended = true;
        notify();
      },
      fail(error: unknown) {
        failure = error;
        notify();
      },
    };
  }

  function failWithEvents(...items: AssistantEvent[]) {
    const channel = failingChannel();
    mockedSend.mockResolvedValueOnce({ sessionId: "server-id", events: channel.events });
    channel.push(...items);
    channel.end();
  }

  it.each([
    {
      name: "busy",
      arrange: () =>
        mockedSend.mockRejectedValueOnce(new ApiError(503, "assistant_busy", "Busy")),
      text: BUSY,
      retry: true,
      link: false,
    },
    {
      name: "throttled",
      arrange: () => mockedSend.mockRejectedValueOnce(new ApiError(429, "throttled", "Slow")),
      text: "Ви пишете швидше, ніж я встигаю думати 😅 Зачекайте, будь ласка, хвилинку.",
      retry: true,
      link: false,
    },
    {
      name: "daily_limit",
      arrange: () =>
        mockedSend.mockRejectedValueOnce(new ApiError(429, "assistant_daily_limit", "Limit")),
      text: "Я сьогодні наговорилась досхочу і пішла на заслужений манікюр 💅 Повертайтесь завтра! А записатися можна й без мене:",
      retry: false,
      link: true,
    },
    {
      name: "identifier_blocked",
      arrange: () => failWithEvents({ type: "error", code: "assistant_identifier_blocked" }),
      text: "На жаль, зараз я не можу відповісти. Записатися можна через форму на сайті.",
      retry: false,
      link: true,
    },
    {
      name: "network",
      arrange: () => mockedSend.mockRejectedValueOnce(new TypeError("Failed to fetch")),
      text: NETWORK,
      retry: true,
      link: false,
    },
    {
      name: "cut_off_after_text",
      arrange: () =>
        failWithEvents(
          { type: "text", text: "Ось що я знайш" },
          { type: "error", code: "assistant_provider_error" },
        ),
      text: CUT_OFF,
      retry: true,
      link: false,
    },
    {
      name: "nothing_arrived",
      arrange: () => failWithEvents({ type: "error", code: "assistant_turn_limit" }),
      text: NOTHING_ARRIVED,
      retry: true,
      link: false,
    },
    {
      name: "not_found",
      arrange: () => mockedSend.mockRejectedValueOnce(new ApiError(404, "not_found", "Gone")),
      text: "Асистентка зараз недоступна. Записатися можна через форму на сайті.",
      retry: false,
      link: true,
    },
    {
      name: "invalid",
      arrange: () => mockedSend.mockRejectedValueOnce(new ApiError(400, "invalid", "Bad")),
      text: NOTHING_ARRIVED,
      retry: false,
      link: false,
    },
  ])(
    "test_table_row_shows_its_text_retry_and_link_($name)",
    async ({ arrange, text, retry, link }) => {
      arrange();
      const { user } = renderChat();

      await send(user, "Привіт");

      expect(await screen.findByText(text)).toBeInTheDocument();
      if (retry) {
        expect(screen.getByRole("button", { name: RETRY })).toBeInTheDocument();
      } else {
        expect(screen.queryByRole("button", { name: RETRY })).not.toBeInTheDocument();
      }
      if (link) {
        expect(screen.getByRole("link", { name: BOOK_YOURSELF })).toHaveAttribute(
          "href",
          "/services",
        );
      } else {
        expect(screen.queryByRole("link", { name: BOOK_YOURSELF })).not.toBeInTheDocument();
      }
      expect(screen.queryByText(NEUTRAL_ERROR)).not.toBeInTheDocument();
    },
  );

  it("test_network_failure_after_text_shows_the_network_text", async () => {
    const channel = failingChannel();
    mockedSend.mockResolvedValueOnce({ sessionId: "server-id", events: channel.events });
    const { user } = renderChat();
    await send(user, "Привіт");

    channel.push({ type: "text", text: "Ось що я знайш" });
    await screen.findByText("Ось що я знайш");
    channel.fail(new TypeError("network error"));

    expect(await screen.findByText(NETWORK)).toBeInTheDocument();
    expect(screen.queryByText(CUT_OFF)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: RETRY })).toBeInTheDocument();
  });

  it("test_retry_resends_the_same_text_without_a_second_customer_bubble", async () => {
    mockedSend.mockRejectedValueOnce(new ApiError(503, "assistant_busy", "Busy"));
    const retried = replyWith("server-id");
    const { user } = renderChat();
    await send(user, "Скільки коштує манікюр?");
    await screen.findByText(BUSY);

    await user.click(screen.getByRole("button", { name: RETRY }));
    retried.push({ type: "text", text: "Від 500 грн." }, { type: "done" });
    retried.end();

    expect(await screen.findByText("Від 500 грн.")).toBeInTheDocument();
    expect(mockedSend).toHaveBeenCalledTimes(2);
    expect(mockedSend).toHaveBeenLastCalledWith(
      "bella-demo",
      "Скільки коштує манікюр?",
      null,
      expect.any(AbortSignal),
    );
    expect(screen.getAllByText("Скільки коштує манікюр?")).toHaveLength(1);
    expect(screen.queryByText(BUSY)).not.toBeInTheDocument();
  });

  it("test_retry_uses_the_stored_id_and_replaces_the_partial_reply", async () => {
    mockedRead.mockReturnValue("stored-id");
    mockedHistory.mockResolvedValueOnce([
      { role: "user", content: "Привіт" },
      { role: "assistant", content: "Вітаю!" },
    ]);
    const { user } = renderChat();
    await screen.findByText("Вітаю!");
    failWithEvents(
      { type: "text", text: "Ось що я знайш" },
      { type: "error", code: "assistant_provider_error" },
    );
    await send(user, "Що є на суботу?");
    await screen.findByText(CUT_OFF);
    expect(screen.getByText("Ось що я знайш")).toBeInTheDocument();

    const retried = replyWith("stored-id");
    await user.click(screen.getByRole("button", { name: RETRY }));
    retried.push({ type: "text", text: "У суботу є 10:00 і 12:30." }, { type: "done" });
    retried.end();

    expect(await screen.findByText("У суботу є 10:00 і 12:30.")).toBeInTheDocument();
    expect(mockedSend).toHaveBeenLastCalledWith(
      "bella-demo",
      "Що є на суботу?",
      "stored-id",
      expect.any(AbortSignal),
    );
    expect(screen.queryByText("Ось що я знайш")).not.toBeInTheDocument();
    // History (2) + the customer message + the new reply.
    expect(screen.getAllByRole("listitem")).toHaveLength(4);
  });

  it("test_second_failure_and_second_retry_resend_the_same_text_once", async () => {
    mockedSend.mockRejectedValueOnce(new ApiError(503, "assistant_busy", "Busy"));
    const { user } = renderChat();
    await send(user, "Скільки коштує манікюр?");
    await screen.findByText(BUSY);

    failWithEvents(
      { type: "text", text: "Ось що я знайш" },
      { type: "error", code: "assistant_provider_error" },
    );
    await user.click(screen.getByRole("button", { name: RETRY }));
    await screen.findByText(CUT_OFF);

    const retried = replyWith("server-id");
    await user.click(screen.getByRole("button", { name: RETRY }));
    retried.push({ type: "text", text: "Від 500 грн." }, { type: "done" });
    retried.end();

    expect(await screen.findByText("Від 500 грн.")).toBeInTheDocument();
    expect(mockedSend).toHaveBeenCalledTimes(3);
    expect(mockedSend).toHaveBeenLastCalledWith(
      "bella-demo",
      "Скільки коштує манікюр?",
      null,
      expect.any(AbortSignal),
    );
    expect(screen.getAllByText("Скільки коштує манікюр?")).toHaveLength(1);
    expect(screen.queryByText("Ось що я знайш")).not.toBeInTheDocument();
    expect(screen.queryByText(CUT_OFF)).not.toBeInTheDocument();
  });

  it("test_input_stays_enabled_and_a_new_message_hides_the_retry_button", async () => {
    mockedSend.mockRejectedValueOnce(new ApiError(503, "assistant_busy", "Busy"));
    const { user } = renderChat();
    await send(user, "Привіт");
    await screen.findByText(BUSY);

    expect(screen.getByRole("textbox", { name: INPUT })).toBeEnabled();
    expect(screen.getByRole("button", { name: RETRY })).toBeInTheDocument();

    const next = replyWith("server-id");
    await send(user, "Інше питання");

    expect(screen.queryByRole("button", { name: RETRY })).not.toBeInTheDocument();
    next.push({ type: "text", text: "Слухаю" }, { type: "done" });
    next.end();
    await screen.findByText("Слухаю");
    expect(screen.queryByRole("button", { name: RETRY })).not.toBeInTheDocument();
  });

  // docs/DECISIONS.md § "Stage 17 sub-step order", 17.7c, failure-block item
  // of 06.11.2026 (mockup "AI 8"): each failure is one block inside the
  // message list, not inside an assistant bubble, holding its 17.5b text and
  // its action; the retry button reads "Повторити". Structure only: the
  // layout is checked by hand, so the block is found without naming markup.
  describe("failure block (17.7c)", () => {
    const OLD_RETRY = "Спробувати ще раз";
    const PARTIAL = "Ось що я знайш";

    type Action = "retry" | "link" | "none";
    interface Kind {
      name: string;
      arrange: () => void;
      text: string;
      action: Action;
    }

    const KINDS: Kind[] = [
      {
        name: "busy",
        arrange: () =>
          mockedSend.mockRejectedValueOnce(new ApiError(503, "assistant_busy", "Busy")),
        text: BUSY,
        action: "retry",
      },
      {
        name: "throttled",
        arrange: () => mockedSend.mockRejectedValueOnce(new ApiError(429, "throttled", "Slow")),
        text: "Ви пишете швидше, ніж я встигаю думати 😅 Зачекайте, будь ласка, хвилинку.",
        action: "retry",
      },
      {
        name: "daily_limit",
        arrange: () =>
          mockedSend.mockRejectedValueOnce(new ApiError(429, "assistant_daily_limit", "Limit")),
        text: "Я сьогодні наговорилась досхочу і пішла на заслужений манікюр 💅 Повертайтесь завтра! А записатися можна й без мене:",
        action: "link",
      },
      {
        name: "identifier_blocked",
        arrange: () => failWithEvents({ type: "error", code: "assistant_identifier_blocked" }),
        text: "На жаль, зараз я не можу відповісти. Записатися можна через форму на сайті.",
        action: "link",
      },
      {
        name: "network_before_text",
        arrange: () => mockedSend.mockRejectedValueOnce(new TypeError("Failed to fetch")),
        text: NETWORK,
        action: "retry",
      },
      {
        name: "network_after_text",
        arrange: () => {
          const channel = failingChannel();
          mockedSend.mockResolvedValueOnce({ sessionId: "server-id", events: channel.events });
          channel.push({ type: "text", text: PARTIAL });
          channel.fail(new TypeError("network error"));
        },
        text: NETWORK,
        action: "retry",
      },
      {
        name: "cut_off_after_text",
        arrange: () =>
          failWithEvents(
            { type: "text", text: PARTIAL },
            { type: "error", code: "assistant_provider_error" },
          ),
        text: CUT_OFF,
        action: "retry",
      },
      {
        name: "nothing_arrived",
        arrange: () => failWithEvents({ type: "error", code: "assistant_turn_limit" }),
        text: NOTHING_ARRIVED,
        action: "retry",
      },
      {
        name: "not_found",
        arrange: () => mockedSend.mockRejectedValueOnce(new ApiError(404, "not_found", "Gone")),
        text: "Асистентка зараз недоступна. Записатися можна через форму на сайті.",
        action: "link",
      },
      {
        name: "invalid",
        arrange: () => mockedSend.mockRejectedValueOnce(new ApiError(400, "invalid", "Bad")),
        text: NOTHING_ARRIVED,
        action: "none",
      },
    ];

    /** The nearest element holding both nodes. */
    function commonAncestor(first: Element, second: Element): Element {
      let node: Element | null = first;
      while (node !== null && !node.contains(second)) {
        node = node.parentElement;
      }
      return node as Element;
    }

    async function showFailure(kind: Kind) {
      kind.arrange();
      const { user } = renderChat();
      await send(user, "Привіт");
      const text = await screen.findByText(kind.text);
      return { user, text };
    }

    /** The block: inside the message list, and not in an assistant bubble. */
    function expectBlockInTheList(block: Element) {
      const list = screen.getByRole("list", { name: "Розмова" });
      expect(block).not.toBe(list);
      expect(list).toContainElement(block as HTMLElement);
      const item = block.closest("li");
      if (item !== null) {
        expect(item.textContent).toBe(block.textContent);
      }
    }

    it.each(KINDS)("test_failure_text_and_action_share_one_block_in_the_list_($name)", async (kind) => {
      const { text } = await showFailure(kind);

      const action =
        kind.action === "retry"
          ? screen.getByRole("button", { name: RETRY })
          : kind.action === "link"
            ? screen.getByRole("link", { name: BOOK_YOURSELF })
            : null;
      const block = action === null ? text : commonAncestor(text, action);

      expectBlockInTheList(block);
    });

    it.each(KINDS.filter((kind) => kind.action === "retry"))(
      "test_retryable_failure_has_povtoryty_and_no_old_retry_text_($name)",
      async (kind) => {
        const { text } = await showFailure(kind);

        const button = screen.getByRole("button", { name: RETRY });
        expectBlockInTheList(commonAncestor(text, button));
        expect(screen.queryByText(OLD_RETRY)).not.toBeInTheDocument();
        expect(screen.queryByRole("button", { name: OLD_RETRY })).not.toBeInTheDocument();
        expect(screen.queryByRole("link", { name: OLD_RETRY })).not.toBeInTheDocument();
        expect(screen.queryByLabelText(OLD_RETRY)).not.toBeInTheDocument();
      },
    );

    it.each(KINDS.filter((kind) => kind.action === "link"))(
      "test_non_retryable_failure_has_the_booking_link_in_the_block_and_no_retry_($name)",
      async (kind) => {
        const { text } = await showFailure(kind);

        const link = screen.getByRole("link", { name: BOOK_YOURSELF });
        expect(link).toHaveAttribute("href", "/services");
        expectBlockInTheList(commonAncestor(text, link));
        expect(screen.queryByRole("button", { name: RETRY })).not.toBeInTheDocument();
        expect(screen.queryByRole("button", { name: OLD_RETRY })).not.toBeInTheDocument();
      },
    );

    it("test_povtoryty_retries_the_same_text_and_the_block_goes_away", async () => {
      mockedSend.mockRejectedValueOnce(new ApiError(503, "assistant_busy", "Busy"));
      const retried = replyWith("server-id");
      const { user } = renderChat();
      await send(user, "Привіт");
      await screen.findByText(BUSY);

      await user.click(screen.getByRole("button", { name: RETRY }));
      retried.push({ type: "text", text: "Від 500 грн." }, { type: "done" });
      retried.end();

      expect(await screen.findByText("Від 500 грн.")).toBeInTheDocument();
      expect(mockedSend).toHaveBeenCalledTimes(2);
      expect(mockedSend).toHaveBeenLastCalledWith(
        "bella-demo",
        "Привіт",
        null,
        expect.any(AbortSignal),
      );
      expect(screen.getAllByText("Привіт")).toHaveLength(1);
      expect(screen.queryByText(BUSY)).not.toBeInTheDocument();
      expect(screen.queryByRole("button", { name: RETRY })).not.toBeInTheDocument();
    });
  });
});

// docs/DECISIONS.md § "Stage 17 sub-step order", 17.7c, item of 06.11.2026:
// while a reply streams the input stays enabled and text typed meanwhile is
// the 17.7b draft; only sending is blocked (button disabled, Enter does not
// send) until the turn finishes. Focus is covered in AssistantWidget.test.
describe("AssistantChat while a reply streams (17.7c)", () => {
  const DOTS = "assistant-typing-dots";
  const DRAFT_KEY = "salonhub:assistant-draft";
  const RETRY = "Повторити";
  const LATER = "А в неділю?";

  // The draft lives in real sessionStorage; keep it from leaking between tests.
  beforeEach(() => sessionStorage.clear());
  afterEach(() => sessionStorage.clear());

  function input() {
    return screen.getByRole("textbox", { name: INPUT });
  }

  function sendButton() {
    return screen.getByRole("button", { name: SEND });
  }

  it("test_input_is_enabled_while_streaming_and_typing_is_stored_as_the_draft", async () => {
    replyWith();
    const { user } = renderChat();
    await send(user, "Привіт");
    await screen.findByTestId(DOTS);

    expect(input()).toBeEnabled();
    await user.type(input(), LATER);

    expect(input()).toHaveValue(LATER);
    expect(sessionStorage.getItem(DRAFT_KEY)).toBe(LATER);
  });

  it("test_sending_is_blocked_while_streaming_by_the_button_and_by_enter", async () => {
    replyWith();
    const { user } = renderChat();
    await send(user, "Привіт");
    await screen.findByTestId(DOTS);

    await user.type(input(), LATER);
    expect(sendButton()).toBeDisabled();
    await user.click(sendButton());
    await user.type(input(), "{Enter}");

    expect(mockedSend).toHaveBeenCalledTimes(1);
    expect(input()).toHaveValue(LATER);
    expect(
      within(screen.getByRole("list", { name: "Розмова" })).queryByText(LATER),
    ).not.toBeInTheDocument();
  });

  it("test_after_the_turn_enter_sends_the_text_typed_while_streaming", async () => {
    const first = replyWith("server-id");
    replyWith("server-id");
    const { user } = renderChat();
    await send(user, "Привіт");
    await user.type(input(), LATER);

    first.push({ type: "text", text: "Вітаю" }, { type: "done" });
    first.end();
    await waitFor(() => expect(sendButton()).toBeEnabled());
    await user.type(input(), "{Enter}");

    expect(mockedSend).toHaveBeenCalledTimes(2);
    expect(mockedSend).toHaveBeenLastCalledWith(
      "bella-demo",
      LATER,
      "server-id",
      expect.any(AbortSignal),
    );
    expect(input()).toHaveValue("");
  });

  it("test_the_same_rules_hold_while_a_retry_streams", async () => {
    mockedSend.mockRejectedValueOnce(new ApiError(503, "assistant_busy", "Busy"));
    const retried = replyWith("server-id");
    replyWith("server-id");
    const { user } = renderChat();
    await send(user, "Привіт");
    await user.click(await screen.findByRole("button", { name: RETRY }));
    await screen.findByTestId(DOTS);

    expect(input()).toBeEnabled();
    await user.type(input(), LATER);
    expect(sessionStorage.getItem(DRAFT_KEY)).toBe(LATER);
    expect(sendButton()).toBeDisabled();
    await user.type(input(), "{Enter}");
    expect(mockedSend).toHaveBeenCalledTimes(2);
    expect(input()).toHaveValue(LATER);

    retried.push({ type: "text", text: "Вітаю" }, { type: "done" });
    retried.end();
    await waitFor(() => expect(sendButton()).toBeEnabled());
    await user.type(input(), "{Enter}");

    expect(mockedSend).toHaveBeenCalledTimes(3);
    expect(mockedSend).toHaveBeenLastCalledWith(
      "bella-demo",
      LATER,
      "server-id",
      expect.any(AbortSignal),
    );
  });
});

// docs/DECISIONS.md § "Stage 17 sub-step order", 17.6a; card shape from
// backend/ai_assistant/proposals.py. 2026-11-07 is a Saturday.
describe("AssistantChat proposal card", () => {
  const SERVICE_NAME = "Манікюр з покриттям";
  const OTHER_TIME = "Інший день або час";
  const CHOOSE_TIME = "Обрати час";
  const SAT_10 = "2026-11-07T10:00:00+02:00";
  const SAT_1230 = "2026-11-07T12:30:00+02:00";
  const SUN_11 = "2026-11-08T11:00:00+02:00";
  const BASE = "/booking?entry=service&service=7&specialist=3";

  function proposalCard(overrides: Record<string, unknown> = {}) {
    return {
      service: { id: 7, name: SERVICE_NAME, duration_minutes: 60 },
      specialist: { id: 3, name: "Олена", average_rating: 4.8, review_count: 21 },
      price: "500.00",
      deposit: "100.00",
      due_at_visit: "400.00",
      currency: "UAH",
      slots: [SAT_10, SAT_1230],
      ...overrides,
    };
  }

  /** formatPrice output uses U+00A0; match any whitespace run instead. */
  function textPattern(text: string): RegExp {
    const escaped = text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    return new RegExp(`^${escaped.replace(/\s+/g, "\\s+")}$`);
  }

  function slotHref(slot: string, specialist = "3"): string {
    return `/booking?entry=service&service=7&specialist=${specialist}&step=4&slot=${encodeURIComponent(slot)}`;
  }

  /** Renders a restored conversation whose assistant message has the card. */
  async function renderCard(card: unknown, content = "") {
    mockedRead.mockReturnValue("stored-id");
    mockedHistory.mockResolvedValueOnce([
      { role: "user", content: "Запишіть мене на манікюр" },
      { role: "assistant", content, card },
    ]);
    const result = renderChat();
    await screen.findByText("Запишіть мене на манікюр");
    return result;
  }

  it("test_specialist_line_shows_rating_and_review_count_plural", async () => {
    await renderCard(proposalCard());

    expect(await screen.findByText(SERVICE_NAME)).toBeInTheDocument();
    expect(screen.getByText("Олена · ★ 4.8 (21 відгук) · 60 хв")).toBeInTheDocument();
  });

  it("test_specialist_line_without_reviews", async () => {
    await renderCard(
      proposalCard({
        specialist: { id: 3, name: "Олена", average_rating: null, review_count: 0 },
      }),
    );

    expect(await screen.findByText("Олена · Немає відгуків · 60 хв")).toBeInTheDocument();
  });

  it("test_specialist_line_for_any_specialist", async () => {
    await renderCard(proposalCard({ specialist: "any" }));

    expect(await screen.findByText("Будь-який спеціаліст · 60 хв")).toBeInTheDocument();
  });

  it("test_money_lines_with_the_deposit_bold", async () => {
    await renderCard(proposalCard());

    const price = await screen.findByText(textPattern(`Вартість ${formatPrice("500.00", "UAH")}`));
    const deposit = screen.getByText(
      textPattern(`Передоплата онлайн ${formatPrice("100.00", "UAH")}`),
    );
    const due = screen.getByText(textPattern(`На місці ${formatPrice("400.00", "UAH")}`));
    expect(deposit.closest("strong, b")).not.toBeNull();
    expect(price.closest("strong, b")).toBeNull();
    expect(due.closest("strong, b")).toBeNull();
  });

  it("test_slots_on_one_day_show_a_date_line_and_time_chips", async () => {
    await renderCard(proposalCard({ slots: [SAT_10, SAT_1230] }));

    expect(await screen.findByText("Сб, 7 листопада")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "10:00" })).toHaveAttribute("href", slotHref(SAT_10));
    expect(screen.getByRole("link", { name: "12:30" })).toHaveAttribute(
      "href",
      slotHref(SAT_1230),
    );
    expect(screen.getByRole("link", { name: OTHER_TIME })).toHaveAttribute(
      "href",
      `${BASE}&step=3&date_from=2026-11-07`,
    );
    expect(screen.queryByRole("link", { name: CHOOSE_TIME })).not.toBeInTheDocument();
  });

  it("test_slots_on_different_days_show_the_day_on_each_chip", async () => {
    await renderCard(proposalCard({ slots: [SAT_10, SUN_11] }));

    expect(await screen.findByRole("link", { name: "Сб 10:00" })).toHaveAttribute(
      "href",
      slotHref(SAT_10),
    );
    expect(screen.getByRole("link", { name: "Нд 11:00" })).toHaveAttribute(
      "href",
      slotHref(SUN_11),
    );
    expect(screen.queryByText("Сб, 7 листопада")).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: OTHER_TIME })).toHaveAttribute(
      "href",
      `${BASE}&step=3&date_from=2026-11-07`,
    );
  });

  it("test_one_slot_shows_one_booking_button", async () => {
    await renderCard(proposalCard({ slots: [SAT_10] }));

    expect(
      await screen.findByRole("link", { name: "Записатися на 7 листопада, 10:00" }),
    ).toHaveAttribute("href", slotHref(SAT_10));
    expect(screen.queryByRole("link", { name: "10:00" })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: OTHER_TIME })).toHaveAttribute(
      "href",
      `${BASE}&step=3&date_from=2026-11-07`,
    );
  });

  it("test_no_slots_show_only_choose_time", async () => {
    await renderCard(proposalCard({ specialist: "any", slots: [] }));

    expect(await screen.findByRole("link", { name: CHOOSE_TIME })).toHaveAttribute(
      "href",
      "/booking?entry=service&service=7&specialist=any&step=3",
    );
    expect(screen.queryByRole("link", { name: OTHER_TIME })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /Записатися на/ })).not.toBeInTheDocument();
  });

  it("test_live_proposal_without_text_shows_the_card", async () => {
    const channel = replyWith("server-id");
    const { user } = renderChat();
    await send(user, "Запишіть мене");

    channel.push(
      { type: "proposal", card: proposalCard({ slots: [SAT_10] }) },
      { type: "done" },
    );
    channel.end();

    expect(await screen.findByText(SERVICE_NAME)).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: "Записатися на 7 листопада, 10:00" }),
    ).toBeInTheDocument();
    expect(screen.getAllByRole("listitem")).toHaveLength(2);
  });

  it("test_history_message_with_empty_content_and_a_card_shows_the_card", async () => {
    await renderCard(proposalCard(), "");

    expect(await screen.findByText(SERVICE_NAME)).toBeInTheDocument();
    expect(screen.getAllByRole("listitem")).toHaveLength(2);
  });

  it("test_history_message_with_text_and_a_card_shows_both", async () => {
    await renderCard(proposalCard(), "Ось вільний час:");

    expect(await screen.findByText(SERVICE_NAME)).toBeInTheDocument();
    expect(screen.getByText("Ось вільний час:")).toBeInTheDocument();
  });

  // A malformed history card: merged into the "AssistantChat history" test
  // test_history_skips_empty_messages_and_drops_a_malformed_card_keeping_its_text.

  it("test_retry_removes_the_failed_turn_card", async () => {
    const failed = replyWith("server-id");
    const { user } = renderChat();
    await send(user, "Запишіть мене");
    failed.push(
      { type: "text", text: "Ось варіант" },
      { type: "proposal", card: proposalCard({ slots: [SAT_10] }) },
      { type: "error", code: "assistant_provider_error" },
    );
    failed.end();
    await screen.findByText(SERVICE_NAME);

    const retried = replyWith("server-id");
    await user.click(screen.getByRole("button", { name: "Повторити" }));
    retried.push({ type: "text", text: "Спробуйте інший день" }, { type: "done" });
    retried.end();

    expect(await screen.findByText("Спробуйте інший день")).toBeInTheDocument();
    expect(screen.queryByText(SERVICE_NAME)).not.toBeInTheDocument();
  });
});

// docs/DECISIONS.md § "Stage 17 sub-step order", 17.7c, AI notice item of
// 06.11.2026: always visible, including while a failure block shows. Its
// place and link on phone and desktop: AssistantWidget.test.
describe("AssistantChat AI notice (17.7c)", () => {
  const NOTICE = "ШІ може помилятися. Перевіряйте важливу інформацію.";
  const BUSY = "Зараз у мене багато співрозмовниць. Спробуйте, будь ласка, за хвилинку 🤗";

  it("test_notice_stays_after_the_input_form_while_a_failure_shows", async () => {
    mockedSend.mockRejectedValueOnce(new ApiError(503, "assistant_busy", "Busy"));
    const { user } = renderChat();
    await send(user, "Привіт");
    await screen.findByText(BUSY);

    const list = screen.getByRole("list", { name: "Розмова" });
    // 17.7c, changed 06.11.2026: the sentence alone is the link.
    const notice = screen.getByRole("link", { name: (name) => name.startsWith(NOTICE) });
    const input = screen.getByRole("textbox", { name: INPUT });
    expect(notice).toBeVisible();
    const form = input.closest("form") as HTMLFormElement;
    expect(list).not.toContainElement(notice);
    expect(form).not.toContainElement(notice);
    expect(list.compareDocumentPosition(notice) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(form.compareDocumentPosition(notice) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });
});

// docs/DECISIONS.md § "Stage 17 sub-step order", 17.7c: a visually hidden
// "Асистентка пише…" in the status container while the dots show (decided
// 06.11.2026: only then; a status line replacing the dots is announced
// itself).
describe("AssistantChat hidden typing text (17.7c)", () => {
  const DOTS = "assistant-typing-dots";
  const TYPING = "Асистентка пише…";
  const CHECKING = "Перевіряю вільний час…";

  /** The status container that holds the dots. */
  function statusContainer(): HTMLElement {
    return screen.getByTestId(DOTS).closest('[role="status"]') as HTMLElement;
  }

  /** The hidden text is in the container, sr-only, and not aria-hidden. */
  function expectTypingTextIn(container: HTMLElement) {
    const typing = within(container).getByText(TYPING);
    expect(typing).toHaveClass("sr-only");
    expect(typing.closest('[aria-hidden="true"]')).toBeNull();
  }

  it("test_typing_text_is_in_the_status_container_while_the_dots_show", async () => {
    replyWith();
    const { user } = renderChat();
    await send(user, "Привіт");
    await screen.findByTestId(DOTS);

    expectTypingTextIn(statusContainer());
  });

  it("test_a_status_line_replaces_the_typing_text", async () => {
    const channel = replyWith();
    const { user } = renderChat();
    await send(user, "Чи є час у суботу?");
    await screen.findByTestId(DOTS);
    const container = statusContainer();
    expectTypingTextIn(container);

    channel.push({ type: "status", step: "check_availability" });

    expect(await within(container).findByText(CHECKING)).toBeInTheDocument();
    expect(screen.queryByText(TYPING)).not.toBeInTheDocument();
  });

  it("test_nothing_is_announced_after_the_reply_ends", async () => {
    const channel = replyWith("server-id");
    const { user } = renderChat();
    await send(user, "Привіт");
    await screen.findByTestId(DOTS);
    expectTypingTextIn(statusContainer());

    channel.push({ type: "status", step: "check_availability" }, { type: "done" });
    channel.end();
    // The id is stored only at `done`, so this waits for the end of the turn.
    await waitFor(() => expect(mockedStore).toHaveBeenCalledWith("server-id"));

    expect(screen.queryByText(TYPING)).not.toBeInTheDocument();
    expect(screen.queryByText(CHECKING)).not.toBeInTheDocument();
    expect(screen.queryByTestId(DOTS)).not.toBeInTheDocument();
  });
});
