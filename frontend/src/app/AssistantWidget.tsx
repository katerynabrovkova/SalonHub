"use client";

/**
 * Assistant widget shell (docs/DECISIONS.md § "Stage 17 sub-step order",
 * 17.3c; § "Stage 17 block A" and "block B"). No conversation yet: the chat
 * body stays empty until 17.4.
 *
 * - Shown only on allowlisted paths (isAssistantPath). On any other path it
 *   renders nothing but stays mounted in the root layout, so its state
 *   survives the visit. 17.7d: the mode is kept in sessionStorage and
 *   restored right after hydration after a full reload, without taking
 *   focus.
 * - Collapsed: a stripe on the right edge, slightly below the middle (26 px
 *   visible inside a 44 px tap area).
 * - Phone (< lg): the opened chat is full screen and modal — focus trap, no
 *   background scroll.
 * - Desktop (lg, 1024 px, and up): a non-modal small window in the corner
 *   (360 x 500), resizable into a 440 px side panel. The panel shifts the
 *   page through `data-assistant-panel` on <html> and the matching rule in
 *   globals.css; the attribute exists only while the panel is on screen.
 * - No close button (block B). "Згорнути" and Escape minimise; focus moves
 *   to the message input on open (17.4b) and back to the stripe on
 *   minimise.
 * - 17.4b: the conversation lives in useAssistantConversation, called here
 *   rather than in the chat view, so it and a streaming reply survive
 *   minimise and excluded pages. The stripe shows a dot once the
 *   conversation has at least one stored turn.
 *
 * Neutral styling only; the look is finished in the design pass.
 */

import { usePathname } from "next/navigation";
import {
  useEffect,
  useId,
  useRef,
  useState,
  useSyncExternalStore,
  type FormEvent,
  type KeyboardEvent,
} from "react";

import { isAssistantPath } from "@/lib/assistant/isAssistantPath";
import { readWidgetMode, storeWidgetMode, type WidgetMode } from "@/lib/assistant/widgetMode";
import {
  isPending as isNavigationPending,
  subscribe as subscribeToNavigation,
} from "@/lib/navigation/pendingNavigation";

import AssistantChat, { useAssistantConversation } from "./AssistantChat";

export interface AssistantWidgetProps {
  salonName: string;
  slug: string;
  /** 17.7c: the salon's starter questions, from GET salon; none by default. */
  starters?: string[];
}

const NO_STARTERS: string[] = [];

type Mode = WidgetMode;

// Tailwind's `lg`, the same breakpoint as the globals.css panel rule.
const DESKTOP_QUERY = "(min-width: 1024px)";
const PANEL_ATTRIBUTE = "data-assistant-panel";

const FOCUSABLE =
  'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

function subscribeToDesktop(onChange: () => void): () => void {
  const query = window.matchMedia(DESKTOP_QUERY);
  query.addEventListener("change", onChange);
  return () => query.removeEventListener("change", onChange);
}

function readDesktop(): boolean {
  return window.matchMedia(DESKTOP_QUERY).matches;
}

// The server has no viewport; the widget starts collapsed there, so the
// phone default never shows as a wrong layout.
function readDesktopOnServer(): boolean {
  return false;
}

// 17.7d: the stored mode changes only through the widget itself, which
// pins it in state, so there is nothing to subscribe to. 17.7g: the
// hydration flag never changes after hydration either.
function subscribeNever(): () => void {
  return () => {};
}

// The server cannot read sessionStorage. It renders no widget at all
// (17.7g), and the hydrating render reads this too, so it matches.
function readModeOnServer(): WidgetMode {
  return "collapsed";
}

// 17.7g: false on the server and in the hydrating render, true right after
// hydration, so the stripe never shows before it can respond to a tap.
function readHydratedOnClient(): boolean {
  return true;
}

function readHydratedOnServer(): boolean {
  return false;
}

const iconProps = {
  width: 20,
  height: 20,
  viewBox: "0 0 24 24",
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 2,
  strokeLinecap: "round" as const,
  strokeLinejoin: "round" as const,
  "aria-hidden": true,
};

function ChatIcon() {
  return (
    <svg {...iconProps} width={16} height={16}>
      <path d="M21 12a8 8 0 0 1-11.6 7.1L4 20l1-4.6A8 8 0 1 1 21 12z" />
    </svg>
  );
}

function ExpandIcon() {
  return (
    <svg {...iconProps}>
      <path d="M15 3h6v6M9 21H3v-6M21 3l-7 7M3 21l7-7" />
    </svg>
  );
}

function ShrinkIcon() {
  return (
    <svg {...iconProps}>
      <path d="M4 14h6v6M20 10h-6V4M14 10l7-7M3 21l7-7" />
    </svg>
  );
}

function MinimiseIcon() {
  return (
    <svg {...iconProps}>
      <path d="M5 12h14" />
    </svg>
  );
}

const HEADER_BUTTON =
  "flex h-11 w-11 items-center justify-center rounded text-zinc-600 hover:bg-zinc-100 dark:text-zinc-300 dark:hover:bg-zinc-800";

export default function AssistantWidget({
  salonName,
  slug,
  starters = NO_STARTERS,
}: AssistantWidgetProps) {
  const pathname = usePathname();
  // 17.7g: the server markup and the hydrating render are empty; the widget
  // appears right after hydration, read like the PaymentStatus hash.
  const hydrated = useSyncExternalStore(subscribeNever, readHydratedOnClient, readHydratedOnServer);
  // 17.7d: the stored mode, read like the PaymentStatus hash. The server
  // snapshot is "collapsed", so the hydrating render has the chat closed and
  // the stored mode applies right after hydration. The
  // subscription never fires; the customer's first change of mode (or a
  // phone card click) pins it in `chosenMode`, which takes over from then on.
  const storedMode = useSyncExternalStore(subscribeNever, readWidgetMode, readModeOnServer);
  const [chosenMode, setChosenMode] = useState<Mode | null>(null);
  const mode = chosenMode ?? storedMode;
  const shown = isAssistantPath(pathname);
  const isOpen = mode !== "collapsed";
  // 17.7a: "open" for the conversation means open on an allowed page.
  const conversation = useAssistantConversation(slug, shown && isOpen);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const isDesktop = useSyncExternalStore(subscribeToDesktop, readDesktop, readDesktopOnServer);
  const headingId = useId();
  const dialogRef = useRef<HTMLDivElement>(null);
  const stripeRef = useRef<HTMLButtonElement>(null);
  // Set by minimise() only, so mounting collapsed never steals focus.
  const returnFocusToStripe = useRef(false);
  // 17.7d: every mode change the customer makes is stored for the tab.
  function setMode(next: Mode) {
    setChosenMode(next);
    storeWidgetMode(next);
  }

  // Read by the open effect below without re-running it: null means the
  // chat is open only because a stored mode was restored, which takes no
  // focus (17.7d). Declared before it, so it is current in the same commit.
  const chosenModeRef = useRef(chosenMode);
  useEffect(() => {
    chosenModeRef.current = chosenMode;
  }, [chosenMode]);
  // Unsubscribes the wait started by a phone card click (17.6e).
  const stopWaitingForNavigation = useRef<(() => void) | null>(null);

  useEffect(() => {
    const waiting = stopWaitingForNavigation;
    return () => {
      waiting.current?.();
    };
  }, []);

  const inPanel = shown && isDesktop && mode === "panel";
  const isModal = shown && isOpen && !isDesktop;

  // Read by the open effect below without re-running it on every turn.
  // Declared before it, so it is current when both run in one commit.
  const streamingRef = useRef(conversation.streaming);
  useEffect(() => {
    streamingRef.current = conversation.streaming;
  }, [conversation.streaming]);

  useEffect(() => {
    if (!shown) {
      return;
    }
    if (isOpen) {
      // A restored chat (17.7d) takes no focus; only an open by the
      // customer does.
      if (chosenModeRef.current === null) {
        return;
      }
      // The input, unless a reply is streaming: then the dialog, as before
      // 17.7c made the input stay enabled while streaming.
      const input = inputRef.current;
      if (input !== null && !streamingRef.current) {
        input.focus();
      } else {
        dialogRef.current?.focus();
      }
    } else if (returnFocusToStripe.current) {
      returnFocusToStripe.current = false;
      stripeRef.current?.focus();
    }
  }, [shown, isOpen]);

  // 17.7c (decided 06.11.2026): after a send, focus stays in the input on
  // desktop (a click on the send button would take it); on a phone the
  // input loses focus so the keyboard closes and the reply is visible. The
  // input stays enabled, so a tap on it brings the keyboard back.
  function handleSend() {
    const input = inputRef.current;
    if (isDesktop) {
      input?.focus();
    } else {
      input?.blur();
    }
  }

  // After a reply finishes (done or any error), focus goes back to the input
  // on desktop if the chat is open. Not on a phone: the keyboard would cover
  // the reply (decided 03.11.2026, 17.4b). Still needed after 17.7c for a
  // turn that began with no focus in the input: a retry (its button goes
  // away), or focus moved elsewhere in the chat while the reply streamed.
  const wasStreaming = useRef(false);
  useEffect(() => {
    const finished = wasStreaming.current && !conversation.streaming;
    wasStreaming.current = conversation.streaming;
    if (finished && isDesktop && shown && isOpen) {
      inputRef.current?.focus();
    }
  }, [conversation.streaming, isDesktop, shown, isOpen]);

  useEffect(() => {
    if (!inPanel) {
      return;
    }
    const root = document.documentElement;
    root.setAttribute(PANEL_ATTRIBUTE, "");
    return () => root.removeAttribute(PANEL_ATTRIBUTE);
  }, [inPanel]);

  useEffect(() => {
    if (!isModal) {
      return;
    }
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = previous;
    };
  }, [isModal]);

  // A manual minimise (button or Escape) or typing in the input cancels a
  // waiting card minimise (decided 05.11.2026, 17.6e). This also covers a
  // manual reopen while waiting: the chat stays open while it waits, so
  // reopening it by hand always follows a manual minimise.
  function cancelWaitingMinimise() {
    stopWaitingForNavigation.current?.();
    stopWaitingForNavigation.current = null;
  }

  function minimise() {
    cancelWaitingMinimise();
    returnFocusToStripe.current = true;
    setMode("collapsed");
  }

  function handleInput(event: FormEvent<HTMLDivElement>) {
    if (event.target !== inputRef.current) {
      return;
    }
    // 17.7d (decided 06.11.2026): the card click stored "collapsed"; typing
    // keeps the chat open, so its open mode is stored again.
    const wasWaiting = stopWaitingForNavigation.current !== null;
    cancelWaitingMinimise();
    if (wasWaiting) {
      storeWidgetMode(mode);
    }
  }

  // 17.6a / block B, 17.6e: after a card link click the phone chat
  // minimises so the booking form is visible, but only once the navigation
  // has ended, so the previous page is never uncovered on the way. The
  // pending state comes from the shared store: NavigationProgress's
  // capture-phase listener has already started it (or not, for a link to
  // the current URL) by the time this onClick runs, and ends it on the URL
  // change or the 10 s safety timeout. Nothing pending: minimise at once.
  // Focus is left alone (not moved to the stripe): the page is changing
  // under it. Desktop stays as it was.
  function handleCardNavigate() {
    if (isDesktop) {
      return;
    }
    // 17.7d: stored at the click, so a reload during the navigation shows
    // the stripe, not the chat over the booking page. The current mode is
    // pinned first, so the chat stays open on screen until the minimise.
    setChosenMode(mode);
    storeWidgetMode("collapsed");
    if (!isNavigationPending()) {
      setMode("collapsed");
      return;
    }
    if (stopWaitingForNavigation.current !== null) {
      return;
    }
    const unsubscribe = subscribeToNavigation(() => {
      if (isNavigationPending()) {
        return;
      }
      unsubscribe();
      stopWaitingForNavigation.current = null;
      setMode("collapsed");
    });
    stopWaitingForNavigation.current = unsubscribe;
  }

  function trapTab(event: KeyboardEvent<HTMLDivElement>) {
    const focusable = Array.from(
      event.currentTarget.querySelectorAll<HTMLElement>(FOCUSABLE),
    );
    event.preventDefault();
    if (focusable.length === 0) {
      return;
    }
    const index = focusable.indexOf(document.activeElement as HTMLElement);
    const last = focusable.length - 1;
    const next = event.shiftKey
      ? index <= 0
        ? last
        : index - 1
      : index === -1 || index === last
        ? 0
        : index + 1;
    focusable[next].focus();
  }

  function handleKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    if (event.key === "Escape") {
      event.stopPropagation();
      minimise();
    } else if (event.key === "Tab" && isModal) {
      trapTab(event);
    }
  }

  if (!hydrated || !shown) {
    return null;
  }

  if (!isOpen) {
    return (
      <button
        ref={stripeRef}
        type="button"
        aria-label="Відкрити асистента"
        onClick={() => setMode("window")}
        className="fixed right-0 top-[60%] z-50 flex h-16 w-11 -translate-y-1/2 items-center justify-end"
      >
        <span className="relative flex h-full w-[26px] items-center justify-center rounded-l-lg bg-zinc-900 text-white shadow dark:bg-zinc-100 dark:text-zinc-900">
          <ChatIcon />
          {conversation.hasConversation ? (
            <span
              data-testid="assistant-live-dot"
              aria-hidden="true"
              className="absolute left-1 top-1.5 h-2 w-2 rounded-full bg-emerald-500"
            />
          ) : null}
        </span>
      </button>
    );
  }

  const title = salonName === "" ? "Асистент" : `Асистент ${salonName}`;
  const resizeLabel = mode === "panel" ? "Зменшити" : "Розгорнути";

  let frame: string;
  if (!isDesktop) {
    frame = "inset-0";
  } else if (mode === "panel") {
    frame = "inset-y-0 right-0 w-[440px] border-l";
  } else {
    frame =
      "right-4 bottom-4 h-[500px] max-h-[calc(100vh-2rem)] w-[360px] rounded-lg border shadow-lg";
  }

  return (
    <div
      ref={dialogRef}
      role="dialog"
      aria-modal={isModal ? true : undefined}
      aria-labelledby={headingId}
      tabIndex={-1}
      onKeyDown={handleKeyDown}
      onInput={handleInput}
      className={`fixed z-50 flex flex-col border-zinc-200 bg-white text-zinc-900 outline-none dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-100 ${frame}`}
    >
      <header className="flex items-center justify-between gap-2 border-b border-zinc-200 py-1 pl-4 pr-1 dark:border-zinc-700">
        <h2 id={headingId} className="truncate text-base font-semibold">
          {title}
        </h2>
        <div className="flex shrink-0 items-center">
          {isDesktop ? (
            <button
              type="button"
              aria-label={resizeLabel}
              title={resizeLabel}
              onClick={() => setMode(mode === "panel" ? "window" : "panel")}
              className={HEADER_BUTTON}
            >
              {mode === "panel" ? <ShrinkIcon /> : <ExpandIcon />}
            </button>
          ) : null}
          <button
            type="button"
            aria-label="Згорнути"
            title={isDesktop ? "Згорнути" : undefined}
            onClick={minimise}
            className={HEADER_BUTTON}
          >
            <MinimiseIcon />
          </button>
        </div>
      </header>
      <AssistantChat
        conversation={conversation}
        inputRef={inputRef}
        onCardNavigate={handleCardNavigate}
        onSend={handleSend}
        isDesktop={isDesktop}
        starters={starters}
      />
    </div>
  );
}
