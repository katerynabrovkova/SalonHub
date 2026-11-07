// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactElement } from "react";
import { hydrateRoot, type Root } from "react-dom/client";
import { renderToString } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// docs/DECISIONS.md § "Stage 17 sub-step order", 17.7h (decided 07.11.2026):
// with a stored session id, sending waits until the history has loaded on
// this page load; a slow load shows a spinner after about 0.3 s; the request
// is aborted after 10 s and on unmount; a failed load shows the amber block
// "Не вдалося завантажити розмову." with "Повторити" and "Записатись
// самостійно"; opening the chat and returning to the tab retry it, never in
// parallel; a successful load (mount or retry) runs the 17.7e rules and stops
// the retries.
//
// Driven through the widget with real sessionStorage; only the network
// (assistantApi) and the router are mocked. Assumption (not fixed by the
// entry): the hook owns the request's AbortController and passes its signal
// to getAssistantHistory as a third argument, which forwards it to fetch.

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
import { ApiError } from "@/lib/api/errors";
import type { AssistantEvent, AssistantHistoryMessage } from "@/lib/assistant/assistantApi";
import { getAssistantHistory, sendAssistantMessage } from "@/lib/assistant/assistantApi";

import AssistantWidget from "./AssistantWidget";

const SESSION_KEY = "salonhub:assistant-session";
const PENDING_KEY = "salonhub:assistant-pending";
const DRAFT_KEY = "salonhub:assistant-draft";
const MODE_KEY = "salonhub:assistant-mode";

const STRIPE = "Відкрити асистента";
const MINIMISE = "Згорнути";
const INPUT = "Повідомлення";
const SEND = "Надіслати";
const RETRY = "Повторити";
const BOOK_YOURSELF = "Записатись самостійно";
const LOAD_FAILED = "Не вдалося завантажити розмову.";
const LOADING = "Завантажую розмову…";
const SPINNER = "assistant-history-spinner";
const CUT_OFF = "Відповідь обірвалась. Спробуйте ще раз.";
const TEXT = "Чи є вільний час у суботу?";
const DRAFT = "А в неділю?";
const OLD: AssistantHistoryMessage[] = [
  { role: "user", content: "Привіт" },
  { role: "assistant", content: "Вітаю!" },
];

const mockedHistory = vi.mocked(getAssistantHistory);
const mockedSend = vi.mocked(sendAssistantMessage);

type HistoryArgs = [slug: string, sessionId: string | null, signal?: AbortSignal];

/** The signal passed with the history call at `index` (17.7h assumption). */
function signalOf(index: number): AbortSignal | undefined {
  return (mockedHistory.mock.calls[index] as unknown as HistoryArgs)[2];
}

interface PendingHistory {
  resolve: (messages: AssistantHistoryMessage[]) => void;
  reject: (error: unknown) => void;
}

/**
 * The next history call stays pending until resolved or rejected. Like a
 * real fetch, it rejects with an AbortError once its signal aborts.
 */
function pendingHistory(): PendingHistory {
  const handle: PendingHistory = { resolve: () => {}, reject: () => {} };
  mockedHistory.mockImplementationOnce(((_slug: string, _id: string | null, signal?: AbortSignal) =>
    new Promise<AssistantHistoryMessage[]>((resolve, reject) => {
      handle.resolve = resolve;
      handle.reject = reject;
      signal?.addEventListener("abort", () =>
        reject(new DOMException("The operation was aborted.", "AbortError")),
      );
    })) as typeof getAssistantHistory);
  return handle;
}

let visibility: DocumentVisibilityState = "visible";
Object.defineProperty(document, "visibilityState", {
  configurable: true,
  get: () => visibility,
});
Object.defineProperty(document, "hidden", {
  configurable: true,
  get: () => visibility === "hidden",
});

async function setVisibility(state: DocumentVisibilityState) {
  visibility = state;
  await act(async () => {
    document.dispatchEvent(new Event("visibilitychange"));
  });
}

async function returnToTab() {
  await setVisibility("hidden");
  await setVisibility("visible");
}

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

function widget(): ReactElement {
  return <AssistantWidget salonName="Bella" slug="bella-demo" />;
}

function renderWidget() {
  const user = userEvent.setup();
  return { user, ...render(widget()) };
}

async function openChat(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole("button", { name: STRIPE }));
  return screen.getByRole("dialog");
}

async function minimise(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole("button", { name: MINIMISE }));
}

function input() {
  return screen.getByRole("textbox", { name: INPUT });
}

function sendButton() {
  return screen.getByRole("button", { name: SEND });
}

function tick() {
  return new Promise((resolve) => setTimeout(resolve, 0));
}

// Waits by plain assertions (not findBy*), so missing behaviour fails as an
// AssertionError.
async function shown(text: string) {
  await waitFor(() => expect(screen.queryByText(text)).toBeInTheDocument());
}

async function failureBlockShown() {
  await shown(LOAD_FAILED);
}

/** The text of each conversation item, in order (bubbles and failure block). */
function items() {
  return within(screen.getByRole("list", { name: "Розмова" }))
    .getAllByRole("listitem")
    .map((item) => item.textContent);
}

async function* streamOf(events: AssistantEvent[]): AsyncGenerator<AssistantEvent> {
  for (const event of events) {
    yield event;
  }
}

/** A reload into a stored conversation: the id is stored, the chat open. */
function storeConversation({ open }: { open: boolean }) {
  sessionStorage.setItem(SESSION_KEY, "stored-id");
  if (open) {
    sessionStorage.setItem(MODE_KEY, "window");
  }
}

/** Mount with a stored id whose history request fails; chat collapsed. */
async function mountWithFailedHistory(error: unknown = new TypeError("Failed to fetch")) {
  storeConversation({ open: false });
  mockedHistory.mockRejectedValueOnce(error);
  const result = renderWidget();
  await waitFor(() => expect(mockedHistory).toHaveBeenCalledTimes(1));
  await tick();
  return result;
}

beforeEach(() => {
  sessionStorage.clear();
  nav.pathname = "/services";
  visibility = "visible";
  stubMatchMedia();
  mockedHistory.mockReset();
  mockedSend.mockReset();
  mockedHistory.mockResolvedValue([]);
});

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

// --- 1-2. Sending waits for the history --------------------------------------

describe("AssistantWidget sending waits for the history (17.7h)", () => {
  it("test_send_is_disabled_while_the_history_loads", async () => {
    storeConversation({ open: false });
    pendingHistory();
    const { user } = renderWidget();
    await openChat(user);

    await user.type(input(), DRAFT);

    expect(sendButton()).toBeDisabled();
  });

  it("test_input_stays_enabled_and_keeps_the_draft_while_the_history_loads", async () => {
    storeConversation({ open: false });
    pendingHistory();
    const { user } = renderWidget();
    await openChat(user);

    await user.type(input(), DRAFT);

    expect(input()).toBeEnabled();
    expect(input()).toHaveValue(DRAFT);
    expect(sessionStorage.getItem(DRAFT_KEY)).toBe(DRAFT);
  });

  it("test_enter_does_not_send_while_the_history_loads", async () => {
    storeConversation({ open: false });
    pendingHistory();
    const { user } = renderWidget();
    await openChat(user);

    await user.type(input(), `${DRAFT}{Enter}`);
    await tick();

    expect(mockedSend).not.toHaveBeenCalled();
    expect(input()).toHaveValue(DRAFT);
  });

  it.each([
    { name: "with_messages", history: OLD },
    { name: "empty_expired", history: [] as AssistantHistoryMessage[] },
  ])("test_send_is_enabled_once_the_history_has_loaded_($name)", async ({ history }) => {
    storeConversation({ open: false });
    const load = pendingHistory();
    const { user } = renderWidget();
    await openChat(user);
    await user.type(input(), DRAFT);
    // Precondition: still waiting for the history.
    expect(sendButton()).toBeDisabled();

    await act(async () => load.resolve(history));

    await waitFor(() => expect(sendButton()).toBeEnabled());
  });

  // An expired conversation counts as loaded: she can start a new one at
  // once, without the cleared session id.
  it("test_after_an_expired_history_sending_starts_a_new_conversation", async () => {
    storeConversation({ open: false });
    mockedHistory.mockResolvedValueOnce([]);
    mockedSend.mockResolvedValueOnce({
      sessionId: "server-id",
      events: streamOf([{ type: "text", text: "Так, є." }, { type: "done" }]),
    });
    const { user } = renderWidget();
    await waitFor(() => expect(mockedHistory).toHaveBeenCalledTimes(1));
    await tick();
    await openChat(user);

    await user.type(input(), TEXT);
    await user.click(sendButton());

    await waitFor(() => expect(mockedSend).toHaveBeenCalledTimes(1));
    expect(mockedSend.mock.calls[0][1]).toBe(TEXT);
    expect(mockedSend.mock.calls[0][2]).toBeNull();
  });

  it("test_without_a_stored_id_nothing_loads_and_sending_works_at_once", async () => {
    mockedSend.mockResolvedValueOnce({
      sessionId: "server-id",
      events: streamOf([{ type: "text", text: "Так, є." }, { type: "done" }]),
    });
    const { user } = renderWidget();
    await openChat(user);

    await user.type(input(), TEXT);
    expect(sendButton()).toBeEnabled();
    await user.click(sendButton());

    expect(await screen.findByText("Так, є.")).toBeInTheDocument();
    expect(mockedHistory).not.toHaveBeenCalled();
  });
});

// --- 3. Spinner ----------------------------------------------------------------

describe("AssistantWidget history spinner (17.7h)", () => {
  async function advance(ms: number) {
    await act(async () => {
      vi.advanceTimersByTime(ms);
    });
  }

  /** Restored open chat with a stored id, history pending, under fake timers. */
  function mountLoadingOpenChat() {
    vi.useFakeTimers();
    storeConversation({ open: true });
    const load = pendingHistory();
    render(widget());
    return load;
  }

  it("test_no_spinner_before_about_0_3_seconds", async () => {
    mountLoadingOpenChat();

    await advance(200);

    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(screen.queryByTestId(SPINNER)).not.toBeInTheDocument();
    expect(screen.queryByText(LOADING)).not.toBeInTheDocument();
  });

  it("test_spinner_shows_after_about_0_3_seconds_with_a_hidden_text", async () => {
    mountLoadingOpenChat();

    await advance(400);

    expect(screen.queryByTestId(SPINNER)).toBeInTheDocument();
    expect(screen.queryByText(LOADING)).toBeInTheDocument();
    expect(screen.queryByText(LOADING)).toHaveClass("sr-only");
  });

  it("test_spinner_goes_after_the_history_loads", async () => {
    const load = mountLoadingOpenChat();
    await advance(400);
    // Precondition: the spinner is on screen.
    expect(screen.queryByTestId(SPINNER)).toBeInTheDocument();

    await act(async () => load.resolve(OLD));

    expect(screen.getByText("Вітаю!")).toBeInTheDocument();
    expect(screen.queryByTestId(SPINNER)).not.toBeInTheDocument();
    expect(screen.queryByText(LOADING)).not.toBeInTheDocument();
  });

  it("test_fast_load_never_shows_the_spinner", async () => {
    const load = mountLoadingOpenChat();
    await advance(100);

    await act(async () => load.resolve(OLD));
    await advance(1_000);

    expect(screen.getByText("Вітаю!")).toBeInTheDocument();
    expect(screen.queryByTestId(SPINNER)).not.toBeInTheDocument();
  });
});

// --- 4. Timeout and unmount -------------------------------------------------

describe("AssistantWidget history request abort (17.7h)", () => {
  async function advance(ms: number) {
    await act(async () => {
      vi.advanceTimersByTime(ms);
    });
  }

  it("test_history_request_carries_an_abort_signal", async () => {
    storeConversation({ open: false });
    pendingHistory();

    renderWidget();

    await waitFor(() => expect(mockedHistory).toHaveBeenCalledTimes(1));
    expect(signalOf(0)).toBeInstanceOf(AbortSignal);
  });

  it("test_request_is_not_aborted_before_10_seconds", async () => {
    vi.useFakeTimers();
    storeConversation({ open: true });
    pendingHistory();
    render(widget());

    await advance(9_900);

    expect(signalOf(0)?.aborted).toBe(false);
    expect(screen.queryByText(LOAD_FAILED)).not.toBeInTheDocument();
  });

  it("test_request_is_aborted_after_10_seconds_and_counts_as_a_failure", async () => {
    vi.useFakeTimers();
    storeConversation({ open: true });
    pendingHistory();
    render(widget());

    await advance(10_000);

    expect(signalOf(0)?.aborted).toBe(true);
    expect(screen.queryByText(LOAD_FAILED)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: RETRY })).toBeInTheDocument();
  });

  it("test_request_is_aborted_on_unmount", async () => {
    storeConversation({ open: false });
    pendingHistory();
    const { unmount } = renderWidget();
    await waitFor(() => expect(mockedHistory).toHaveBeenCalledTimes(1));

    unmount();

    expect(signalOf(0)?.aborted).toBe(true);
  });
});

// --- 5. Failure block ----------------------------------------------------------

describe("AssistantWidget failed history load (17.7h)", () => {
  it.each([
    { name: "network_error", error: new TypeError("Failed to fetch") },
    { name: "server_error", error: new ApiError(500, "internal_error", "Server error.") },
  ])("test_failure_shows_the_block_with_retry_and_book_yourself_($name)", async ({ error }) => {
    const { user } = await mountWithFailedHistory(error);
    mockedHistory.mockRejectedValue(error);

    await openChat(user);

    const dialog = screen.getByRole("dialog");
    expect(within(dialog).queryByText(LOAD_FAILED)).toBeInTheDocument();
    expect(within(dialog).queryByRole("button", { name: RETRY })).toBeInTheDocument();
    expect(within(dialog).queryByRole("link", { name: BOOK_YOURSELF })).toHaveAttribute(
      "href",
      "/services",
    );
  });

  it("test_send_stays_disabled_after_a_failure", async () => {
    // Stays failed: the open trigger's retry fails too.
    const { user } = await mountWithFailedHistory();
    mockedHistory.mockRejectedValue(new TypeError("Failed to fetch"));
    await openChat(user);
    await failureBlockShown();

    await user.type(input(), `${DRAFT}{Enter}`);

    expect(input()).toBeEnabled();
    expect(sendButton()).toBeDisabled();
    expect(mockedSend).not.toHaveBeenCalled();
  });

  it("test_failure_keeps_a_pending_text_in_storage_and_does_not_show_it", async () => {
    sessionStorage.setItem(PENDING_KEY, TEXT);
    const { user } = await mountWithFailedHistory();
    mockedHistory.mockRejectedValue(new TypeError("Failed to fetch"));
    await openChat(user);

    await failureBlockShown();

    expect(sessionStorage.getItem(PENDING_KEY)).toBe(TEXT);
    expect(screen.queryByText(TEXT)).not.toBeInTheDocument();
    expect(screen.queryByText(CUT_OFF)).not.toBeInTheDocument();
  });
});

// --- 6. "Повторити" ------------------------------------------------------------

describe("AssistantWidget history retry button (17.7h)", () => {
  async function failedOpenChat() {
    const result = await mountWithFailedHistory();
    mockedHistory.mockRejectedValueOnce(new TypeError("Failed to fetch"));
    await openChat(result.user);
    await failureBlockShown();
    return result;
  }

  it("test_retry_loads_the_history_again_and_shows_the_old_messages", async () => {
    const { user } = await failedOpenChat();
    const callsBefore = mockedHistory.mock.calls.length;
    mockedHistory.mockResolvedValueOnce(OLD);

    await user.click(screen.getByRole("button", { name: RETRY }));

    await shown("Вітаю!");
    expect(mockedHistory).toHaveBeenCalledTimes(callsBefore + 1);
    expect(mockedHistory.mock.calls[callsBefore][1]).toBe("stored-id");
    expect(items()).toEqual(["Привіт", "Вітаю!"]);
  });

  it("test_successful_retry_removes_the_block_and_enables_sending", async () => {
    const { user } = await failedOpenChat();
    mockedHistory.mockResolvedValueOnce(OLD);
    await user.type(input(), DRAFT);

    await user.click(screen.getByRole("button", { name: RETRY }));
    await shown("Вітаю!");

    expect(screen.queryByText(LOAD_FAILED)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: RETRY })).not.toBeInTheDocument();
    expect(sendButton()).toBeEnabled();
  });
});

// --- 7. Automatic retry, never in parallel ------------------------------------

describe("AssistantWidget automatic history retry (17.7h)", () => {
  it("test_opening_the_chat_after_a_failure_loads_the_history_again", async () => {
    const { user } = await mountWithFailedHistory();
    mockedHistory.mockResolvedValueOnce(OLD);

    await openChat(user);

    await shown("Вітаю!");
    expect(mockedHistory).toHaveBeenCalledTimes(2);
  });

  it("test_returning_to_the_tab_after_a_failure_loads_the_history_again", async () => {
    const { user } = await mountWithFailedHistory();
    // The open retry fails as well; the tab return then succeeds.
    mockedHistory.mockRejectedValueOnce(new TypeError("Failed to fetch"));
    await openChat(user);
    await failureBlockShown();
    mockedHistory.mockResolvedValueOnce(OLD);

    await returnToTab();

    await shown("Вітаю!");
    expect(screen.queryByText(LOAD_FAILED)).not.toBeInTheDocument();
  });

  it("test_opening_while_the_mount_request_runs_starts_no_second_request", async () => {
    storeConversation({ open: false });
    pendingHistory();
    const { user } = renderWidget();
    await waitFor(() => expect(mockedHistory).toHaveBeenCalledTimes(1));

    await openChat(user);
    await minimise(user);
    await openChat(user);
    await returnToTab();
    await tick();

    expect(mockedHistory).toHaveBeenCalledTimes(1);
  });

  it("test_triggers_while_a_retry_runs_start_no_second_request", async () => {
    const { user } = await mountWithFailedHistory();
    pendingHistory();
    await openChat(user);
    await waitFor(() => expect(mockedHistory).toHaveBeenCalledTimes(2));

    await minimise(user);
    await openChat(user);
    await returnToTab();
    await tick();

    expect(mockedHistory).toHaveBeenCalledTimes(2);
  });

  it("test_retry_button_while_a_retry_runs_starts_no_second_request", async () => {
    const result = await mountWithFailedHistory();
    mockedHistory.mockRejectedValueOnce(new TypeError("Failed to fetch"));
    await openChat(result.user);
    await failureBlockShown();
    pendingHistory();
    const retry = screen.getByRole("button", { name: RETRY });

    fireEvent.click(retry);
    fireEvent.click(retry);
    await returnToTab();
    await tick();

    expect(mockedHistory).toHaveBeenCalledTimes(3);
  });

  describe("restored open chat right after hydration", () => {
    let root: Root | null = null;
    let container: HTMLElement | null = null;

    afterEach(() => {
      if (root !== null) {
        const mounted = root;
        act(() => mounted.unmount());
        root = null;
      }
      container?.remove();
      container = null;
      cleanup();
    });

    async function hydrate(): Promise<void> {
      const target = document.createElement("div");
      target.innerHTML = renderToString(widget());
      document.body.appendChild(target);
      container = target;
      await act(async () => {
        root = hydrateRoot(target, widget());
      });
    }

    it("test_hydration_with_a_restored_open_chat_makes_one_request", async () => {
      storeConversation({ open: true });
      pendingHistory();

      await hydrate();
      // Precondition: the chat was restored open after hydration.
      expect(await screen.findByRole("dialog")).toBeInTheDocument();
      await tick();

      expect(mockedHistory).toHaveBeenCalledTimes(1);
    });

    it("test_hydration_with_a_restored_open_chat_loads_and_enables_sending", async () => {
      storeConversation({ open: true });
      const load = pendingHistory();
      await hydrate();
      await screen.findByRole("dialog");

      await act(async () => load.resolve(OLD));

      await shown("Вітаю!");
      expect(screen.getAllByText("Вітаю!")).toHaveLength(1);
      fireEvent.change(input(), { target: { value: DRAFT } });
      expect(sendButton()).toBeEnabled();
      expect(mockedHistory).toHaveBeenCalledTimes(1);
    });
  });
});

// --- 8. After a successful load ------------------------------------------------

describe("AssistantWidget after a successful history load (17.7h)", () => {
  it("test_open_and_tab_return_only_recheck_and_do_not_reload_the_history", async () => {
    storeConversation({ open: false });
    mockedHistory.mockResolvedValue(OLD);
    const { user } = renderWidget();
    await screen.findByTestId("assistant-live-dot");

    await openChat(user);
    await waitFor(() => expect(mockedHistory).toHaveBeenCalledTimes(2));
    await returnToTab();
    await waitFor(() => expect(mockedHistory).toHaveBeenCalledTimes(3));
    await tick();

    // The 17.7a expiry checks found the conversation alive; nothing is
    // added a second time.
    expect(items()).toEqual(["Привіт", "Вітаю!"]);
    expect(screen.queryByText(LOAD_FAILED)).not.toBeInTheDocument();
  });

  it("test_failed_expiry_recheck_after_a_successful_load_shows_no_failure_block", async () => {
    storeConversation({ open: false });
    mockedHistory.mockResolvedValueOnce(OLD);
    mockedHistory.mockRejectedValueOnce(new TypeError("Failed to fetch"));
    const { user } = renderWidget();
    await screen.findByTestId("assistant-live-dot");

    await openChat(user);
    await waitFor(() => expect(mockedHistory).toHaveBeenCalledTimes(2));
    await tick();

    expect(screen.queryByText(LOAD_FAILED)).not.toBeInTheDocument();
    await user.type(input(), DRAFT);
    expect(sendButton()).toBeEnabled();
  });
});

// --- 9. A successful retry runs the 17.7e rules -------------------------------

describe("AssistantWidget successful history retry and the pending text (17.7e via 17.7h)", () => {
  async function retryWith(history: AssistantHistoryMessage[]) {
    sessionStorage.setItem(PENDING_KEY, TEXT);
    const result = await mountWithFailedHistory();
    mockedHistory.mockRejectedValueOnce(new TypeError("Failed to fetch"));
    await openChat(result.user);
    await failureBlockShown();
    mockedHistory.mockResolvedValueOnce(history);
    await result.user.click(screen.getByRole("button", { name: RETRY }));
    await waitFor(() => expect(screen.queryByText(LOAD_FAILED)).not.toBeInTheDocument());
    await tick();
    return result;
  }

  it("test_retry_shows_a_pending_text_not_in_the_last_turn_with_retry", async () => {
    await retryWith(OLD);

    expect(items()).toEqual(["Привіт", "Вітаю!", TEXT, `${CUT_OFF}${RETRY}`]);
    expect(sessionStorage.getItem(PENDING_KEY)).toBe(TEXT);
  });

  it("test_retry_drops_a_pending_text_in_the_last_turn", async () => {
    await retryWith([
      ...OLD,
      { role: "user", content: TEXT },
      { role: "assistant", content: "У суботу є 10:00." },
    ]);

    expect(items()).toEqual(["Привіт", "Вітаю!", TEXT, "У суботу є 10:00."]);
    expect(screen.queryByText(CUT_OFF)).not.toBeInTheDocument();
    expect(sessionStorage.getItem(PENDING_KEY)).toBeNull();
  });

  it("test_retry_with_empty_history_moves_the_pending_text_into_an_empty_input", async () => {
    await retryWith([]);

    await waitFor(() => expect(input()).toHaveValue(TEXT));
    expect(sessionStorage.getItem(PENDING_KEY)).toBeNull();
    expect(sessionStorage.getItem(DRAFT_KEY)).toBe(TEXT);
    expect(screen.queryByText(CUT_OFF)).not.toBeInTheDocument();
  });

  it("test_open_trigger_retry_applies_the_same_rules_as_a_mount_load", async () => {
    sessionStorage.setItem(PENDING_KEY, TEXT);
    const { user } = await mountWithFailedHistory();
    mockedHistory.mockResolvedValueOnce(OLD);

    await openChat(user);

    await shown("Вітаю!");
    await waitFor(() => expect(items()).toEqual(["Привіт", "Вітаю!", TEXT, `${CUT_OFF}${RETRY}`]));
  });
});

// --- API: the signal reaches fetch -------------------------------------------

describe("getAssistantHistory forwards the abort signal (17.7h)", () => {
  it("test_signal_is_passed_to_fetch", async () => {
    const actual = await vi.importActual<typeof import("@/lib/assistant/assistantApi")>(
      "@/lib/assistant/assistantApi",
    );
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ messages: [] }), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    const controller = new AbortController();

    try {
      await (actual.getAssistantHistory as (...args: HistoryArgs) => Promise<unknown>)(
        "bella-demo",
        "stored-id",
        controller.signal,
      );
    } finally {
      vi.unstubAllGlobals();
    }

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const init = (fetchMock.mock.calls[0] as unknown as [string, RequestInit])[1];
    expect(init.signal).toBe(controller.signal);
  });
});
