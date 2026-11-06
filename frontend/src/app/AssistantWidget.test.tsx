// @vitest-environment jsdom
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// Mutable pathname read by the mocked usePathname, so a test can move the
// widget between allowed and excluded pages with rerender().
// `search` and useSearchParams are read by NavigationProgress, which the
// 17.6e tests mount next to the widget as the root layout does.
const nav = vi.hoisted(() => ({ pathname: "/services", search: "" }));

vi.mock("next/navigation", () => ({
  usePathname: () => nav.pathname,
  useSearchParams: () => new URLSearchParams(nav.search),
}));

// 17.4b: the widget now holds a conversation. Its engine is mocked at the
// module boundary; the defaults (no stored id, empty history) make no
// request, so the 17.3c tests above see the same widget as before.
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

// Imported after the mock above so the mocked module is what the widget sees.
import type { AssistantEvent } from "@/lib/assistant/assistantApi";
import { getAssistantHistory, sendAssistantMessage } from "@/lib/assistant/assistantApi";
import { readSessionId, storeSessionId } from "@/lib/assistant/sessionId";
import { end } from "@/lib/navigation/pendingNavigation";

import AssistantWidget from "./AssistantWidget";
import NavigationProgress from "./NavigationProgress";

beforeEach(() => {
  vi.mocked(sendAssistantMessage).mockReset();
  vi.mocked(getAssistantHistory).mockReset();
  vi.mocked(readSessionId).mockReset();
  vi.mocked(readSessionId).mockReturnValue(null);
  vi.mocked(getAssistantHistory).mockResolvedValue([]);
});

// docs/DECISIONS.md § "Stage 17 sub-step order", 17.3c, and § "Stage 17
// block A" / "block B". Accessible names of the header buttons come from
// block B ("Розгорнути", "Зменшити", "Згорнути").
const STRIPE = "Відкрити асистента";
const MINIMISE = "Згорнути";
const TO_PANEL = "Розгорнути";
const TO_WINDOW = "Зменшити";
const PANEL_ATTRIBUTE = "data-assistant-panel";

const PHONE_WIDTH = 375;
const DESKTOP_WIDTH = 1280;

let viewportWidth = DESKTOP_WIDTH;

// jsdom has no matchMedia. Answer min-width / max-width queries against
// viewportWidth, so the widget can use any query form for the lg (1024 px)
// breakpoint.
function stubMatchMedia() {
  window.matchMedia = vi.fn((query: string) => {
    const min = /min-width:\s*(\d+)px/.exec(query);
    const max = /max-width:\s*(\d+(?:\.\d+)?)px/.exec(query);
    const matches =
      (min === null || viewportWidth >= Number(min[1])) &&
      (max === null || viewportWidth <= Number(max[1])) &&
      (min !== null || max !== null);
    return {
      matches,
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

function renderWidget(salonName = "Bella") {
  const user = userEvent.setup();
  const result = render(<AssistantWidget salonName={salonName} slug="bella-demo" />);
  return { user, ...result };
}

async function openChat(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole("button", { name: STRIPE }));
  return screen.getByRole("dialog");
}

const VIEWPORTS = [
  { name: "phone", width: PHONE_WIDTH },
  { name: "desktop", width: DESKTOP_WIDTH },
];

beforeEach(() => {
  nav.pathname = "/services";
  viewportWidth = DESKTOP_WIDTH;
  stubMatchMedia();
  document.documentElement.removeAttribute(PANEL_ATTRIBUTE);
  document.body.style.overflow = "";
});

describe("AssistantWidget visibility", () => {
  it("test_excluded_path_renders_nothing", () => {
    nav.pathname = "/login";

    const { container } = renderWidget();

    expect(container).toBeEmptyDOMElement();
  });

  it("test_allowed_path_renders_the_collapsed_stripe", () => {
    renderWidget();

    expect(screen.getByRole("button", { name: STRIPE })).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("test_starts_collapsed_after_a_full_reload", async () => {
    const { user, unmount } = renderWidget();
    await openChat(user);
    unmount();

    renderWidget();

    expect(screen.getByRole("button", { name: STRIPE })).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});

describe("AssistantWidget chat window", () => {
  it("test_stripe_opens_the_chat_with_the_salon_name_in_the_header", async () => {
    const { user } = renderWidget("Bella");

    const dialog = await openChat(user);

    expect(dialog).toContainElement(screen.getByRole("heading", { name: "Асистент Bella" }));
  });

  it("test_empty_salon_name_shows_plain_assistant_header", async () => {
    const { user } = renderWidget("");

    await openChat(user);

    expect(screen.getByRole("heading", { name: "Асистент" })).toBeInTheDocument();
  });

  it.each(VIEWPORTS)(
    "test_no_close_button_and_minimise_returns_to_the_stripe_($name)",
    async ({ width }) => {
      viewportWidth = width;
      const { user } = renderWidget();
      await openChat(user);

      expect(screen.queryByRole("button", { name: /Закрити/ })).not.toBeInTheDocument();
      await user.click(screen.getByRole("button", { name: MINIMISE }));

      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
      expect(screen.getByRole("button", { name: STRIPE })).toBeInTheDocument();
    },
  );

  it.each(VIEWPORTS)(
    "test_focus_moves_into_the_chat_and_back_to_the_stripe_($name)",
    async ({ width }) => {
      viewportWidth = width;
      const { user } = renderWidget();

      const dialog = await openChat(user);
      expect(dialog).toContainElement(document.activeElement as HTMLElement);

      await user.click(screen.getByRole("button", { name: MINIMISE }));
      expect(document.activeElement).toBe(screen.getByRole("button", { name: STRIPE }));
    },
  );

  it.each(VIEWPORTS)("test_escape_minimises_($name)", async ({ width }) => {
    viewportWidth = width;
    const { user } = renderWidget();
    await openChat(user);

    await user.keyboard("{Escape}");

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: STRIPE })).toBeInTheDocument();
  });
});

describe("AssistantWidget on a phone", () => {
  beforeEach(() => {
    viewportWidth = PHONE_WIDTH;
  });

  it("test_phone_chat_is_a_modal_dialog", async () => {
    const { user } = renderWidget();

    const dialog = await openChat(user);

    expect(dialog).toHaveAttribute("aria-modal", "true");
  });

  it("test_phone_tab_stays_inside_the_chat", async () => {
    const user = userEvent.setup();
    render(
      <>
        <button type="button">Кнопка сторінки</button>
        <AssistantWidget salonName="Bella" slug="bella-demo" />
        <a href="/services">Посилання сторінки</a>
      </>,
    );
    const dialog = await openChat(user);

    for (let i = 0; i < 4; i += 1) {
      await user.tab();
      expect(dialog).toContainElement(document.activeElement as HTMLElement);
    }
    for (let i = 0; i < 4; i += 1) {
      await user.tab({ shift: true });
      expect(dialog).toContainElement(document.activeElement as HTMLElement);
    }
  });

  it("test_phone_background_scroll_is_locked_while_open_and_restored", async () => {
    const { user } = renderWidget();

    await openChat(user);
    expect(document.body.style.overflow).toBe("hidden");

    await user.click(screen.getByRole("button", { name: MINIMISE }));
    expect(document.body.style.overflow).toBe("");
  });

  it("test_phone_has_no_resize_button", async () => {
    const { user } = renderWidget();

    await openChat(user);

    expect(screen.getByRole("button", { name: MINIMISE })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: TO_PANEL })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: TO_WINDOW })).not.toBeInTheDocument();
  });
});

describe("AssistantWidget on desktop", () => {
  it("test_desktop_window_is_non_modal_and_page_scrolls", async () => {
    const { user } = renderWidget();

    const dialog = await openChat(user);

    expect(dialog).not.toHaveAttribute("aria-modal", "true");
    expect(document.body.style.overflow).not.toBe("hidden");
  });

  it("test_resize_toggles_between_window_and_side_panel", async () => {
    const { user } = renderWidget();
    await openChat(user);

    await user.click(screen.getByRole("button", { name: TO_PANEL }));
    expect(screen.getByRole("button", { name: TO_WINDOW })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: TO_PANEL })).not.toBeInTheDocument();
    expect(screen.getByRole("dialog")).not.toHaveAttribute("aria-modal", "true");

    await user.click(screen.getByRole("button", { name: TO_WINDOW }));
    expect(screen.getByRole("button", { name: TO_PANEL })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: TO_WINDOW })).not.toBeInTheDocument();
  });
});

describe("AssistantWidget page shift", () => {
  async function openPanel(user: ReturnType<typeof userEvent.setup>) {
    await openChat(user);
    await user.click(screen.getByRole("button", { name: TO_PANEL }));
  }

  it("test_panel_sets_the_attribute_and_back_to_window_removes_it", async () => {
    const { user } = renderWidget();
    await openChat(user);
    expect(document.documentElement).not.toHaveAttribute(PANEL_ATTRIBUTE);

    await user.click(screen.getByRole("button", { name: TO_PANEL }));
    expect(document.documentElement).toHaveAttribute(PANEL_ATTRIBUTE);

    await user.click(screen.getByRole("button", { name: TO_WINDOW }));
    expect(document.documentElement).not.toHaveAttribute(PANEL_ATTRIBUTE);
  });

  it("test_minimise_from_the_panel_removes_the_attribute", async () => {
    const { user } = renderWidget();
    await openPanel(user);

    await user.click(screen.getByRole("button", { name: MINIMISE }));

    expect(document.documentElement).not.toHaveAttribute(PANEL_ATTRIBUTE);
  });

  it("test_excluded_path_removes_the_attribute_and_returning_restores_it", async () => {
    const { user, rerender } = renderWidget();
    await openPanel(user);

    nav.pathname = "/booking/pay";
    rerender(<AssistantWidget salonName="Bella" slug="bella-demo" />);
    expect(document.documentElement).not.toHaveAttribute(PANEL_ATTRIBUTE);

    nav.pathname = "/booking";
    rerender(<AssistantWidget salonName="Bella" slug="bella-demo" />);
    expect(document.documentElement).toHaveAttribute(PANEL_ATTRIBUTE);
  });

  it("test_unmount_removes_the_attribute", async () => {
    const { user, unmount } = renderWidget();
    await openPanel(user);

    unmount();

    expect(document.documentElement).not.toHaveAttribute(PANEL_ATTRIBUTE);
  });
});

describe("AssistantWidget across navigation", () => {
  it("test_state_is_kept_across_an_excluded_page", async () => {
    const { user, rerender, container } = renderWidget();
    await openChat(user);

    nav.pathname = "/client";
    rerender(<AssistantWidget salonName="Bella" slug="bella-demo" />);
    expect(container).toBeEmptyDOMElement();

    nav.pathname = "/specialists/12";
    rerender(<AssistantWidget salonName="Bella" slug="bella-demo" />);
    expect(screen.getByRole("dialog")).toContainElement(
      screen.getByRole("heading", { name: "Асистент Bella" }),
    );
  });
});

// docs/DECISIONS.md § "Stage 17 sub-step order", 17.4b, and § "Stage 17
// block A": the stripe dot means the conversation has at least one stored
// turn; the conversation survives minimise; opening focuses the input.
describe("AssistantWidget conversation", () => {
  const LIVE_DOT = "assistant-live-dot";
  const INPUT = "Повідомлення";

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
      push(...items: AssistantEvent[]) {
        buffer.push(...items);
        notify();
      },
      end() {
        ended = true;
        notify();
      },
    };
  }

  function renderChatWidget() {
    const user = userEvent.setup();
    return { user, ...render(<AssistantWidget salonName="Bella" slug="bella-demo" />) };
  }

  async function sendAndFinish(user: ReturnType<typeof userEvent.setup>, text: string) {
    const channel = eventChannel();
    vi.mocked(sendAssistantMessage).mockResolvedValueOnce({
      sessionId: "server-id",
      events: channel.events,
    });
    await user.type(screen.getByRole("textbox", { name: INPUT }), text);
    await user.click(screen.getByRole("button", { name: "Надіслати" }));
    channel.push({ type: "text", text: "Вітаю!" }, { type: "done" });
    channel.end();
    await screen.findByText("Вітаю!");
  }

  it("test_stripe_shows_the_dot_when_history_has_a_message", async () => {
    vi.mocked(readSessionId).mockReturnValue("stored-id");
    vi.mocked(getAssistantHistory).mockResolvedValueOnce([
      { role: "user", content: "Привіт" },
      { role: "assistant", content: "Вітаю!" },
    ]);

    renderChatWidget();

    const stripe = screen.getByRole("button", { name: STRIPE });
    expect(await within(stripe).findByTestId(LIVE_DOT)).toBeInTheDocument();
  });

  it("test_stripe_shows_the_dot_after_the_first_successful_reply", async () => {
    const { user } = renderChatWidget();
    expect(screen.queryByTestId(LIVE_DOT)).not.toBeInTheDocument();
    await openChat(user);

    await sendAndFinish(user, "Привіт");
    await user.click(screen.getByRole("button", { name: MINIMISE }));

    const stripe = screen.getByRole("button", { name: STRIPE });
    expect(within(stripe).getByTestId(LIVE_DOT)).toBeInTheDocument();
  });

  it("test_stripe_has_no_dot_without_a_conversation", async () => {
    renderChatWidget();

    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(screen.getByRole("button", { name: STRIPE })).toBeInTheDocument();
    expect(screen.queryByTestId(LIVE_DOT)).not.toBeInTheDocument();
  });

  it("test_opening_the_chat_focuses_the_message_input", async () => {
    const { user } = renderChatWidget();

    await openChat(user);

    expect(document.activeElement).toBe(screen.getByRole("textbox", { name: INPUT }));
  });

  // Decided 03.11.2026 (17.4b follow-up): after a reply finishes, focus
  // returns to the input on desktop only; on a phone the keyboard would
  // cover the reply.
  it("test_desktop_focus_returns_to_the_input_after_the_reply", async () => {
    viewportWidth = DESKTOP_WIDTH;
    const { user } = renderChatWidget();
    await openChat(user);
    const storedBefore = vi.mocked(storeSessionId).mock.calls.length;

    await sendAndFinish(user, "Привіт");

    const input = screen.getByRole("textbox", { name: INPUT });
    // The id is stored only at `done`, so this waits for the end of the turn.
    await waitFor(() => expect(storeSessionId).toHaveBeenCalledTimes(storedBefore + 1));
    expect(document.activeElement).toBe(input);
  });

  it("test_phone_focus_does_not_return_to_the_input_after_the_reply", async () => {
    viewportWidth = PHONE_WIDTH;
    const { user } = renderChatWidget();
    await openChat(user);
    const storedBefore = vi.mocked(storeSessionId).mock.calls.length;

    await sendAndFinish(user, "Привіт");

    const input = screen.getByRole("textbox", { name: INPUT });
    // The id is stored only at `done`, so this waits for the end of the turn.
    await waitFor(() => expect(storeSessionId).toHaveBeenCalledTimes(storedBefore + 1));
    expect(document.activeElement).not.toBe(input);
  });

  // docs/DECISIONS.md § "Stage 17 sub-step order", 17.7c, item of
  // 06.11.2026: on desktop focus stays in the input; on a phone the input
  // loses focus after sending (the keyboard closes) but stays enabled.
  describe("focus while a reply streams (17.7c)", () => {
    const LATER = "А в неділю?";

    // The draft lives in real sessionStorage; keep it from leaking.
    afterEach(() => sessionStorage.clear());

    async function startTurn(user: ReturnType<typeof userEvent.setup>, how: "button" | "enter") {
      const channel = eventChannel();
      vi.mocked(sendAssistantMessage).mockResolvedValueOnce({
        sessionId: "server-id",
        events: channel.events,
      });
      await openChat(user);
      const input = screen.getByRole("textbox", { name: INPUT });
      await user.type(input, "Привіт");
      if (how === "button") {
        await user.click(screen.getByRole("button", { name: "Надіслати" }));
      } else {
        await user.keyboard("{Enter}");
      }
      return { channel, input };
    }

    it("test_desktop_focus_stays_in_the_input_after_the_click_and_after_the_turn", async () => {
      viewportWidth = DESKTOP_WIDTH;
      const { user } = renderChatWidget();
      const { channel, input } = await startTurn(user, "button");

      expect(document.activeElement).toBe(input);

      const storedBefore = vi.mocked(storeSessionId).mock.calls.length;
      channel.push({ type: "text", text: "Вітаю!" }, { type: "done" });
      channel.end();
      await screen.findByText("Вітаю!");
      await waitFor(() => expect(storeSessionId).toHaveBeenCalledTimes(storedBefore + 1));

      expect(document.activeElement).toBe(input);
    });

    it("test_desktop_focus_stays_in_the_input_after_enter_and_after_the_turn", async () => {
      viewportWidth = DESKTOP_WIDTH;
      const { user } = renderChatWidget();
      const { channel, input } = await startTurn(user, "enter");

      expect(document.activeElement).toBe(input);

      const storedBefore = vi.mocked(storeSessionId).mock.calls.length;
      channel.push({ type: "text", text: "Вітаю!" }, { type: "done" });
      channel.end();
      await screen.findByText("Вітаю!");
      await waitFor(() => expect(storeSessionId).toHaveBeenCalledTimes(storedBefore + 1));

      expect(document.activeElement).toBe(input);
    });

    it.each(["button", "enter"] as const)(
      "test_phone_input_is_unfocused_after_sending_and_accepts_typing_after_a_tap_while_streaming_(%s)",
      async (how) => {
        viewportWidth = PHONE_WIDTH;
        const { user } = renderChatWidget();
        const { input } = await startTurn(user, how);

        expect(document.activeElement).not.toBe(input);
        expect(input).toBeEnabled();

        await user.click(input);
        expect(document.activeElement).toBe(input);
        await user.keyboard(LATER);

        expect(input).toHaveValue(LATER);
        expect(screen.queryByText("Вітаю!")).not.toBeInTheDocument();
      },
    );
  });

  // docs/DECISIONS.md § "Stage 17 sub-step order", 17.6a, and block B:
  // after a card click the phone chat minimises so the booking form shows,
  // without moving focus to the stripe; desktop stays as it was.
  async function openCardReply(user: ReturnType<typeof userEvent.setup>) {
    const channel = eventChannel();
    vi.mocked(sendAssistantMessage).mockResolvedValueOnce({
      sessionId: "server-id",
      events: channel.events,
    });
    await openChat(user);
    await user.type(screen.getByRole("textbox", { name: INPUT }), "Запишіть мене");
    await user.click(screen.getByRole("button", { name: "Надіслати" }));
    channel.push(
      {
        type: "proposal",
        card: {
          service: { id: 7, name: "Манікюр з покриттям", duration_minutes: 60 },
          specialist: "any",
          price: "500.00",
          deposit: "100.00",
          due_at_visit: "400.00",
          currency: "UAH",
          slots: [],
        },
      },
      { type: "done" },
    );
    channel.end();
    return screen.findByRole("link", { name: "Обрати час" });
  }

  // No NavigationProgress is mounted here, so no navigation is pending at
  // the click: this covers the "nothing pending, minimise at once" path
  // (17.6e). The wait for a pending navigation is covered in "after a card
  // click (17.6e)" below.
  it("test_phone_card_click_with_no_pending_navigation_minimises_at_once_without_focusing_the_stripe", async () => {
    viewportWidth = PHONE_WIDTH;
    const { user } = renderChatWidget();
    const link = await openCardReply(user);

    await user.click(link);

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    const stripe = screen.getByRole("button", { name: STRIPE });
    expect(document.activeElement).not.toBe(stripe);
  });

  it("test_desktop_card_click_keeps_the_chat_open", async () => {
    viewportWidth = DESKTOP_WIDTH;
    const { user } = renderChatWidget();
    const link = await openCardReply(user);

    await user.click(link);

    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  it("test_messages_survive_minimise_and_reopen", async () => {
    const { user } = renderChatWidget();
    await openChat(user);
    await sendAndFinish(user, "Скільки коштує манікюр?");

    await user.click(screen.getByRole("button", { name: MINIMISE }));
    await openChat(user);

    expect(screen.getByText("Скільки коштує манікюр?")).toBeInTheDocument();
    expect(screen.getByText("Вітаю!")).toBeInTheDocument();
  });

  // docs/DECISIONS.md § "Stage 17 sub-step order", 17.6e: on a phone a card
  // click keeps the chat open until the navigation ends (the URL changes, or
  // the 10 s safety timeout), then minimises it without focusing the
  // stripe; a card link to the current URL minimises at once. Desktop is
  // unchanged. NavigationProgress is mounted next to the widget, as in the
  // root layout. The chat is set up with real timers (userEvent), then fake
  // timers are switched on before the click, so every timer the click
  // schedules is on the fake clock; fireEvent from then on (see CLAUDE.md).
  describe("after a card click (17.6e)", () => {
    beforeEach(() => {
      nav.search = "";
      window.history.replaceState(null, "", "/services");
    });

    afterEach(() => {
      act(() => {
        end();
      });
      vi.useRealTimers();
    });

    function ui() {
      return (
        <>
          <NavigationProgress />
          <AssistantWidget salonName="Bella" slug="bella-demo" />
        </>
      );
    }

    function renderWithProgress() {
      const user = userEvent.setup();
      const result = render(ui());
      return { user, rerenderAll: () => result.rerender(ui()) };
    }

    function advance(ms: number) {
      act(() => {
        vi.advanceTimersByTime(ms);
      });
    }

    function changeUrl(rerenderAll: () => void) {
      nav.pathname = "/booking";
      nav.search = "entry=service&service=7&specialist=any";
      rerenderAll();
    }

    it("test_phone_card_click_keeps_the_chat_open_while_the_url_is_unchanged", async () => {
      viewportWidth = PHONE_WIDTH;
      const { user } = renderWithProgress();
      const link = await openCardReply(user);
      vi.useFakeTimers();

      fireEvent.click(link);
      advance(5_000);

      expect(screen.getByRole("dialog")).toBeInTheDocument();
    });

    it("test_phone_url_change_after_a_card_click_minimises_without_focusing_the_stripe", async () => {
      viewportWidth = PHONE_WIDTH;
      const { user, rerenderAll } = renderWithProgress();
      const link = await openCardReply(user);
      vi.useFakeTimers();

      fireEvent.click(link);
      expect(screen.getByRole("dialog")).toBeInTheDocument();
      changeUrl(rerenderAll);

      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
      const stripe = screen.getByRole("button", { name: STRIPE });
      expect(document.activeElement).not.toBe(stripe);
    });

    it("test_phone_search_params_change_after_a_card_click_minimises", async () => {
      viewportWidth = PHONE_WIDTH;
      const { user, rerenderAll } = renderWithProgress();
      const link = await openCardReply(user);
      vi.useFakeTimers();

      fireEvent.click(link);
      expect(screen.getByRole("dialog")).toBeInTheDocument();
      nav.search = "entry=service";
      rerenderAll();

      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    });

    it("test_phone_card_link_to_the_current_url_minimises_at_once", async () => {
      viewportWidth = PHONE_WIDTH;
      const { user } = renderWithProgress();
      const link = await openCardReply(user);
      window.history.replaceState(null, "", link.getAttribute("href"));
      vi.useFakeTimers();

      fireEvent.click(link);

      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    });

    it("test_phone_card_click_minimises_after_the_10_s_safety_timeout", async () => {
      viewportWidth = PHONE_WIDTH;
      const { user } = renderWithProgress();
      const link = await openCardReply(user);
      vi.useFakeTimers();

      fireEvent.click(link);
      advance(9_999);
      expect(screen.getByRole("dialog")).toBeInTheDocument();

      advance(1);
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    });

    // Decided 05.11.2026: a manual minimise or reopen while the navigation is
    // pending cancels the waiting minimise.
    it("test_phone_manual_minimise_and_reopen_cancel_the_waiting_minimise", async () => {
      viewportWidth = PHONE_WIDTH;
      const { user, rerenderAll } = renderWithProgress();
      const link = await openCardReply(user);
      vi.useFakeTimers();

      fireEvent.click(link);
      fireEvent.click(screen.getByRole("button", { name: MINIMISE }));
      fireEvent.click(screen.getByRole("button", { name: STRIPE }));
      expect(screen.getByRole("dialog")).toBeInTheDocument();
      changeUrl(rerenderAll);

      expect(screen.getByRole("dialog")).toBeInTheDocument();
    });

    // Decided 05.11.2026: typing in the input while the navigation is
    // pending cancels the waiting minimise, so a customer who starts writing
    // is not interrupted.
    it("test_phone_typing_while_pending_cancels_the_waiting_minimise", async () => {
      viewportWidth = PHONE_WIDTH;
      const { user, rerenderAll } = renderWithProgress();
      const link = await openCardReply(user);
      vi.useFakeTimers();

      fireEvent.click(link);
      fireEvent.input(screen.getByRole("textbox", { name: INPUT }), {
        target: { value: "А ще питання" },
      });
      changeUrl(rerenderAll);

      expect(screen.getByRole("dialog")).toBeInTheDocument();
      expect(screen.getByRole("textbox", { name: INPUT })).toHaveValue("А ще питання");
    });

    it("test_desktop_card_click_leaves_the_chat_open_after_the_url_changes", async () => {
      viewportWidth = DESKTOP_WIDTH;
      const { user, rerenderAll } = renderWithProgress();
      const link = await openCardReply(user);
      vi.useFakeTimers();

      fireEvent.click(link);
      changeUrl(rerenderAll);

      expect(screen.getByRole("dialog")).toBeInTheDocument();
    });
  });
});

// docs/DECISIONS.md § "Stage 17 sub-step order", 17.7c, AI notice item of
// 06.11.2026, and § "Stage 17 block D" (AI notice).
describe("AssistantWidget AI notice (17.7c)", () => {
  const NOTICE = "ШІ може помилятися. Перевіряйте важливу інформацію.";
  const INPUT = "Повідомлення";
  // 17.7c, changed 06.11.2026: the sentence alone is the link.
  const HIDDEN_PART = "Як ми обробляємо дані, відкривається в новій вкладці";

  function isBefore(first: Element, second: Element): boolean {
    return Boolean(first.compareDocumentPosition(second) & Node.DOCUMENT_POSITION_FOLLOWING);
  }

  /** The notice: the link whose accessible name starts with the sentence. */
  function noticeLink(): HTMLElement {
    return screen.getByRole("link", { name: (name) => name.startsWith(NOTICE) });
  }

  /** The element's text without its visually hidden (sr-only) parts. */
  function visibleText(element: Element): string {
    const copy = element.cloneNode(true) as Element;
    copy.querySelectorAll(".sr-only").forEach((hidden) => hidden.remove());
    return (copy.textContent ?? "").replace(/\s+/g, " ").trim();
  }

  it.each(VIEWPORTS)(
    "test_notice_comes_after_the_input_form_($name)",
    async ({ width }) => {
      viewportWidth = width;
      vi.mocked(readSessionId).mockReturnValue("stored-id");
      // Live on every request, so reopening finds no expiry (17.7a).
      vi.mocked(getAssistantHistory).mockResolvedValue([
        { role: "user", content: "Привіт" },
        { role: "assistant", content: "Вітаю!" },
      ]);
      const { user } = renderWidget();
      await openChat(user);
      await screen.findByText("Вітаю!");

      const list = screen.getByRole("list", { name: "Розмова" });
      const notice = noticeLink();
      const input = screen.getByRole("textbox", { name: INPUT });
      expect(notice).toBeVisible();
      expect(screen.getByRole("dialog")).toContainElement(notice);
      const form = input.closest("form") as HTMLFormElement;
      expect(list).not.toContainElement(notice);
      expect(form).not.toContainElement(notice);
      expect(isBefore(list, notice)).toBe(true);
      expect(isBefore(form, notice)).toBe(true);
    },
  );

  it("test_notice_link_opens_the_privacy_page_in_a_new_tab", async () => {
    const { user } = renderWidget();
    await openChat(user);

    const link = noticeLink();
    expect(visibleText(link)).toBe(NOTICE);
    expect(link).toHaveAttribute("href", "/privacy");
    expect(link).toHaveAttribute("target", "_blank");
    expect(link.getAttribute("rel") ?? "").toMatch(/(^|\s)noopener(\s|$)/);
  });

  it("test_notice_name_adds_the_hidden_destination", async () => {
    const { user } = renderWidget();
    await openChat(user);

    const link = noticeLink();
    expect(link).toHaveAccessibleName(expect.stringContaining(NOTICE));
    expect(link).toHaveAccessibleName(expect.stringContaining(HIDDEN_PART));
    expect(within(link).getByText(HIDDEN_PART)).toHaveClass("sr-only");
  });

  it("test_no_visible_how_we_process_data_text_in_the_chat", async () => {
    const { user } = renderWidget();
    const dialog = await openChat(user);

    expect(visibleText(dialog)).not.toContain("Як ми обробляємо дані");
  });
});

// docs/DECISIONS.md § "Stage 17 block B" (header buttons) and § "Stage 17
// sub-step order", 17.7c (decided 06.11.2026): the native `title` is the
// desktop hover tooltip, equal to the aria-label; built in 17.3c.
describe("AssistantWidget header tooltips (17.7c)", () => {
  function expectTitleEqualsLabel(button: HTMLElement) {
    expect(button).toHaveAttribute("title", button.getAttribute("aria-label") ?? "");
  }

  it("test_desktop_header_buttons_have_a_title_equal_to_their_label", async () => {
    viewportWidth = DESKTOP_WIDTH;
    const { user } = renderWidget();
    await openChat(user);

    expectTitleEqualsLabel(screen.getByRole("button", { name: TO_PANEL }));
    expectTitleEqualsLabel(screen.getByRole("button", { name: MINIMISE }));

    await user.click(screen.getByRole("button", { name: TO_PANEL }));

    expectTitleEqualsLabel(screen.getByRole("button", { name: TO_WINDOW }));
    expectTitleEqualsLabel(screen.getByRole("button", { name: MINIMISE }));
  });

  it("test_phone_minimise_button_has_no_title", async () => {
    viewportWidth = PHONE_WIDTH;
    const { user } = renderWidget();
    await openChat(user);

    expect(screen.getByRole("button", { name: MINIMISE })).not.toHaveAttribute("title");
  });
});
