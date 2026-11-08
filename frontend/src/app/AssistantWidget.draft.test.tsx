// @vitest-environment jsdom
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// docs/DECISIONS.md § "Stage 17 sub-step order", 17.7b: the unsent text in
// the chat input survives minimise, navigation between pages and a full
// reload, and is cleared after sending; it is never sent to the server.
// Driven through the widget with real sessionStorage; only the network
// (assistantApi) and the router are mocked. NavigationProgress is mounted
// next to the widget, as in the root layout, for the phone card case.
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
import type { AssistantEvent } from "@/lib/assistant/assistantApi";
import { getAssistantHistory, sendAssistantMessage } from "@/lib/assistant/assistantApi";
import { end } from "@/lib/navigation/pendingNavigation";

import AssistantWidget from "./AssistantWidget";
import NavigationProgress from "./NavigationProgress";

const DRAFT_KEY = "salonhub:assistant-draft";
const STRIPE = "Відкрити асистентку";
const MINIMISE = "Згорнути";
const INPUT = "Повідомлення";
const SEND = "Надіслати";
const DRAFT = "Чи є вільний час у суботу?";

const PHONE_WIDTH = 375;
const DESKTOP_WIDTH = 1280;
let viewportWidth = DESKTOP_WIDTH;

const mockedHistory = vi.mocked(getAssistantHistory);
const mockedSend = vi.mocked(sendAssistantMessage);

function stubMatchMedia() {
  window.matchMedia = vi.fn((query: string) => {
    const min = /min-width:\s*(\d+)px/.exec(query);
    return {
      matches: min !== null && viewportWidth >= Number(min[1]),
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
  window.history.replaceState(null, "", "/services");
  viewportWidth = DESKTOP_WIDTH;
  stubMatchMedia();
  mockedHistory.mockReset();
  mockedSend.mockReset();
  mockedHistory.mockResolvedValue([]);
});

afterEach(() => {
  act(() => {
    end();
  });
});

function ui() {
  return (
    <>
      <NavigationProgress />
      <AssistantWidget salonName="Bella" slug="bella-demo" />
    </>
  );
}

function renderWidget() {
  const user = userEvent.setup();
  const result = render(ui());
  return { user, ...result, rerenderAll: () => result.rerender(ui()) };
}

async function openChat(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole("button", { name: STRIPE }));
  return screen.getByRole("dialog");
}

function input() {
  return screen.getByRole("textbox", { name: INPUT });
}

function tick() {
  return new Promise((resolve) => setTimeout(resolve, 0));
}

async function* streamOf(events: AssistantEvent[]): AsyncGenerator<AssistantEvent> {
  for (const event of events) {
    yield event;
  }
}

function replyWith(events: AssistantEvent[]) {
  mockedSend.mockResolvedValueOnce({ sessionId: "server-id", events: streamOf(events) });
}

describe("AssistantWidget draft survives (17.7b)", () => {
  it("test_draft_survives_navigation_between_allowed_pages", async () => {
    const { user, rerenderAll } = renderWidget();
    await openChat(user);
    await user.type(input(), DRAFT);

    nav.pathname = "/specialists/12";
    rerenderAll();

    expect(input()).toHaveValue(DRAFT);
  });

  it("test_draft_survives_navigation_through_an_excluded_page", async () => {
    const { user, rerenderAll } = renderWidget();
    await openChat(user);
    await user.type(input(), DRAFT);

    nav.pathname = "/login";
    rerenderAll();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    nav.pathname = "/services";
    rerenderAll();

    expect(input()).toHaveValue(DRAFT);
  });

  it("test_draft_survives_minimise_and_reopen", async () => {
    const { user } = renderWidget();
    await openChat(user);
    await user.type(input(), DRAFT);

    await user.click(screen.getByRole("button", { name: MINIMISE }));
    await openChat(user);

    expect(input()).toHaveValue(DRAFT);
  });

  it("test_draft_survives_the_phone_minimise_after_a_card_navigation", async () => {
    viewportWidth = PHONE_WIDTH;
    // Reopening re-checks history (17.7a); live history keeps this test
    // about the minimise only, with no expiry.
    mockedHistory.mockResolvedValue([
      { role: "user", content: "Запишіть мене" },
      { role: "assistant", content: "" },
    ]);
    replyWith([
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
    ]);
    const { user, rerenderAll } = renderWidget();
    await openChat(user);
    await user.type(input(), "Запишіть мене");
    await user.click(screen.getByRole("button", { name: SEND }));
    const link = await screen.findByRole("link", { name: "Обрати час" });
    // The id is stored only at `done`, so this waits for the end of the turn.
    await waitFor(() =>
      expect(sessionStorage.getItem("salonhub:assistant-session")).toBe("server-id"),
    );
    await user.type(input(), DRAFT);

    await user.click(link);
    nav.pathname = "/booking";
    nav.search = "entry=service&service=7&specialist=any";
    rerenderAll();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    await openChat(user);

    expect(input()).toHaveValue(DRAFT);
  });

  it("test_draft_survives_a_full_reload", async () => {
    const first = renderWidget();
    await openChat(first.user);
    await first.user.type(input(), DRAFT);
    first.unmount();

    renderWidget();
    // 17.7d: the chat comes back open after a full reload.
    await screen.findByRole("dialog");

    expect(input()).toHaveValue(DRAFT);
  });
});

describe("AssistantWidget draft is cleared (17.7b)", () => {
  it("test_emptied_input_does_not_come_back_after_a_reload", async () => {
    const first = renderWidget();
    await openChat(first.user);
    await first.user.type(input(), DRAFT);
    await first.user.clear(input());
    first.unmount();

    renderWidget();
    // 17.7d: the chat comes back open after a full reload.
    await screen.findByRole("dialog");

    expect(input()).toHaveValue("");
    expect(sessionStorage.getItem(DRAFT_KEY)).toBeNull();
  });

  it("test_sending_clears_the_draft_after_a_successful_turn", async () => {
    replyWith([{ type: "text", text: "Так, є." }, { type: "done" }]);
    const first = renderWidget();
    await openChat(first.user);
    await first.user.type(input(), DRAFT);
    await first.user.click(screen.getByRole("button", { name: SEND }));
    await screen.findByText("Так, є.");
    expect(input()).toHaveValue("");
    first.unmount();

    renderWidget();
    // 17.7d: the chat comes back open after a full reload.
    await screen.findByRole("dialog");

    expect(input()).toHaveValue("");
    expect(sessionStorage.getItem(DRAFT_KEY)).toBeNull();
  });

  it.each([
    {
      name: "refused_before_the_stream",
      arrange: () =>
        mockedSend.mockRejectedValueOnce(
          new ApiError(503, "assistant_busy", "The assistant is busy."),
        ),
      failureText: "Зараз у мене багато співрозмовниць. Спробуйте, будь ласка, за хвилинку 🤗",
    },
    {
      name: "error_event",
      arrange: () =>
        replyWith([
          { type: "text", text: "Вітаю" },
          { type: "error", code: "assistant_provider_error" },
        ]),
      failureText: "Відповідь обірвалась. Спробуйте ще раз.",
    },
  ])("test_sending_clears_the_draft_after_a_failed_turn_($name)", async ({ arrange, failureText }) => {
    arrange();
    const first = renderWidget();
    await openChat(first.user);
    await first.user.type(input(), DRAFT);
    await first.user.click(screen.getByRole("button", { name: SEND }));
    await screen.findByText(failureText);
    first.unmount();

    renderWidget();
    // 17.7d: the chat comes back open after a full reload.
    await screen.findByRole("dialog");

    expect(input()).toHaveValue("");
    expect(sessionStorage.getItem(DRAFT_KEY)).toBeNull();
  });

  // A real page reload runs no React unmount, so the draft must already be
  // in storage while the page is still up.
  it("test_typing_alone_writes_the_draft_to_storage", async () => {
    const { user } = renderWidget();
    await openChat(user);

    await user.type(input(), DRAFT);

    expect(sessionStorage.getItem(DRAFT_KEY)).toBe(DRAFT);
  });

  it("test_typing_a_draft_sends_nothing_to_the_server", async () => {
    const { user } = renderWidget();
    await openChat(user);

    await user.type(input(), DRAFT);
    await user.click(screen.getByRole("button", { name: MINIMISE }));
    await tick();

    expect(mockedSend).not.toHaveBeenCalled();
    expect(mockedHistory).not.toHaveBeenCalled();
  });
});

// Decided 05.11.2026 (17.7b): the draft lives as long as the tab, not as long
// as the server conversation, so every kind of expiry (17.7a) leaves it.
describe("AssistantWidget draft and an expired conversation (17.7b)", () => {
  const SESSION_KEY = "salonhub:assistant-session";
  const EXPIRED = "Попередня розмова завершилась";
  const RETRY = "Повторити";
  const OLD = [
    { role: "user" as const, content: "Привіт" },
    { role: "assistant" as const, content: "Вітаю!" },
  ];

  function expectDraftKept() {
    expect(input()).toHaveValue(DRAFT);
    expect(sessionStorage.getItem(DRAFT_KEY)).toBe(DRAFT);
  }

  it("test_draft_stays_with_the_reload_notice", async () => {
    sessionStorage.setItem(SESSION_KEY, "stored-id");
    sessionStorage.setItem(DRAFT_KEY, DRAFT);
    const { user } = renderWidget();
    await waitFor(() => expect(sessionStorage.getItem(SESSION_KEY)).toBeNull());

    await openChat(user);

    expect(await screen.findByText(EXPIRED)).toBeInTheDocument();
    expectDraftKept();
  });

  it("test_draft_stays_with_the_open_time_divider", async () => {
    sessionStorage.setItem(SESSION_KEY, "stored-id");
    mockedHistory.mockResolvedValueOnce(OLD).mockResolvedValueOnce(OLD).mockResolvedValueOnce([]);
    const { user } = renderWidget();
    await screen.findByTestId("assistant-live-dot");
    await openChat(user);
    await waitFor(() => expect(mockedHistory).toHaveBeenCalledTimes(2));
    await user.type(input(), DRAFT);
    await user.click(screen.getByRole("button", { name: MINIMISE }));

    await openChat(user);

    expect(await screen.findByRole("separator")).toHaveTextContent(EXPIRED);
    expectDraftKept();
  });

  // A retry that finishes with a new id brings the divider while a draft,
  // typed after the failure, is in the input; the draft must stay.
  it("test_draft_stays_with_the_new_id_divider_after_a_retry", async () => {
    sessionStorage.setItem(SESSION_KEY, "stored-id");
    mockedHistory.mockResolvedValue(OLD);
    mockedSend
      .mockResolvedValueOnce({
        sessionId: "new-id",
        events: streamOf([
          { type: "text", text: "Ось що я знайш" },
          { type: "error", code: "assistant_provider_error" },
        ]),
      })
      .mockResolvedValueOnce({
        sessionId: "new-id",
        events: streamOf([{ type: "text", text: "У суботу є 10:00." }, { type: "done" }]),
      });
    const { user } = renderWidget();
    await screen.findByTestId("assistant-live-dot");
    await openChat(user);
    await waitFor(() => expect(mockedHistory).toHaveBeenCalledTimes(2));
    await user.type(input(), "Що є на суботу?");
    await user.click(screen.getByRole("button", { name: SEND }));
    await screen.findByRole("button", { name: RETRY });
    await user.type(input(), DRAFT);

    await user.click(screen.getByRole("button", { name: RETRY }));

    await screen.findByText("У суботу є 10:00.");
    expect(screen.getByRole("separator")).toHaveTextContent(EXPIRED);
    // The id is stored only at `done`, so this waits for the end of the turn.
    await waitFor(() => expect(sessionStorage.getItem(SESSION_KEY)).toBe("new-id"));
    expectDraftKept();
  });
});
