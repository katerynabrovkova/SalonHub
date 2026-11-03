// @vitest-environment jsdom
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

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
}));

// Imported after the mocks above so the mocked modules are what the chat sees.
import { ApiError } from "@/lib/api/errors";
import { getAssistantHistory, sendAssistantMessage } from "@/lib/assistant/assistantApi";
import { clearSessionId, readSessionId, storeSessionId } from "@/lib/assistant/sessionId";

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
function Chat({ slug = "bella-demo" }: { slug?: string }) {
  const conversation = useAssistantConversation(slug);
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

  it("test_input_is_disabled_while_streaming_and_enabled_after_done", async () => {
    const channel = replyWith();
    const { user } = renderChat();
    await send(user, "Привіт");

    expect(screen.getByRole("textbox", { name: INPUT })).toBeDisabled();

    channel.push({ type: "text", text: "Вітаю" }, { type: "done" });
    channel.end();

    await waitFor(() => expect(screen.getByRole("textbox", { name: INPUT })).toBeEnabled());
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

  it("test_status_proposal_and_unknown_events_are_ignored", async () => {
    const channel = replyWith("server-id");
    const { user } = renderChat();
    await send(user, "Привіт");

    channel.push(
      { type: "text", text: "Ось " },
      { type: "status", step: "check_availability" },
      { type: "proposal", card: { service: { id: 1, name: "Манікюр" }, slots: [] } },
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
    expect(mockedHistory).toHaveBeenCalledWith("bella-demo", "stored-id");
  });

  it("test_history_card_shows_text_only_and_empty_messages_are_skipped", async () => {
    mockedRead.mockReturnValue("stored-id");
    mockedHistory.mockResolvedValueOnce([
      { role: "user", content: "Манікюр на суботу?" },
      {
        role: "assistant",
        content: "Є вільний час.",
        card: { service: { id: 1, name: "Манікюр" }, slots: ["2026-11-07T10:00:00+02:00"] },
      },
      { role: "assistant", content: "", card: { service: { id: 1, name: "Манікюр" }, slots: [] } },
    ]);
    renderChat();

    expect(await screen.findByText("Є вільний час.")).toBeInTheDocument();
    const items = screen.getAllByRole("listitem");
    expect(items).toHaveLength(2);
    expect(within(items[1]).queryByText(/Манікюр/)).not.toBeInTheDocument();
  });

  it("test_stored_id_with_empty_history_shows_the_expired_notice_and_clears_the_id", async () => {
    mockedRead.mockReturnValue("stored-id");
    mockedHistory.mockResolvedValueOnce([]);
    renderChat();

    const notice = await screen.findByText(EXPIRED);
    const greeting = screen.getByText(GREETING);
    expect(notice.compareDocumentPosition(greeting) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(mockedClear).toHaveBeenCalledTimes(1);
  });

  it("test_failed_history_shows_the_plain_greeting_and_keeps_the_id", async () => {
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

    await waitFor(() => expect(screen.getByRole("textbox", { name: INPUT })).toBeEnabled());
    expect(screen.queryByTestId(DOTS)).not.toBeInTheDocument();
    expect(screen.queryByText(CHECKING)).not.toBeInTheDocument();
    expect(screen.queryByText("Дивлюся послуги…")).not.toBeInTheDocument();
  });
});

// docs/DECISIONS.md § "Stage 17 sub-step order", 17.5b and its table.
describe("AssistantChat errors and retry", () => {
  const RETRY = "Спробувати ще раз";
  const BOOK_YOURSELF = "Записатись самостійно";
  const BUSY = "Зараз у мене багато співрозмовниць. Спробуйте, будь ласка, за хвилинку 🤗";
  const NETWORK = "Схоже, зник зв'язок. Перевірте інтернет і спробуйте ще раз.";
  const CUT_OFF = "Відповідь обірвалась. Спробуйте ще раз.";
  const NOTHING_ARRIVED =
    "Ой, щось пішло не так, і я не встиг відповісти. Спробуйте, будь ласка, ще раз.";

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
      text: "Я сьогодні наговорився досхочу і пішов на заслужений манікюр 💅 Повертайтесь завтра! А записатися можна й без мене:",
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
      text: "Асистент зараз недоступний. Записатися можна через форму на сайті.",
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
});
