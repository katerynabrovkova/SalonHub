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
 *   history means the conversation expired: notice, id dropped. A failed
 *   request keeps the id and shows the plain greeting.
 * - The session id is stored only after `done`; the backend returns an id
 *   even for a first message that fails and stores nothing (block B).
 * - Statuses (17.5a): the last status event's step is kept until the next
 *   text or the end of the turn; the view shows its text, or pulsing dots
 *   before any text or known status.
 * - Proposal and unknown events are ignored, history cards show their text
 *   only and empty messages are skipped until 17.6.
 * - Any other ending (an `error` event, a refusal before the stream, a lost
 *   connection, a stream ending without `done`) shows its 17.5b table row:
 *   text, "Спробувати ще раз" where offered, and the "book it yourself"
 *   link where offered (lib/assistant/failure.ts). Retry resends the last
 *   customer text without a second bubble and replaces the partial reply.
 * - The request is aborted on unmount.
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
import { failureFor, type Failure, type FailureCause } from "@/lib/assistant/failure";
import { clearSessionId, readSessionId, storeSessionId } from "@/lib/assistant/sessionId";
import { statusText } from "@/lib/assistant/statusText";

const GREETING =
  "Вітаю! Я допоможу дізнатися ціни, обрати послугу і знайти вільний час у нашому салоні. Що Вас цікавить?";
const EXPIRED_NOTICE = "Попередня розмова завершилась";

// Same limit as the backend serializer (ai_assistant/serializers.py).
const MAX_MESSAGE_LENGTH = 2000;

export interface ChatMessage {
  id: number;
  role: "user" | "assistant";
  content: string;
}

export interface AssistantConversation {
  messages: ChatMessage[];
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
  send: (text: string) => void;
  /** Resends the last customer text after a failure that offers retry. */
  retry: () => void;
}

export function useAssistantConversation(slug: string): AssistantConversation {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [streaming, setStreaming] = useState(false);
  const [failure, setFailure] = useState<Failure | null>(null);
  const [expired, setExpired] = useState(false);
  const [hasConversation, setHasConversation] = useState(false);
  const [status, setStatus] = useState<string | null>(null);
  const [replyStarted, setReplyStarted] = useState(false);
  const sessionIdRef = useRef<string | null>(null);
  const streamingRef = useRef(false);
  const abortRef = useRef<AbortController | null>(null);
  const nextIdRef = useRef(0);
  // The last customer text and the reply bubble of the last turn, for retry.
  const lastTextRef = useRef<string | null>(null);
  const replyIdRef = useRef<number | null>(null);

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
          clearSessionId();
          sessionIdRef.current = null;
          setExpired(true);
          return;
        }
        const restored = history
          .filter((message) => message.content !== "")
          .map((message) => ({
            id: nextIdRef.current++,
            role: message.role,
            content: message.content,
          }));
        // Prepended: a message sent before history arrived stays last.
        setMessages((current) => [...restored, ...current]);
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

      function showReply(content: string) {
        setMessages((current) =>
          current.some((message) => message.id === replyId)
            ? current.map((message) => (message.id === replyId ? { ...message, content } : message))
            : [...current, { id: replyId, role: "assistant", content }],
        );
      }

      void (async () => {
        let done = false;
        let replyText = "";
        let cause: FailureCause | null = null;
        try {
          const reply = await sendAssistantMessage(
            slug,
            text,
            sessionIdRef.current,
            controller.signal,
          );
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
              if (reply.sessionId !== null) {
                storeSessionId(reply.sessionId);
                sessionIdRef.current = reply.sessionId;
              }
              setHasConversation(true);
              break;
            } else if (event.type === "error") {
              cause = { kind: "event", code: event.code, textReceived: replyText !== "" };
              break;
            }
            // proposal and unknown events: ignored until 17.6.
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
      const userId = nextIdRef.current++;
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
    send,
    retry,
  };
}

interface AssistantChatProps {
  conversation: AssistantConversation;
  inputRef?: Ref<HTMLTextAreaElement>;
}

export default function AssistantChat({ conversation, inputRef }: AssistantChatProps) {
  const { messages, streaming, failure, expired, status, replyStarted, send, retry } =
    conversation;
  const [draft, setDraft] = useState("");
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
    setDraft("");
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
            {messages.map((message) => (
              <li
                key={message.id}
                className={
                  message.role === "user"
                    ? "max-w-[85%] self-end whitespace-pre-wrap rounded-lg bg-zinc-900 px-3 py-2 text-white dark:bg-zinc-100 dark:text-zinc-900"
                    : "max-w-[85%] self-start whitespace-pre-wrap rounded-lg bg-zinc-100 px-3 py-2 dark:bg-zinc-800"
                }
              >
                {message.content}
              </li>
            ))}
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
