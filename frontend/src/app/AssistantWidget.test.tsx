// @vitest-environment jsdom
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

// Mutable pathname read by the mocked usePathname, so a test can move the
// widget between allowed and excluded pages with rerender().
const nav = vi.hoisted(() => ({ pathname: "/services" }));

vi.mock("next/navigation", () => ({
  usePathname: () => nav.pathname,
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
}));

// Imported after the mock above so the mocked module is what the widget sees.
import type { AssistantEvent } from "@/lib/assistant/assistantApi";
import { getAssistantHistory, sendAssistantMessage } from "@/lib/assistant/assistantApi";
import { readSessionId } from "@/lib/assistant/sessionId";

import AssistantWidget from "./AssistantWidget";

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

    await sendAndFinish(user, "Привіт");

    const input = screen.getByRole("textbox", { name: INPUT });
    await waitFor(() => expect(input).toBeEnabled());
    expect(document.activeElement).toBe(input);
  });

  it("test_phone_focus_does_not_return_to_the_input_after_the_reply", async () => {
    viewportWidth = PHONE_WIDTH;
    const { user } = renderChatWidget();
    await openChat(user);

    await sendAndFinish(user, "Привіт");

    const input = screen.getByRole("textbox", { name: INPUT });
    await waitFor(() => expect(input).toBeEnabled());
    expect(document.activeElement).not.toBe(input);
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
});
