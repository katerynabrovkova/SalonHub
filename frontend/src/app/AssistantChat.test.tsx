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
      arrange: () => {
        mockedSend.mockRejectedValueOnce(
          new ApiError(503, "assistant_busy", "The assistant is busy."),
        );
        return () => undefined;
      },
    },
    {
      name: "stream_ends_without_done",
      arrange: () => {
        const channel = replyWith();
        return () => {
          channel.push({ type: "text", text: "Вітаю" });
          channel.end();
        };
      },
    },
  ])(
    "test_failed_reply_stores_no_id_shows_the_neutral_line_and_enables_input_($name)",
    async ({ arrange }) => {
      const finish = arrange();
      const { user } = renderChat();
      await send(user, "Привіт");

      finish();

      expect(await screen.findByText(NEUTRAL_ERROR)).toBeInTheDocument();
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
