"use client";

/**
 * The assistant conversation (docs/DECISIONS.md § "Stage 17 sub-step
 * order", 17.4b; block B).
 *
 * `useAssistantConversation` holds the conversation; AssistantWidget calls
 * it, so the conversation and a streaming reply survive minimise and pages
 * where the widget renders nothing. `AssistantChat` only renders it.
 *
 * - History loads once per mount, only when a session id is stored. Empty
 *   history means the conversation expired: the id is dropped at once and
 *   the expiry waits in storage (17.7a) until the chat is open on an
 *   allowed page, where the notice shows once. A failed request keeps the
 *   id and shows the plain greeting.
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
 *   text, "Спробувати ще раз" where offered, and the "book it yourself"
 *   link where offered (lib/assistant/failure.ts). Retry resends the last
 *   customer text without a second bubble and replaces the partial reply.
 * - The request is aborted on unmount.
 * - The draft (17.7b) lives in the hook and in sessionStorage
 *   (lib/assistant/draft.ts): written on every change, cleared on send
 *   whatever the outcome, kept when the conversation expires. Retry resends
 *   the earlier text and leaves the draft alone.
 */

import {
  useCallback,
  useEffect,
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
import { failureFor, type Failure, type FailureCause } from "@/lib/assistant/failure";
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

// Same limit as the backend serializer (ai_assistant/serializers.py).
const MAX_MESSAGE_LENGTH = 2000;

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

export interface AssistantConversation {
  messages: ChatEntry[];
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
  const [messages, setMessages] = useState<ChatEntry[]>([]);
  const [streaming, setStreaming] = useState(false);
  const [failure, setFailure] = useState<Failure | null>(null);
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
  const nextIdRef = useRef(0);
  // The last customer text and the reply bubble of the last turn, for retry.
  const lastTextRef = useRef<string | null>(null);
  const replyIdRef = useRef<number | null>(null);
  // The customer bubble of the last turn: a divider found on send goes
  // above it (17.7a).
  const userIdRef = useRef<number | null>(null);
  // The chat holds a conversation (restored history or a finished turn), so
  // opening it or returning to the tab re-checks history (17.7a).
  const liveRef = useRef(false);
  const mountedRef = useRef(true);

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

  useEffect(() => {
    const stored = readSessionId();
    sessionIdRef.current = stored;
    if (stored === null) {
      return;
    }
    let active = true;
    getAssistantHistory(slug, stored).then(
      (history) => {
        if (!active) {
          return;
        }
        if (history.length === 0) {
          // Cleared at once so the booking source stays honest; the notice
          // waits in storage until the chat is opened on an allowed page.
          clearSessionId();
          sessionIdRef.current = null;
          markConversationExpired();
          setNoticeOwed(true);
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
        // Prepended: a message sent before history arrived stays last.
        setMessages((current) => [...restored, ...current]);
        liveRef.current = true;
        setHasConversation(true);
      },
      () => {
        // Failed request: keep the id, show the plain greeting.
      },
    );
    return () => {
      active = false;
    };
  }, [slug]);

  /**
   * 17.7a, a page that stays loaded: asks whether the held conversation is
   * still alive. Empty history means it expired: the old messages stay, a
   * divider goes at the bottom, the id is dropped. A live conversation or a
   * failed request changes nothing. Skipped while a reply streams, and the
   * answer is ignored if a turn has started or the id changed meanwhile.
   */
  const recheck = useCallback(() => {
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
  }, [slug]);

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
      if (text === "" || streamingRef.current) {
        return;
      }
      lastTextRef.current = text;
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
}

export default function AssistantChat({
  conversation,
  inputRef,
  onCardNavigate,
}: AssistantChatProps) {
  const {
    messages,
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
  const endRef = useRef<HTMLDivElement>(null);

  // 17.5a: a known status shows its text under the last bubble; before any
  // text or known status the dots pulse. An unknown step keeps the dots.
  const statusLine = status === null ? null : statusText(status);
  const showDots = streaming && !replyStarted && statusLine === null;

  useEffect(() => {
    // jsdom has no scrollIntoView, hence the optional call.
    endRef.current?.scrollIntoView?.({ block: "end" });
  }, [messages, failure, statusLine, showDots]);

  function submit(event?: FormEvent<HTMLFormElement>) {
    event?.preventDefault();
    if (streaming || draft.trim() === "") {
      return;
    }
    send(draft);
  }

  function handleKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    // Enter sends, Shift+Enter starts a new line.
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      submit();
    }
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex flex-1 flex-col gap-3 overflow-y-auto px-4 py-3 text-sm">
        {expired ? (
          <p className="text-center text-xs text-zinc-500 dark:text-zinc-400">{EXPIRED_NOTICE}</p>
        ) : null}
        <p className="self-start rounded-lg bg-zinc-100 px-3 py-2 dark:bg-zinc-800">{GREETING}</p>
        {messages.length > 0 ? (
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
          </ul>
        ) : null}
        <div role="status" className="self-start text-zinc-500 dark:text-zinc-400">
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
        {failure !== null ? (
          <div
            role="status"
            className="flex flex-col items-start gap-2 text-zinc-600 dark:text-zinc-400"
          >
            <p>{failure.text}</p>
            {failure.retry ? (
              <button
                type="button"
                onClick={retry}
                className="min-h-11 rounded border border-zinc-300 px-3 text-sm text-zinc-900 dark:border-zinc-600 dark:text-zinc-100"
              >
                Спробувати ще раз
              </button>
            ) : null}
            {failure.link !== null ? (
              <Link href={failure.link.href} className="underline">
                {failure.link.label}
              </Link>
            ) : null}
          </div>
        ) : null}
        <div ref={endRef} />
      </div>

      <form
        onSubmit={submit}
        className="flex items-end gap-2 border-t border-zinc-200 p-3 dark:border-zinc-700"
      >
        <textarea
          ref={inputRef}
          aria-label="Повідомлення"
          placeholder="Напишіть питання…"
          maxLength={MAX_MESSAGE_LENGTH}
          rows={2}
          value={draft}
          disabled={streaming}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={handleKeyDown}
          className="min-h-11 flex-1 resize-none rounded border border-zinc-300 bg-transparent px-3 py-2 text-sm disabled:opacity-60 dark:border-zinc-600"
        />
        <button
          type="submit"
          disabled={streaming || draft.trim() === ""}
          className="min-h-11 rounded bg-zinc-900 px-4 text-sm text-white disabled:opacity-50 dark:bg-zinc-100 dark:text-zinc-900"
        >
          Надіслати
        </button>
      </form>
    </div>
  );
}
