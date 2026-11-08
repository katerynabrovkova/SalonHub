// @vitest-environment jsdom
import { act, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useEffect } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// docs/DECISIONS.md § "Stage 17 sub-step order", 17.7i (decided 07.11.2026):
// part 1, the scroll to the end runs before paint (a layout effect), never
// smooth; part 2, the list follows new content only while she is within
// about 80 px of the end; a "↓" button ("Прокрутити донизу") shows while she
// is not, and its tap scrolls smoothly to the end and resumes following;
// sending always scrolls to the end and resumes following; only the "↓" tap
// is smooth.
//
// jsdom has no layout. The message list (the scroll container holding the
// greeting) gets stubbed scrollHeight, clientHeight and scrollTop. The tests
// accept either scroll mechanism: writing the list's scrollTop, or calling
// scrollIntoView / scrollTo; each stub moves the stubbed position the way a
// browser would and, like a browser, fires a `scroll` event afterwards when
// the position changed. Expected for GREEN: automatic scrolls write
// `list.scrollTop = list.scrollHeight` in a layout effect; the "↓" tap calls
// `list.scrollTo({ top: list.scrollHeight, behavior: "smooth" })`.

vi.mock("@/lib/assistant/assistantApi", () => ({
  sendAssistantMessage: vi.fn(),
  getAssistantHistory: vi.fn(),
}));

// Imported after the mock above so the mocked module is what the hook sees.
import type { AssistantEvent, AssistantHistoryMessage } from "@/lib/assistant/assistantApi";
import { getAssistantHistory, sendAssistantMessage } from "@/lib/assistant/assistantApi";

import AssistantChat, { useAssistantConversation } from "./AssistantChat";

const SESSION_KEY = "salonhub:assistant-session";
const INPUT = "Повідомлення";
const SEND = "Надіслати";
const TO_BOTTOM = "Прокрутити донизу";
const OLD: AssistantHistoryMessage[] = [
  { role: "user", content: "Привіт" },
  { role: "assistant", content: "Вітаю!" },
];

const mockedHistory = vi.mocked(getAssistantHistory);
const mockedSend = vi.mocked(sendAssistantMessage);

// --- Layout stub ------------------------------------------------------------

interface Layout {
  scrollHeight: number;
  clientHeight: number;
  scrollTop: number;
}

/** Programmatic scrolls of the list, in order, with their behaviour. */
let scrolls: { via: string; behavior: string }[] = [];
/** Ordering log for part 1: "scroll" (any programmatic scroll), "passive". */
let order: string[] = [];
let list: HTMLElement | null = null;
let layout: Layout = { scrollHeight: 0, clientHeight: 0, scrollTop: 0 };

function maxScrollTop() {
  return Math.max(0, layout.scrollHeight - layout.clientHeight);
}

function distanceFromEnd() {
  return layout.scrollHeight - layout.clientHeight - layout.scrollTop;
}

/** A programmatic scroll to `top`: clamped, logged, then a `scroll` event. */
function programmaticScroll(via: string, top: number, behavior: string) {
  const before = layout.scrollTop;
  layout.scrollTop = Math.min(Math.max(0, top), maxScrollTop());
  scrolls.push({ via, behavior });
  order.push("scroll");
  const target = list;
  if (target !== null && layout.scrollTop !== before) {
    setTimeout(() => target.dispatchEvent(new Event("scroll")), 0);
  }
}

function behaviorOf(arg: unknown): string {
  if (typeof arg === "object" && arg !== null && "behavior" in arg) {
    return String((arg as { behavior?: unknown }).behavior ?? "auto");
  }
  return "auto";
}

/** The scroll container, found by its test id (the greeting hides while history loads). */
function findList(): HTMLElement {
  return screen.getByTestId("assistant-message-list");
}

/** Stubs the list's layout; the list starts at the bottom unless `scrollTop` is given. */
function stubLayout(initial: { scrollHeight: number; clientHeight: number; scrollTop?: number }) {
  const element = findList();
  list = element;
  layout = {
    scrollHeight: initial.scrollHeight,
    clientHeight: initial.clientHeight,
    scrollTop: initial.scrollTop ?? initial.scrollHeight - initial.clientHeight,
  };
  Object.defineProperty(element, "scrollHeight", {
    configurable: true,
    get: () => layout.scrollHeight,
  });
  Object.defineProperty(element, "clientHeight", {
    configurable: true,
    get: () => layout.clientHeight,
  });
  Object.defineProperty(element, "scrollTop", {
    configurable: true,
    get: () => layout.scrollTop,
    set: (value: number) => programmaticScroll("scrollTop", value, "auto"),
  });
  Object.defineProperty(element, "scrollTo", {
    configurable: true,
    value: (arg: unknown, y?: number) => {
      const top =
        typeof arg === "object" && arg !== null
          ? ((arg as ScrollToOptions).top ?? layout.scrollTop)
          : (y ?? layout.scrollTop);
      programmaticScroll("scrollTo", top, behaviorOf(arg));
    },
  });
}

/** She scrolls by hand to `distance` px above the end. */
async function userScrollToDistance(distance: number) {
  layout.scrollTop = Math.max(0, maxScrollTop() - distance);
  await act(async () => {
    fireEvent.scroll(list as HTMLElement);
  });
}

// --- Harness ----------------------------------------------------------------

/** Logs "passive" in its passive effect, which runs before AssistantChat's (tree order). */
function Probe({ dep }: { dep: unknown }) {
  useEffect(() => {
    order.push("passive");
  }, [dep]);
  return null;
}

function Harness() {
  const conversation = useAssistantConversation("bella-demo", true);
  return (
    <>
      <Probe dep={conversation.messages} />
      <AssistantChat conversation={conversation} />
    </>
  );
}

function tick() {
  return new Promise((resolve) => setTimeout(resolve, 0));
}

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

type Channel = ReturnType<typeof eventChannel>;

/** New content arrives: the list grows by 100 px, then the event lands. */
async function arrive(channel: Channel, ...events: AssistantEvent[]) {
  layout.scrollHeight += 100;
  await act(async () => {
    channel.push(...events);
    await tick();
    await tick();
  });
}

const UPDATES: { name: string; events: AssistantEvent[] }[] = [
  { name: "text", events: [{ type: "text", text: " Ще трохи тексту." }] },
  { name: "status", events: [{ type: "status", step: "check_availability" }] },
  { name: "failure_block", events: [{ type: "error", code: "internal_error" }] },
];

/**
 * No stored id (sends at once): sends a message, and the reply starts
 * streaming and stays open. The list is stubbed tall and at the bottom.
 */
async function startStreaming() {
  const user = userEvent.setup();
  render(<Harness />);
  stubLayout({ scrollHeight: 1000, clientHeight: 400 });
  const channel = eventChannel();
  mockedSend.mockResolvedValueOnce({ sessionId: "server-id", events: channel.events });
  await user.type(screen.getByRole("textbox", { name: INPUT }), "Привіт");
  await user.click(screen.getByRole("button", { name: SEND }));
  await arrive(channel, { type: "text", text: "Вітаю!" });
  // Precondition: at the bottom once the first reply text is in.
  expect(distanceFromEnd()).toBe(0);
  return { user, channel };
}

beforeEach(() => {
  sessionStorage.clear();
  mockedHistory.mockReset();
  mockedSend.mockReset();
  mockedHistory.mockResolvedValue([]);
  scrolls = [];
  order = [];
  list = null;
  // jsdom has no scrollIntoView. A browser-like stub: an element inside the
  // list is brought to the list's end (block "end" of the last element).
  Element.prototype.scrollIntoView = function scrollIntoView(arg?: boolean | ScrollIntoViewOptions) {
    if (list !== null && list.contains(this)) {
      programmaticScroll("scrollIntoView", layout.scrollHeight, behaviorOf(arg));
    }
  };
});

afterEach(() => {
  delete (Element.prototype as Partial<Element>).scrollIntoView;
  vi.restoreAllMocks();
});

// --- Part 1 -------------------------------------------------------------------

describe("AssistantChat scrolls to the end before paint (17.7i part 1)", () => {
  async function loadHistoryIntoAnOpenChat() {
    sessionStorage.setItem(SESSION_KEY, "stored-id");
    let resolve: (messages: AssistantHistoryMessage[]) => void = () => {};
    mockedHistory.mockImplementationOnce(
      () =>
        new Promise<AssistantHistoryMessage[]>((done) => {
          resolve = done;
        }),
    );
    render(<Harness />);
    // The list exists before the history arrives and starts at its top.
    stubLayout({ scrollHeight: 400, clientHeight: 400, scrollTop: 0 });
    order = [];
    scrolls = [];
    layout.scrollHeight = 1200;
    await act(async () => {
      resolve(OLD);
      await tick();
    });
  }

  it("test_history_arrival_scrolls_in_the_layout_phase_before_passive_effects", async () => {
    await loadHistoryIntoAnOpenChat();

    expect(screen.getByText("Привіт")).toBeInTheDocument();
    expect(order.slice(0, 2)).toEqual(["scroll", "passive"]);
  });

  it("test_history_arrival_ends_at_the_end_of_the_list", async () => {
    await loadHistoryIntoAnOpenChat();

    expect(distanceFromEnd()).toBe(0);
  });

  it("test_history_arrival_scroll_is_not_smooth", async () => {
    await loadHistoryIntoAnOpenChat();

    expect(scrolls.length).toBeGreaterThan(0);
    expect(scrolls.filter((scroll) => scroll.behavior === "smooth")).toEqual([]);
  });

  it("test_streamed_text_scrolls_in_the_layout_phase_before_passive_effects", async () => {
    const { channel } = await startStreaming();
    order = [];

    await arrive(channel, { type: "text", text: " Ще трохи тексту." });

    expect(order.slice(0, 2)).toEqual(["scroll", "passive"]);
  });
});

// --- Part 2: following at the bottom -----------------------------------------

describe("AssistantChat follows new content at the bottom (17.7i part 2)", () => {
  it.each(UPDATES)("test_update_at_the_bottom_keeps_the_end_in_view_($name)", async ({ events }) => {
    const { channel } = await startStreaming();

    await arrive(channel, ...events);

    expect(distanceFromEnd()).toBe(0);
  });

  it.each([
    { name: "0px", distance: 0 },
    { name: "40px", distance: 40 },
    { name: "70px", distance: 70 },
  ])("test_within_about_80px_counts_as_the_bottom_($name)", async ({ distance }) => {
    const { channel } = await startStreaming();
    await userScrollToDistance(distance);

    await arrive(channel, { type: "text", text: " Ще трохи тексту." });

    expect(distanceFromEnd()).toBe(0);
    expect(screen.queryByRole("button", { name: TO_BOTTOM })).not.toBeInTheDocument();
  });

  it.each([
    { name: "100px", distance: 100 },
    { name: "300px", distance: 300 },
  ])("test_beyond_about_80px_is_not_the_bottom_($name)", async ({ distance }) => {
    const { channel } = await startStreaming();
    await userScrollToDistance(distance);
    const before = layout.scrollTop;

    await arrive(channel, { type: "text", text: " Ще трохи тексту." });

    expect(layout.scrollTop).toBe(before);
  });
});

// --- Part 2: not following after she scrolls up ------------------------------

describe("AssistantChat stops following after she scrolls up (17.7i part 2)", () => {
  it.each(UPDATES)("test_update_after_scrolling_up_does_not_scroll_($name)", async ({ events }) => {
    const { channel } = await startStreaming();
    await userScrollToDistance(300);
    const before = layout.scrollTop;
    scrolls = [];

    await arrive(channel, ...events);

    expect(layout.scrollTop).toBe(before);
    expect(scrolls).toEqual([]);
  });

  it("test_several_updates_after_scrolling_up_do_not_scroll", async () => {
    const { channel } = await startStreaming();
    await userScrollToDistance(300);
    const before = layout.scrollTop;

    await arrive(channel, { type: "status", step: "check_availability" });
    await arrive(channel, { type: "text", text: " Перевіряю." });
    await arrive(channel, { type: "text", text: " Ще трохи." });

    expect(layout.scrollTop).toBe(before);
  });

  it("test_scrolling_back_to_the_bottom_by_hand_resumes_following", async () => {
    const { channel } = await startStreaming();
    await userScrollToDistance(300);
    await userScrollToDistance(0);

    await arrive(channel, { type: "text", text: " Ще трохи тексту." });

    expect(distanceFromEnd()).toBe(0);
  });
});

// --- Part 2: the "↓" button ----------------------------------------------------

describe('AssistantChat "↓" button (17.7i part 2)', () => {
  it("test_button_is_absent_at_the_bottom", async () => {
    await startStreaming();

    expect(screen.queryByRole("button", { name: TO_BOTTOM })).not.toBeInTheDocument();
  });

  it("test_button_appears_after_scrolling_up", async () => {
    await startStreaming();

    await userScrollToDistance(300);

    expect(screen.queryByRole("button", { name: TO_BOTTOM })).toBeInTheDocument();
  });

  it("test_button_goes_after_scrolling_back_to_the_bottom_by_hand", async () => {
    await startStreaming();
    await userScrollToDistance(300);

    await userScrollToDistance(0);

    expect(screen.queryByRole("button", { name: TO_BOTTOM })).not.toBeInTheDocument();
  });

  async function tapToBottom(user: ReturnType<typeof userEvent.setup>) {
    const button = screen.queryByRole("button", { name: TO_BOTTOM });
    expect(button).toBeInTheDocument();
    await user.click(button as HTMLElement);
    await act(async () => {
      await tick();
    });
  }

  it("test_tap_scrolls_smoothly_to_the_end", async () => {
    const { user } = await startStreaming();
    await userScrollToDistance(300);
    scrolls = [];

    await tapToBottom(user);

    expect(distanceFromEnd()).toBe(0);
    expect(scrolls.map((scroll) => scroll.behavior)).toEqual(["smooth"]);
  });

  it("test_tap_hides_the_button", async () => {
    const { user } = await startStreaming();
    await userScrollToDistance(300);

    await tapToBottom(user);

    expect(screen.queryByRole("button", { name: TO_BOTTOM })).not.toBeInTheDocument();
  });

  // Decided 07.11.2026: only an upward scroll that ends more than about
  // 80 px from the end leaves the bottom, so the smooth "↓" animation (its
  // scroll events move downward) neither shows the button nor stops
  // following mid-way.
  it("test_downward_scroll_events_of_the_tap_animation_keep_following", async () => {
    const { user, channel } = await startStreaming();
    await userScrollToDistance(300);
    // The smooth scroll is still animating: scrollTo starts it but does not
    // move the list yet; the animation's own scroll events follow.
    Object.defineProperty(list as HTMLElement, "scrollTo", {
      configurable: true,
      value: (arg: unknown) => {
        scrolls.push({ via: "scrollTo", behavior: behaviorOf(arg) });
      },
    });
    await tapToBottom(user);

    await userScrollToDistance(200);
    await userScrollToDistance(120);

    expect(screen.queryByRole("button", { name: TO_BOTTOM })).not.toBeInTheDocument();
    await arrive(channel, { type: "text", text: " Ще трохи тексту." });
    expect(distanceFromEnd()).toBe(0);
  });

  it("test_tap_resumes_following_for_the_next_update", async () => {
    const { user, channel } = await startStreaming();
    await userScrollToDistance(300);
    await tapToBottom(user);
    scrolls = [];

    await arrive(channel, { type: "text", text: " Ще трохи тексту." });

    expect(distanceFromEnd()).toBe(0);
    expect(scrolls.filter((scroll) => scroll.behavior === "smooth")).toEqual([]);
  });
});

// --- Part 2: sending ---------------------------------------------------------------

describe("AssistantChat sending scrolls to the end (17.7i part 2)", () => {
  async function sendWhileScrolledUp() {
    const { user, channel } = await startStreaming();
    await arrive(channel, { type: "done" });
    channel.end();
    await userScrollToDistance(300);
    const next = eventChannel();
    mockedSend.mockResolvedValueOnce({ sessionId: "server-id", events: next.events });
    scrolls = [];
    layout.scrollHeight += 100;
    await user.type(screen.getByRole("textbox", { name: INPUT }), "А в неділю?");
    await user.click(screen.getByRole("button", { name: SEND }));
    await act(async () => {
      await tick();
    });
    return next;
  }

  it("test_send_while_scrolled_up_scrolls_to_the_end", async () => {
    await sendWhileScrolledUp();

    expect(screen.getByText("А в неділю?")).toBeInTheDocument();
    expect(distanceFromEnd()).toBe(0);
    expect(screen.queryByRole("button", { name: TO_BOTTOM })).not.toBeInTheDocument();
  });

  it("test_send_while_scrolled_up_resumes_following", async () => {
    const next = await sendWhileScrolledUp();

    await arrive(next, { type: "text", text: "У неділю є 12:00." });

    expect(distanceFromEnd()).toBe(0);
  });
});

// --- "Повторити" resumes following (decided 07.11.2026) ----------------------------

describe('AssistantChat "Повторити" scrolls to the end (17.7i part 2)', () => {
  const RETRY = "Повторити";

  /** A turn fails with retry offered, then she scrolls up and taps "Повторити". */
  async function retryTurnWhileScrolledUp() {
    const { user, channel } = await startStreaming();
    await arrive(channel, { type: "error", code: "internal_error" });
    await userScrollToDistance(300);
    const next = eventChannel();
    mockedSend.mockResolvedValueOnce({ sessionId: "server-id", events: next.events });
    layout.scrollHeight += 100;
    await user.click(screen.getByRole("button", { name: RETRY }));
    await act(async () => {
      await tick();
    });
    return next;
  }

  it("test_turn_retry_while_scrolled_up_scrolls_to_the_end", async () => {
    await retryTurnWhileScrolledUp();

    expect(distanceFromEnd()).toBe(0);
    expect(screen.queryByRole("button", { name: TO_BOTTOM })).not.toBeInTheDocument();
  });

  it("test_turn_retry_while_scrolled_up_resumes_following", async () => {
    const next = await retryTurnWhileScrolledUp();

    await arrive(next, { type: "text", text: "Тепер вийшло." });

    expect(distanceFromEnd()).toBe(0);
  });

  /** The history load fails, she scrolls up and taps "Повторити" (17.7h). */
  async function retryHistoryWhileScrolledUp() {
    sessionStorage.setItem(SESSION_KEY, "stored-id");
    mockedHistory.mockRejectedValueOnce(new TypeError("Failed to fetch"));
    const user = userEvent.setup();
    render(<Harness />);
    await act(async () => {
      await tick();
    });
    stubLayout({ scrollHeight: 1000, clientHeight: 400 });
    // At the bottom first, so the next scroll is an upward one.
    await userScrollToDistance(0);
    await userScrollToDistance(300);
    // Precondition: she has left the bottom.
    expect(screen.queryByRole("button", { name: TO_BOTTOM })).toBeInTheDocument();
    let resolve: (messages: AssistantHistoryMessage[]) => void = () => {};
    mockedHistory.mockImplementationOnce(
      () =>
        new Promise<AssistantHistoryMessage[]>((done) => {
          resolve = done;
        }),
    );
    await user.click(screen.getByRole("button", { name: RETRY }));
    return async () => {
      layout.scrollHeight += 100;
      await act(async () => {
        resolve(OLD);
        await tick();
      });
    };
  }

  it("test_history_retry_while_scrolled_up_scrolls_to_the_end", async () => {
    await retryHistoryWhileScrolledUp();

    expect(distanceFromEnd()).toBe(0);
    expect(screen.queryByRole("button", { name: TO_BOTTOM })).not.toBeInTheDocument();
  });

  it("test_history_retry_while_scrolled_up_follows_the_loaded_history", async () => {
    const loaded = await retryHistoryWhileScrolledUp();

    await loaded();

    expect(screen.getByText("Привіт")).toBeInTheDocument();
    expect(distanceFromEnd()).toBe(0);
  });
});

// --- No automatic scroll is smooth ----------------------------------------------

describe("AssistantChat automatic scrolls are instant (17.7i)", () => {
  it("test_no_automatic_scroll_is_smooth", async () => {
    const { channel } = await startStreaming();
    await arrive(channel, { type: "status", step: "check_availability" });
    await arrive(channel, { type: "text", text: " Перевіряю." });
    await arrive(channel, { type: "error", code: "internal_error" });

    expect(scrolls.length).toBeGreaterThan(0);
    expect(scrolls.filter((scroll) => scroll.behavior === "smooth")).toEqual([]);
  });

  it("test_list_has_no_css_smooth_scrolling", async () => {
    await startStreaming();

    expect(findList()).not.toHaveClass("scroll-smooth");
    expect(findList().style.scrollBehavior).not.toBe("smooth");
  });
});
