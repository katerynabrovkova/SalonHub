"use client";

/**
 * The assistant conversation (docs/DECISIONS.md § "Stage 17 sub-step
 * order", 17.4b; block B).
 *
 * `useAssistantConversation` holds the conversation; AssistantWidget calls
 * it, so the conversation and a streaming reply survive minimise and pages
 * where the widget renders nothing. `AssistantChat` only renders it.
 *
 * - History loads on mount, only when a session id is stored. Empty
 *   history means the conversation expired: the id is dropped at once and
 *   the expiry waits in storage (17.7a) until the chat is open on an
 *   allowed page, where the notice shows once.
 * - 17.7h: until the history has loaded on this page load, sending is
 *   blocked (the input stays enabled). A load slower than 0.3 s shows a
 *   spinner; it is aborted after 10 s and on unmount. A failed load keeps
 *   the id and shows the failure block, whose "Повторити" loads it again, as
 *   do opening the chat and returning to the tab; never two loads at once.
 *   Mount and every retry run the same `loadHistory`.
 * - 17.7a, a page that stays loaded: opening a chat that holds a
 *   conversation, or the tab becoming visible with the chat open, re-checks
 *   history; a send answered with a different session id counts too. An
 *   expired conversation is not erased: a divider marks where the new one
 *   starts, one per gap.
 * - The session id is stored only after `done`; the backend returns an id
 *   even for a first message that fails and stores nothing (block B).
 * - Statuses (17.5a): the last status event's step is kept until the next
 *   text or the end of the turn; the view shows its text, or pulsing dots
 *   before any text or known status.
 * - Proposal cards (17.6a) come from the live `proposal` event and from
 *   history; a message is kept when it has text or a well-formed card, and
 *   a malformed card is dropped. Retry removes the failed turn's bubble,
 *   card included. Unknown events are ignored.
 * - Any other ending (an `error` event, a refusal before the stream, a lost
 *   connection, a stream ending without `done`) shows its 17.5b table row:
 *   text, "Повторити" where offered, and the "book it yourself" link where
 *   offered (lib/assistant/failure.ts), in one full-width block at the end
 *   of the message list (17.7c). Retry resends the last customer text
 *   without a second bubble and replaces the partial reply.
 * - 17.7e: the sent text is kept in sessionStorage (lib/assistant/pendingTurn.ts)
 *   from send until `done`. After a reload it shows as her message with the
 *   "cut off" block: at once without a session id, otherwise after history,
 *   unless the last stored turn's customer message equals it (then it is
 *   dropped). Empty history (expired) moves it into an empty input or drops
 *   it; a failed history request leaves it stored.
 * - The request is aborted on unmount.
 * - The draft (17.7b) lives in the hook and in sessionStorage
 *   (lib/assistant/draft.ts): written on every change, cleared on send
 *   whatever the outcome, kept when the conversation expires. Retry resends
 *   the earlier text and leaves the draft alone.
 * - 17.7c (decided 06.11.2026): while a reply streams the input stays
 *   enabled, so text typed meanwhile is the draft; only sending is blocked
 *   (the send button is disabled, Enter does not send). After a send the
 *   view calls `onSend`, where the widget moves focus by device.
 * - 17.7i: the list scrolls to the end in a layout effect, before paint and
 *   instantly, but only while it follows: she leaves the bottom by an upward
 *   scroll ending more than 80 px from the end, and any scroll back within
 *   80 px resumes following. Meanwhile a round "↓" button above the input
 *   scrolls smoothly to the end (the only smooth scroll); it, sending and
 *   both "Повторити" resume following.
 * - 17.7c: the AI notice is one line below the input, always visible; the
 *   whole text is the link, opening `/privacy` in a new tab, with a
 *   visually hidden destination in its name. While the dots show, a visually
 *   hidden "Асистент пише…" in the status container is what is announced.
 */

import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type FormEvent,
  type KeyboardEvent,
  type Ref,
} from "react";

import Link from "next/link";

import { ApiError } from "@/lib/api/errors";
import { getAssistantHistory, sendAssistantMessage } from "@/lib/assistant/assistantApi";
import { clearDraft, readDraft, storeDraft } from "@/lib/assistant/draft";
import {
  failureFor,
  HISTORY_LOAD_FAILURE,
  type Failure,
  type FailureCause,
} from "@/lib/assistant/failure";
import { clearPendingText, readPendingText, storePendingText } from "@/lib/assistant/pendingTurn";
import { parseProposalCard, type ProposalCard } from "@/lib/assistant/proposalCard";
import {
  clearConversationExpired,
  clearSessionId,
  markConversationExpired,
  readConversationExpired,
  readSessionId,
  storeSessionId,
} from "@/lib/assistant/sessionId";
import { statusText } from "@/lib/assistant/statusText";

import AssistantProposalCard from "./AssistantProposalCard";

const GREETING =
  "Вітаю! Я допоможу дізнатися ціни, обрати послугу і знайти вільний час у нашому салоні. Що Вас цікавить?";
const EXPIRED_NOTICE = "Попередня розмова завершилась";
// 17.7c and "Stage 17 block D" (AI notice). `/privacy` returns 404 until the
// Stage 22 privacy page exists.
const AI_NOTICE = "ШІ може помилятися. Перевіряйте важливу інформацію.";
// Visually hidden, so the link's name says where it leads (17.7c).
const AI_NOTICE_DESTINATION = "Як ми обробляємо дані, відкривається в новій вкладці";
const PRIVACY_HREF = "/privacy";
// 17.7c: visually hidden, announced while the dots show.
const TYPING_TEXT = "Асистент пише…";

// 17.7h: visually hidden, announced while the history spinner shows.
const HISTORY_LOADING_TEXT = "Завантажую розмову…";
const HISTORY_SPINNER_DELAY_MS = 300;
const HISTORY_TIMEOUT_MS = 10_000;

// 17.7i: within this distance of the end she counts as at the bottom.
const AT_BOTTOM_PX = 80;
const TO_BOTTOM_LABEL = "Прокрутити донизу";

// Same limit as the backend serializer (ai_assistant/serializers.py).
const MAX_MESSAGE_LENGTH = 2000;

// 17.7e: after a reload a pending turn always shows the "cut off" row
// (17.5b), whatever ended it.
const PENDING_FAILURE = failureFor({ kind: "no_done", textReceived: true });
// The id of a pending customer bubble shown from the first render.
const PENDING_ID = 0;

/**
 * 17.7e: a pending text shown from the first render. Only without a stored
 * session id; with one, the history answer decides. The server render has no
 * storage, so it reads null there.
 */
function readPendingWithoutSession(): string | null {
  return readSessionId() === null ? readPendingText() : null;
}

/** The customer message of the last stored turn (17.7e matching rule). */
function lastCustomerText(history: { role: string; content: string }[]): string | null {
  for (let index = history.length - 1; index >= 0; index--) {
    if (history[index].role === "user") {
      return history[index].content;
    }
  }
  return null;
}

export interface ChatMessage {
  id: number;
  role: "user" | "assistant";
  content: string;
  /** The assistant message's proposal card (17.6a), if any. */
  card?: ProposalCard;
}

/** Marks where a new conversation starts after an expired one (17.7a). */
export interface ChatDivider {
  id: number;
  role: "divider";
}

export type ChatEntry = ChatMessage | ChatDivider;

/**
 * 17.7h: "none" when no session id was stored at mount (nothing to load);
 * sending is blocked while "loading" or "failed".
 */
export type HistoryStatus = "none" | "loading" | "failed" | "loaded";

/** The status a mount starts with: loading whenever a session id is stored. */
function initialHistoryStatus(): HistoryStatus {
  return readSessionId() === null ? "none" : "loading";
}

export interface AssistantConversation {
  messages: ChatEntry[];
  /** 17.7h: where the history load of this page load stands. */
  historyStatus: HistoryStatus;
  /** 17.7h: the load has taken longer than 0.3 s; shows the spinner. */
  historySpinner: boolean;
  /** 17.7h: loads the history again after a failure ("Повторити"). */
  reloadHistory: () => void;
  streaming: boolean;
  /** How the last reply failed (17.5b table row), or null. */
  failure: Failure | null;
  /** A stored conversation had expired; shows the notice. */
  expired: boolean;
  /** At least one stored turn: the stripe's live dot (block A). */
  hasConversation: boolean;
  /**
   * The step key of the last status event (17.5a), cleared by every text
   * event and when the turn ends.
   */
  status: string | null;
  /** The streaming reply has its first text. */
  replyStarted: boolean;
  /** The unsent text in the chat input (17.7b). */
  draft: string;
  /** Updates the draft and its stored copy. */
  setDraft: (text: string) => void;
  /** Sends the text and clears the draft, whatever the turn's outcome. */
  send: (text: string) => void;
  /** Resends the last customer text after a failure that offers retry. */
  retry: () => void;
}

/**
 * `open`: the chat is open on an allowed page (the widget passes it). It is
 * required on purpose: the expired notice and the history re-checks depend
 * on it, so no caller may get them by default (17.7a).
 */
export function useAssistantConversation(slug: string, open: boolean): AssistantConversation {
  // 17.7e: read once per mount, like the draft below.
  const [pendingAtMount] = useState(readPendingWithoutSession);
  const [messages, setMessages] = useState<ChatEntry[]>(() =>
    pendingAtMount === null ? [] : [{ id: PENDING_ID, role: "user", content: pendingAtMount }],
  );
  const [streaming, setStreaming] = useState(false);
  const [failure, setFailure] = useState<Failure | null>(() =>
    pendingAtMount === null ? null : PENDING_FAILURE,
  );
  // 17.7a: an expiry the customer has not seen yet, from storage (an earlier
  // page load) or found by this mount; shown as the notice once the chat is
  // open, then cleared from storage. The server render has no storage, so
  // it reads false there.
  const [noticeOwed, setNoticeOwed] = useState(readConversationExpired);
  const [expired, setExpired] = useState(false);
  if (open && noticeOwed) {
    setNoticeOwed(false);
    setExpired(true);
  }
  const [hasConversation, setHasConversation] = useState(false);
  // 17.7b: held here, not in the view, so it survives minimise and excluded
  // pages; stored on every change, so a real reload keeps it. The server
  // render has no storage, so it reads "" there.
  const [draft, setDraftState] = useState(readDraft);
  const setDraft = useCallback((text: string) => {
    setDraftState(text);
    storeDraft(text);
  }, []);
  const [status, setStatus] = useState<string | null>(null);
  const [replyStarted, setReplyStarted] = useState(false);
  const sessionIdRef = useRef<string | null>(null);
  const streamingRef = useRef(false);
  const abortRef = useRef<AbortController | null>(null);
  const nextIdRef = useRef(PENDING_ID + 1);
  // The last customer text and the reply bubble of the last turn, for retry.
  // A pending text shown after a reload (17.7e) is the last customer text.
  const lastTextRef = useRef<string | null>(pendingAtMount);
  const replyIdRef = useRef<number | null>(null);
  // The customer bubble of the last turn: a divider found on send goes
  // above it (17.7a).
  const userIdRef = useRef<number | null>(pendingAtMount === null ? null : PENDING_ID);
  // The chat holds a conversation (restored history or a finished turn), so
  // opening it or returning to the tab re-checks history (17.7a).
  const liveRef = useRef(false);
  const mountedRef = useRef(true);
  // 17.7h: the history has loaded on this page load, or there was nothing
  // to load (no stored id at mount). Until then sending is blocked and the
  // re-check triggers load it instead.
  const historyLoadedRef = useRef(false);
  const [historyStatus, setHistoryStatus] = useState(initialHistoryStatus);
  const [historySpinner, setHistorySpinner] = useState(false);
  // Cancels the history load in flight; null when none runs (the guard
  // against two loads at once).
  const cancelHistoryLoadRef = useRef<(() => void) | null>(null);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  useEffect(() => {
    if (expired) {
      clearConversationExpired();
    }
  }, [expired]);

  /**
   * 17.7h: the one history load, run on mount, by "Повторити" and by the
   * re-check triggers while the history has not loaded. Does nothing without
   * an id, once loaded, or while a load runs. The request is aborted after
   * 10 s, which counts as a failure, and on unmount (see the mount effect).
   */
  const loadHistory = useCallback(() => {
    const stored = sessionIdRef.current;
    if (stored === null || historyLoadedRef.current || cancelHistoryLoadRef.current !== null) {
      return;
    }
    const controller = new AbortController();
    let current = true;
    const timeout = setTimeout(() => controller.abort(), HISTORY_TIMEOUT_MS);
    const spinner = setTimeout(() => setHistorySpinner(true), HISTORY_SPINNER_DELAY_MS);
    function settle() {
      clearTimeout(timeout);
      clearTimeout(spinner);
      cancelHistoryLoadRef.current = null;
    }
    cancelHistoryLoadRef.current = () => {
      current = false;
      settle();
      controller.abort();
    };
    setHistoryStatus("loading");
    getAssistantHistory(slug, stored, controller.signal).then(
      (history) => {
        if (!current) {
          return;
        }
        settle();
        setHistorySpinner(false);
        historyLoadedRef.current = true;
        setHistoryStatus("loaded");
        if (history.length === 0) {
          // Cleared at once so the booking source stays honest; the notice
          // waits in storage until the chat is opened on an allowed page.
          clearSessionId();
          sessionIdRef.current = null;
          markConversationExpired();
          setNoticeOwed(true);
          // 17.7e: an expired conversation's pending text goes into an
          // empty input, else it is dropped; no failure block either way.
          // Skipped once she has sent on this page: the stored text is then
          // her new turn's. The draft is stored on every change, so storage
          // tells whether the input is empty.
          const pending = lastTextRef.current === null ? readPendingText() : null;
          if (pending !== null) {
            clearPendingText();
            if (readDraft() === "") {
              setDraftState(pending);
              storeDraft(pending);
            }
          }
          return;
        }
        // A message is kept when it has text or a well-formed card; a
        // malformed stored card is dropped, its text still shows (17.6a).
        const restored: ChatMessage[] = [];
        for (const message of history) {
          const card = parseProposalCard(message.card);
          if (message.content === "" && card === null) {
            continue;
          }
          restored.push({
            id: nextIdRef.current++,
            role: message.role,
            content: message.content,
            ...(card === null ? {} : { card }),
          });
        }
        // 17.7e: a pending text is dropped when the last stored turn's
        // customer message equals it (the server saved the turn); otherwise
        // it shows after the history with the "cut off" block. A failed
        // request leaves it stored for the next successful load. Skipped
        // once she has sent on this page, as above.
        const pending = lastTextRef.current === null ? readPendingText() : null;
        if (pending !== null) {
          if (lastCustomerText(history) === pending) {
            clearPendingText();
          } else {
            const pendingId = nextIdRef.current++;
            restored.push({ id: pendingId, role: "user", content: pending });
            lastTextRef.current = pending;
            userIdRef.current = pendingId;
            setFailure(PENDING_FAILURE);
          }
        }
        // Prepended: a message sent before history arrived stays last.
        setMessages((current) => [...restored, ...current]);
        liveRef.current = true;
        setHasConversation(true);
      },
      () => {
        if (!current) {
          return;
        }
        // Failed request (or the timeout): keep the id and any 17.7e stored
        // text; the failure block offers "Повторити".
        settle();
        setHistorySpinner(false);
        setHistoryStatus("failed");
      },
    );
  }, [slug]);

  useEffect(() => {
    const stored = readSessionId();
    sessionIdRef.current = stored;
    if (stored === null) {
      historyLoadedRef.current = true;
      return;
    }
    loadHistory();
    // Aborts whichever load runs at unmount, the mount one or a retry.
    const cancelHistoryLoad = cancelHistoryLoadRef;
    return () => {
      cancelHistoryLoad.current?.();
    };
  }, [loadHistory]);

  /**
   * 17.7a, a page that stays loaded: asks whether the held conversation is
   * still alive. Empty history means it expired: the old messages stay, a
   * divider goes at the bottom, the id is dropped. A live conversation or a
   * failed request changes nothing. Skipped while a reply streams, and the
   * answer is ignored if a turn has started or the id changed meanwhile.
   * 17.7h: while the history has not loaded on this page load, it loads the
   * history instead.
   */
  const recheck = useCallback(() => {
    if (!historyLoadedRef.current) {
      loadHistory();
      return;
    }
    const id = sessionIdRef.current;
    if (id === null || !liveRef.current || streamingRef.current) {
      return;
    }
    getAssistantHistory(slug, id).then(
      (history) => {
        if (
          !mountedRef.current ||
          history.length > 0 ||
          sessionIdRef.current !== id ||
          streamingRef.current
        ) {
          return;
        }
        clearSessionId();
        sessionIdRef.current = null;
        liveRef.current = false;
        setHasConversation(false);
        const dividerId = nextIdRef.current++;
        setMessages((current) => [...current, { id: dividerId, role: "divider" }]);
      },
      () => {
        // Failed request: nothing changes.
      },
    );
  }, [slug, loadHistory]);

  // Re-check on every open of a chat that holds a conversation (not on a
  // chat that is open from the first render: the mount request covers it).
  const wasOpenRef = useRef(open);
  useEffect(() => {
    const opened = open && !wasOpenRef.current;
    wasOpenRef.current = open;
    if (opened) {
      recheck();
    }
  }, [open, recheck]);

  // ...and when the tab becomes visible again with the chat open.
  useEffect(() => {
    if (!open) {
      return;
    }
    function handleVisibility() {
      if (document.visibilityState === "visible") {
        recheck();
      }
    }
    document.addEventListener("visibilitychange", handleVisibility);
    return () => document.removeEventListener("visibilitychange", handleVisibility);
  }, [open, recheck]);

  useEffect(() => () => abortRef.current?.abort(), []);

  /**
   * Runs one turn for `text`, whose customer bubble is already shown. The
   * reply streams into a new bubble; a failure is described by its cause
   * and turned into the 17.5b table row by failureFor.
   */
  const runTurn = useCallback(
    (text: string) => {
      const replyId = nextIdRef.current++;
      replyIdRef.current = replyId;
      const controller = new AbortController();
      abortRef.current = controller;
      streamingRef.current = true;
      setStreaming(true);
      setFailure(null);
      setStatus(null);
      setReplyStarted(false);

      // The reply bubble is created by its first text or its card.
      function updateReply(patch: Partial<ChatMessage>) {
        setMessages((current) =>
          current.some((message) => message.id === replyId)
            ? current.map((message) =>
                message.id === replyId ? { ...(message as ChatMessage), ...patch } : message,
              )
            : [...current, { id: replyId, role: "assistant", content: "", ...patch }],
        );
      }

      // 17.7a fallback: a finished turn answered the stored id with a new
      // one, so the conversation expired before this turn. Checked only at
      // `done`, like storing the id (block B): a failed turn's id means
      // nothing, so its retry still sends the old id and the divider comes
      // once, when the retry finishes. It goes above this turn's customer
      // bubble.
      function markExpiredBeforeThisTurn() {
        const userId = userIdRef.current;
        const dividerId = nextIdRef.current++;
        setMessages((current) => {
          const index = current.findIndex((message) => message.id === userId);
          const at = index === -1 ? current.length : index;
          return [...current.slice(0, at), { id: dividerId, role: "divider" }, ...current.slice(at)];
        });
      }

      function showReply(content: string) {
        updateReply({ content });
      }

      void (async () => {
        let done = false;
        let replyText = "";
        let cause: FailureCause | null = null;
        try {
          const sentId = sessionIdRef.current;
          const reply = await sendAssistantMessage(slug, text, sentId, controller.signal);
          for await (const event of reply.events) {
            if (event.type === "text") {
              replyText += event.text;
              showReply(replyText);
              setReplyStarted(true);
              setStatus(null);
            } else if (event.type === "status") {
              setStatus(event.step);
            } else if (event.type === "done") {
              done = true;
              if (sentId !== null && reply.sessionId !== null && reply.sessionId !== sentId) {
                markExpiredBeforeThisTurn();
              }
              if (reply.sessionId !== null) {
                storeSessionId(reply.sessionId);
                sessionIdRef.current = reply.sessionId;
              }
              // 17.7e: the turn finished, so nothing is pending.
              clearPendingText();
              liveRef.current = true;
              setHasConversation(true);
              break;
            } else if (event.type === "error") {
              cause = { kind: "event", code: event.code, textReceived: replyText !== "" };
              break;
            }
            if (event.type === "proposal") {
              const card = parseProposalCard(event.card);
              if (card !== null) {
                updateReply({ card });
              }
            }
            // Unknown events are ignored.
          }
          if (!done && cause === null) {
            cause = { kind: "no_done", textReceived: replyText !== "" };
          }
        } catch (error) {
          if (error instanceof ApiError) {
            cause = { kind: "api", status: error.status, code: error.code };
          } else if (error instanceof TypeError) {
            // fetch or the stream read failed: the connection was lost,
            // whether or not text had begun.
            cause = { kind: "network" };
          } else {
            // A malformed stream line: the reply broke off.
            cause = { kind: "no_done", textReceived: replyText !== "" };
          }
        }
        if (controller.signal.aborted) {
          return;
        }
        streamingRef.current = false;
        setStreaming(false);
        setStatus(null);
        if (!done && cause !== null) {
          setFailure(failureFor(cause));
        }
      })();
    },
    [slug],
  );

  const send = useCallback(
    (raw: string) => {
      const text = raw.trim();
      // 17.7h: nothing is sent before the history has loaded.
      if (text === "" || streamingRef.current || !historyLoadedRef.current) {
        return;
      }
      lastTextRef.current = text;
      // 17.7e: kept until `done`; replaces an earlier pending text.
      storePendingText(text);
      // Decided 05.11.2026 (17.7b): cleared on send, whether the turn
      // succeeds or fails.
      setDraftState("");
      clearDraft();
      const userId = nextIdRef.current++;
      userIdRef.current = userId;
      setMessages((current) => [...current, { id: userId, role: "user", content: text }]);
      runTurn(text);
    },
    [runTurn],
  );

  // 17.5b: resend the last customer text, without a second customer bubble;
  // the partial reply of the failed turn (never stored) is replaced.
  const retry = useCallback(() => {
    const text = lastTextRef.current;
    if (text === null || streamingRef.current) {
      return;
    }
    const partialId = replyIdRef.current;
    setMessages((current) => current.filter((message) => message.id !== partialId));
    runTurn(text);
  }, [runTurn]);

  return {
    messages,
    historyStatus,
    historySpinner,
    reloadHistory: loadHistory,
    streaming,
    failure,
    expired,
    hasConversation,
    status,
    replyStarted,
    draft,
    setDraft,
    send,
    retry,
  };
}

interface AssistantChatProps {
  conversation: AssistantConversation;
  inputRef?: Ref<HTMLTextAreaElement>;
  /** Runs on every card link click (the widget minimises on a phone). */
  onCardNavigate?: () => void;
  /** Runs after the customer sends (17.7c: the widget moves focus). */
  onSend?: () => void;
  /** Desktop layout: button tooltips (`title`) show only there (17.3c). */
  isDesktop?: boolean;
}

// 17.7c (mockup "AI 8"): one full-width block per failure, the 17.5b text
// on top and its action below, full width. Colours are the amber notice
// of client/VerificationBanner.tsx; the action keeps the widget's button
// style. Still one `role="status"` container, as in 17.5b. 17.7h uses the
// same block for a failed history load.
function FailureBlock({ failure, onRetry }: { failure: Failure; onRetry: () => void }) {
  return (
    <div
      role="status"
      className="flex flex-col gap-2 rounded-xl border border-amber-300 bg-amber-50 p-4 text-amber-950 dark:border-amber-700 dark:bg-amber-950 dark:text-amber-100"
    >
      <p>{failure.text}</p>
      {failure.retry ? (
        <button
          type="button"
          onClick={onRetry}
          className="min-h-11 w-full rounded border border-zinc-300 px-3 text-sm text-zinc-900 dark:border-zinc-600 dark:text-zinc-100"
        >
          Повторити
        </button>
      ) : null}
      {failure.link !== null ? (
        <Link
          href={failure.link.href}
          className="flex min-h-11 w-full items-center justify-center rounded border border-zinc-300 px-3 text-sm text-zinc-900 dark:border-zinc-600 dark:text-zinc-100"
        >
          {failure.link.label}
        </Link>
      ) : null}
    </div>
  );
}

export default function AssistantChat({
  conversation,
  inputRef,
  onCardNavigate,
  onSend,
  isDesktop = false,
}: AssistantChatProps) {
  const {
    messages,
    historyStatus,
    historySpinner,
    reloadHistory,
    streaming,
    failure,
    expired,
    status,
    replyStarted,
    draft,
    setDraft,
    send,
    retry,
  } = conversation;
  const listRef = useRef<HTMLDivElement>(null);
  // 17.7i: whether the list follows new content. Set from scroll events
  // (her position before the content grew), never measured at update time.
  // The ref is read by the layout effect; the state shows the "↓" button.
  const [following, setFollowing] = useState(true);
  const followingRef = useRef(true);
  const lastScrollTopRef = useRef(0);

  function follow(next: boolean) {
    followingRef.current = next;
    setFollowing(next);
  }

  // 17.5a: a known status shows its text under the last bubble; before any
  // text or known status the dots pulse. An unknown step keeps the dots.
  const statusLine = status === null ? null : statusText(status);
  const showDots = streaming && !replyStarted && statusLine === null;
  // 17.7h: sending waits for the history; the input stays enabled, as in
  // 17.7c while a reply streams.
  const historyBlocked = historyStatus === "loading" || historyStatus === "failed";

  // 17.7i part 1: before paint, so the first painted frame already shows the
  // end; instant (a scrollTop write is never smooth). Part 2: only while
  // following.
  useLayoutEffect(() => {
    const list = listRef.current;
    if (list === null || !followingRef.current) {
      return;
    }
    list.scrollTop = list.scrollHeight;
    lastScrollTopRef.current = list.scrollTop;
  }, [messages, failure, statusLine, showDots, historyStatus]);

  // 17.7i part 2 (decided 07.11.2026): reaching within about 80 px of the
  // end, by any scroll, resumes following; she leaves the bottom only by an
  // upward scroll that ends further away. A downward scroll (the smooth "↓"
  // animation) never stops following.
  function handleScroll() {
    const list = listRef.current;
    if (list === null) {
      return;
    }
    const top = list.scrollTop;
    const movedUp = top < lastScrollTopRef.current;
    lastScrollTopRef.current = top;
    if (list.scrollHeight - list.clientHeight - top <= AT_BOTTOM_PX) {
      follow(true);
    } else if (movedUp) {
      follow(false);
    }
  }

  // 17.7i (decided 07.11.2026): "Повторити", in the turn and the history
  // failure block, resumes following like sending.
  function retryAndFollow() {
    follow(true);
    retry();
  }

  function reloadHistoryAndFollow() {
    follow(true);
    reloadHistory();
  }

  // The only smooth scroll (17.7i). jsdom has no Element.scrollTo, hence
  // the optional call.
  function scrollToEnd() {
    const list = listRef.current;
    follow(true);
    list?.scrollTo?.({ top: list.scrollHeight, behavior: "smooth" });
  }

  function submit(event?: FormEvent<HTMLFormElement>) {
    event?.preventDefault();
    // 17.7c: blocked while a reply streams, though the input is enabled;
    // 17.7h: also until the history has loaded.
    if (streaming || historyBlocked || draft.trim() === "") {
      return;
    }
    // 17.7i: sending always scrolls to the end and resumes following.
    follow(true);
    send(draft);
    onSend?.();
  }

  function handleKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    // Enter sends, Shift+Enter starts a new line.
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      submit();
    }
  }

  const failureBlock =
    failure === null ? null : <FailureBlock failure={failure} onRetry={retryAndFollow} />;

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div
        ref={listRef}
        data-testid="assistant-message-list"
        onScroll={handleScroll}
        className="flex flex-1 flex-col gap-3 overflow-y-auto px-4 py-3 text-sm"
      >
        {expired ? (
          <p className="text-center text-xs text-zinc-500 dark:text-zinc-400">{EXPIRED_NOTICE}</p>
        ) : null}
        {/* 17.7i part 1 (decided 08.11.2026): hidden while a stored
            conversation's history loads, so it appears whole, at the end. */}
        {historyStatus === "loading" ? null : (
          <p className="self-start rounded-lg bg-zinc-100 px-3 py-2 dark:bg-zinc-800">{GREETING}</p>
        )}
        {messages.length > 0 || failure !== null ? (
          <ul aria-label="Розмова" className="flex flex-col gap-3">
            {messages.map((message) =>
              message.role === "divider" ? (
                <li key={message.id}>
                  {/* A separator's content is presentational, hence the label. */}
                  <div
                    role="separator"
                    aria-label={EXPIRED_NOTICE}
                    className="flex items-center gap-2 text-xs text-zinc-500 before:h-px before:flex-1 before:bg-zinc-200 after:h-px after:flex-1 after:bg-zinc-200 dark:text-zinc-400 dark:before:bg-zinc-700 dark:after:bg-zinc-700"
                  >
                    {EXPIRED_NOTICE}
                  </div>
                </li>
              ) : (
                <li
                  key={message.id}
                  className={
                    message.role === "user"
                      ? "max-w-[85%] self-end whitespace-pre-wrap rounded-lg bg-zinc-900 px-3 py-2 text-white dark:bg-zinc-100 dark:text-zinc-900"
                      : "max-w-[85%] self-start whitespace-pre-wrap rounded-lg bg-zinc-100 px-3 py-2 dark:bg-zinc-800"
                  }
                >
                  {message.card === undefined ? (
                    message.content
                  ) : (
                    <div className="flex flex-col gap-2">
                      {message.content !== "" ? <p>{message.content}</p> : null}
                      <AssistantProposalCard card={message.card} onNavigate={onCardNavigate} />
                    </div>
                  )}
                </li>
              ),
            )}
            {/* 17.7c: the failure is the list's last item, full width. */}
            {failureBlock !== null ? <li>{failureBlock}</li> : null}
          </ul>
        ) : null}
        {historyStatus === "failed" ? (
          <FailureBlock failure={HISTORY_LOAD_FAILURE} onRetry={reloadHistoryAndFollow} />
        ) : null}
        {/* 17.7h: where the history will appear, a little above the centre. */}
        {historySpinner ? (
          <div aria-hidden="true" className="flex flex-1 items-center justify-center pb-16">
            <span
              data-testid="assistant-history-spinner"
              className="h-6 w-6 animate-spin rounded-full border-2 border-zinc-300 border-t-zinc-600 motion-reduce:animate-none dark:border-zinc-600 dark:border-t-zinc-300"
            />
          </div>
        ) : null}
        <div role="status" className="self-start text-zinc-500 dark:text-zinc-400">
          {/* 17.7h: the spinner is aria-hidden, so this is what is announced. */}
          {historySpinner ? <span className="sr-only">{HISTORY_LOADING_TEXT}</span> : null}
          {/* 17.7c: the dots are aria-hidden, so this is what is announced. */}
          {showDots ? <span className="sr-only">{TYPING_TEXT}</span> : null}
          {showDots ? (
            <span
              data-testid="assistant-typing-dots"
              aria-hidden="true"
              className="flex gap-1 rounded-lg bg-zinc-100 px-3 py-3 dark:bg-zinc-800"
            >
              {[0, 150, 300].map((delay) => (
                <span
                  key={delay}
                  className="h-1.5 w-1.5 animate-pulse rounded-full bg-zinc-400"
                  style={{ animationDelay: `${delay}ms` }}
                />
              ))}
            </span>
          ) : null}
          {statusLine !== null ? <span className="text-xs">{statusLine}</span> : null}
        </div>
      </div>

      {/* 17.7i: round, centred just above the input, only while she is not
          at the bottom. Neutral styling; the look is finished in the
          design pass. */}
      {following ? null : (
        <div className="relative h-0">
          <button
            type="button"
            aria-label={TO_BOTTOM_LABEL}
            title={isDesktop ? TO_BOTTOM_LABEL : undefined}
            onClick={scrollToEnd}
            className="absolute bottom-2 left-1/2 flex h-11 w-11 -translate-x-1/2 items-center justify-center rounded-full border border-zinc-300 bg-white text-zinc-700 shadow dark:border-zinc-600 dark:bg-zinc-800 dark:text-zinc-200"
          >
            <span aria-hidden="true">↓</span>
          </button>
        </div>
      )}

      <form onSubmit={submit} className="flex items-end gap-2 px-3 pt-3 pb-1">
        <textarea
          ref={inputRef}
          aria-label="Повідомлення"
          placeholder="Напишіть питання…"
          maxLength={MAX_MESSAGE_LENGTH}
          rows={2}
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={handleKeyDown}
          className="min-h-11 flex-1 resize-none rounded border border-zinc-300 bg-transparent px-3 py-2 text-sm dark:border-zinc-600"
        />
        <button
          type="submit"
          disabled={streaming || historyBlocked || draft.trim() === ""}
          className="min-h-11 rounded bg-zinc-900 px-4 text-sm text-white disabled:opacity-50 dark:bg-zinc-100 dark:text-zinc-900"
        >
          Надіслати
        </button>
      </form>
      {/* 17.7c (changed twice 06.11.2026): the AI notice is a quiet line
          below the input form, always visible; the whole text is the link.
          The bottom padding keeps it off the window edge and, on a phone,
          above the home indicator. */}
      <p className="px-3 pb-[calc(0.75rem+env(safe-area-inset-bottom))] text-center text-[11px] text-zinc-500 dark:text-zinc-400">
        <Link
          href={PRIVACY_HREF}
          target="_blank"
          rel="noopener noreferrer"
          className="rounded hover:underline focus-visible:underline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600"
        >
          {AI_NOTICE}
          <span className="sr-only"> {AI_NOTICE_DESTINATION}</span>
        </Link>
      </p>
    </div>
  );
}
