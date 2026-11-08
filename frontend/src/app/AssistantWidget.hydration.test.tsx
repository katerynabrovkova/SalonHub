// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import type { ReactElement } from "react";
import { hydrateRoot, type Root } from "react-dom/client";
import { renderToString } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// docs/DECISIONS.md § "Stage 17 sub-step order", 17.7g: the server renders
// nothing for the widget, so the stripe never shows before it can respond to
// a tap. The stripe appears only after hydration; the 17.7d stored mode is
// still restored right after it, without taking focus.

const nav = vi.hoisted(() => ({ pathname: "/services" }));

vi.mock("next/navigation", () => ({
  usePathname: () => nav.pathname,
  useSearchParams: () => new URLSearchParams(),
}));

// Same module boundaries as AssistantWidget.test.tsx: no stored id and an
// empty history, so the widget makes no request.
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

// The loader's server-side boundaries, as in AssistantWidgetLoader.test.tsx;
// the widget itself is real here.
vi.mock("next/headers", () => ({
  headers: vi.fn(),
}));

vi.mock("@/lib/tenants/getSalonInfoPage", () => ({
  getSalonInfoPage: vi.fn(),
}));

// Imported after the mocks above so the mocked modules are what the widget sees.
import { getAssistantHistory, sendAssistantMessage } from "@/lib/assistant/assistantApi";
import { readSessionId } from "@/lib/assistant/sessionId";
import { getSalonInfoPage } from "@/lib/tenants/getSalonInfoPage";
import { SALON_SLUG_HEADER } from "@/middleware";
import { headers } from "next/headers";

import AssistantWidget from "./AssistantWidget";
import AssistantWidgetLoader from "./AssistantWidgetLoader";

const STRIPE = "Відкрити асистента";
const INPUT = "Повідомлення";
const TO_PANEL = "Розгорнути";
const TO_WINDOW = "Зменшити";
const PANEL_ATTRIBUTE = "data-assistant-panel";
const MODE_KEY = "salonhub:assistant-mode";

const PHONE_WIDTH = 375;
const DESKTOP_WIDTH = 1280;

let viewportWidth = DESKTOP_WIDTH;

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

function widget(): ReactElement {
  return <AssistantWidget salonName="Bella" slug="bella-demo" />;
}

// --- Hydration harness -----------------------------------------------------

let root: Root | null = null;
let container: HTMLElement | null = null;
let recoverableErrors: unknown[] = [];
let consoleError: ReturnType<typeof vi.spyOn>;

/**
 * Puts `html` into a container in the document, as the browser receives it,
 * then hydrates `element` over it. Recoverable errors (a hydration mismatch
 * in React 19) are collected; console.error is spied in beforeEach, which is
 * where React's dev-only mismatch warnings go.
 */
async function hydrate(html: string, element: ReactElement): Promise<HTMLElement> {
  const target = document.createElement("div");
  target.innerHTML = html;
  document.body.appendChild(target);
  container = target;
  await act(async () => {
    root = hydrateRoot(target, element, {
      onRecoverableError: (error) => {
        recoverableErrors.push(error);
      },
    });
  });
  return target;
}

beforeEach(() => {
  sessionStorage.clear();
  nav.pathname = "/services";
  viewportWidth = DESKTOP_WIDTH;
  stubMatchMedia();
  document.documentElement.removeAttribute(PANEL_ATTRIBUTE);
  document.body.style.overflow = "";
  vi.mocked(sendAssistantMessage).mockReset();
  vi.mocked(getAssistantHistory).mockReset();
  vi.mocked(getAssistantHistory).mockResolvedValue([]);
  vi.mocked(readSessionId).mockReset();
  vi.mocked(readSessionId).mockReturnValue(null);
  recoverableErrors = [];
  consoleError = vi.spyOn(console, "error");
});

afterEach(() => {
  if (root !== null) {
    const mounted = root;
    act(() => mounted.unmount());
    root = null;
  }
  container?.remove();
  container = null;
  cleanup();
  consoleError.mockRestore();
});

// --- 1. Server render -------------------------------------------------------

describe("AssistantWidget server render (17.7g)", () => {
  it.each(["none", "collapsed", "window", "panel"])(
    "test_server_render_has_no_stripe_and_no_chat_window_(%s)",
    (stored) => {
      if (stored !== "none") {
        sessionStorage.setItem(MODE_KEY, stored);
      }

      const html = renderToString(widget());

      expect(html).not.toContain(`aria-label="${STRIPE}"`);
      expect(html).not.toContain('role="dialog"');
      expect(html).toBe("");
    },
  );
});

// --- 2. Client render -------------------------------------------------------

describe("AssistantWidget client render (17.7g)", () => {
  it("test_client_render_shows_the_stripe_and_it_opens_the_chat", () => {
    render(widget());

    fireEvent.click(screen.getByRole("button", { name: STRIPE }));

    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  it("test_stripe_appears_after_hydration_and_opens_the_chat", async () => {
    const html = renderToString(widget());

    await hydrate(html, widget());

    const stripe = screen.getByRole("button", { name: STRIPE });
    await act(async () => {
      fireEvent.click(stripe);
    });
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(document.activeElement).toBe(screen.getByRole("textbox", { name: INPUT }));
  });
});

// --- 3. Hydration without a mismatch ----------------------------------------

describe("AssistantWidget hydration (17.7g)", () => {
  it.each([
    { name: "phone_nothing_stored", width: PHONE_WIDTH, stored: null },
    { name: "desktop_nothing_stored", width: DESKTOP_WIDTH, stored: null },
    { name: "phone_window_stored", width: PHONE_WIDTH, stored: "window" },
    { name: "desktop_window_stored", width: DESKTOP_WIDTH, stored: "window" },
    { name: "desktop_panel_stored", width: DESKTOP_WIDTH, stored: "panel" },
  ])("test_hydrating_the_server_html_logs_no_mismatch_($name)", async ({ width, stored }) => {
    viewportWidth = width;
    if (stored !== null) {
      sessionStorage.setItem(MODE_KEY, stored);
    }
    const html = renderToString(widget());

    await hydrate(html, widget());

    expect(recoverableErrors).toEqual([]);
    expect(consoleError).not.toHaveBeenCalled();
  });
});

// --- 4. 17.7d restore after hydration ---------------------------------------

describe("AssistantWidget stored mode after hydration (17.7g with 17.7d)", () => {
  it.each([
    { mode: "window", panel: false, resize: TO_PANEL },
    { mode: "panel", panel: true, resize: TO_WINDOW },
  ])(
    "test_desktop_restores_the_stored_mode_after_hydration_($mode)",
    async ({ mode, panel, resize }) => {
      viewportWidth = DESKTOP_WIDTH;
      sessionStorage.setItem(MODE_KEY, mode);
      const html = renderToString(widget());

      await hydrate(html, widget());

      const dialog = await screen.findByRole("dialog");
      expect(dialog).not.toHaveAttribute("aria-modal");
      expect(screen.getByRole("button", { name: resize })).toBeInTheDocument();
      if (panel) {
        expect(document.documentElement).toHaveAttribute(PANEL_ATTRIBUTE);
      } else {
        expect(document.documentElement).not.toHaveAttribute(PANEL_ATTRIBUTE);
      }
    },
  );

  it.each(["window", "panel"])(
    "test_phone_restores_an_open_mode_after_hydration_as_the_full_screen_chat_(%s)",
    async (mode) => {
      viewportWidth = PHONE_WIDTH;
      sessionStorage.setItem(MODE_KEY, mode);
      const html = renderToString(widget());

      await hydrate(html, widget());

      expect(await screen.findByRole("dialog")).toHaveAttribute("aria-modal", "true");
      expect(document.documentElement).not.toHaveAttribute(PANEL_ATTRIBUTE);
    },
  );

  it.each([
    { name: "phone_window", width: PHONE_WIDTH, mode: "window" },
    { name: "desktop_window", width: DESKTOP_WIDTH, mode: "window" },
    { name: "desktop_panel", width: DESKTOP_WIDTH, mode: "panel" },
  ])(
    "test_chat_restored_after_hydration_takes_no_focus_($name)",
    async ({ width, mode }) => {
      viewportWidth = width;
      sessionStorage.setItem(MODE_KEY, mode);
      const html = renderToString(widget());

      await hydrate(html, widget());

      const dialog = await screen.findByRole("dialog");
      expect(document.activeElement).not.toBe(dialog);
      expect(dialog).not.toContainElement(document.activeElement as HTMLElement);
    },
  );
});

// --- 5. Assistant disabled (17.3b behaviour kept) ----------------------------

describe("AssistantWidgetLoader with the real widget (17.7g with 17.3b)", () => {
  function withSlug(slug: string | null) {
    vi.mocked(headers).mockResolvedValue({
      get: (name: string) => (name === SALON_SLUG_HEADER ? slug : null),
      // eslint-disable-next-line @typescript-eslint/no-explicit-any -- minimal ReadonlyHeaders stand-in, only .get() is used by the loader
    } as any);
  }

  function salonInfo(assistantEnabled: boolean) {
    return {
      name: "Bella",
      currency: "UAH",
      assistant_enabled: assistantEnabled,
      assistant_starters: [],
    };
  }

  beforeEach(() => {
    vi.mocked(headers).mockReset();
    vi.mocked(getSalonInfoPage).mockReset();
    withSlug("bella-demo");
  });

  it("test_disabled_assistant_renders_nothing_on_the_server_or_after_hydration", async () => {
    vi.mocked(getSalonInfoPage).mockResolvedValue(salonInfo(false));
    const element = <>{await AssistantWidgetLoader()}</>;

    const html = renderToString(element);
    expect(html).toBe("");

    const target = await hydrate(html, element);
    expect(target).toBeEmptyDOMElement();
    expect(screen.queryByRole("button", { name: STRIPE })).not.toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(recoverableErrors).toEqual([]);
    expect(consoleError).not.toHaveBeenCalled();
  });

  it("test_disabled_assistant_renders_nothing_on_a_client_render", async () => {
    vi.mocked(getSalonInfoPage).mockResolvedValue(salonInfo(false));
    sessionStorage.setItem(MODE_KEY, "window");

    const { container: rendered } = render(<>{await AssistantWidgetLoader()}</>);

    expect(rendered).toBeEmptyDOMElement();
  });

  it("test_enabled_assistant_server_html_has_no_stripe_then_hydration_shows_it", async () => {
    vi.mocked(getSalonInfoPage).mockResolvedValue(salonInfo(true));
    const element = <>{await AssistantWidgetLoader()}</>;

    const html = renderToString(element);
    expect(html).not.toContain(`aria-label="${STRIPE}"`);

    await hydrate(html, element);
    expect(screen.getByRole("button", { name: STRIPE })).toBeInTheDocument();
    expect(recoverableErrors).toEqual([]);
    expect(consoleError).not.toHaveBeenCalled();
  });
});
