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
 * - 17.4 shows text only: status, proposal and unknown events are ignored
 *   (17.5 / 17.6), history cards show their text only and empty messages
 *   are skipped until 17.6.
 * - Any failure (an `error` event, a refusal before the stream, a stream
 *   ending without `done`) shows one neutral line until 17.5.
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

import { getAssistantHistory, sendAssistantMessage } from "@/lib/assistant/assistantApi";
import { clearSessionId, readSessionId, storeSessionId } from "@/lib/assistant/sessionId";

const GREETING =
  "Вітаю! Я допоможу дізнатися ціни, обрати послугу і знайти вільний час у нашому салоні. Що Вас цікавить?";
const EXPIRED_NOTICE = "Попередня розмова завершилась";
const NEUTRAL_ERROR = "Не вдалося отримати відповідь. Спробуйте ще раз.";

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
  /** The last reply failed; shows the neutral line. */
  failed: boolean;
  /** A stored conversation had expired; shows the notice. */
  expired: boolean;
  /** At least one stored turn: the stripe's live dot (block A). */
  hasConversation: boolean;
  send: (text: string) => void;
}

export function useAssistantConversation(slug: string): AssistantConversation {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [streaming, setStreaming] = useState(false);
  const [failed, setFailed] = useState(false);
  const [expired, setExpired] = useState(false);
  const [hasConversation, setHasConversation] = useState(false);
  const sessionIdRef = useRef<string | null>(null);
  const streamingRef = useRef(false);
  const abortRef = useRef<AbortController | null>(null);
  const nextIdRef = useRef(0);

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

  const send = useCallback(
    (raw: string) => {
      const text = raw.trim();
      if (text === "" || streamingRef.current) {
        return;
      }
      const userId = nextIdRef.current++;
      const replyId = nextIdRef.current++;
      const controller = new AbortController();
      abortRef.current = controller;
      streamingRef.current = true;
      setStreaming(true);
      setFailed(false);
      setMessages((current) => [...current, { id: userId, role: "user", content: text }]);

      function showReply(content: string) {
        setMessages((current) =>
          current.some((message) => message.id === replyId)
            ? current.map((message) => (message.id === replyId ? { ...message, content } : message))
            : [...current, { id: replyId, role: "assistant", content }],
        );
      }

      void (async () => {
        let done = false;
        try {
          const reply = await sendAssistantMessage(
            slug,
            text,
            sessionIdRef.current,
            controller.signal,
          );
          let replyText = "";
          for await (const event of reply.events) {
            if (event.type === "text") {
              replyText += event.text;
              showReply(replyText);
            } else if (event.type === "done") {
              done = true;
              if (reply.sessionId !== null) {
                storeSessionId(reply.sessionId);
                sessionIdRef.current = reply.sessionId;
              }
              setHasConversation(true);
              break;
            } else if (event.type === "error") {
              break;
            }
            // status, proposal and unknown events: ignored in 17.4.
          }
        } catch {
          // A refusal, a network failure or a broken stream: handled below.
        }
        if (controller.signal.aborted) {
          return;
        }
        streamingRef.current = false;
        setStreaming(false);
        if (!done) {
          setFailed(true);
        }
      })();
    },
    [slug],
  );

  return { messages, streaming, failed, expired, hasConversation, send };
}

interface AssistantChatProps {
  conversation: AssistantConversation;
  inputRef?: Ref<HTMLTextAreaElement>;
}

export default function AssistantChat({ conversation, inputRef }: AssistantChatProps) {
  const { messages, streaming, failed, expired, send } = conversation;
  const [draft, setDraft] = useState("");
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    // jsdom has no scrollIntoView, hence the optional call.
    endRef.current?.scrollIntoView?.({ block: "end" });
  }, [messages, failed]);

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
        {failed ? (
          <p role="alert" className="text-zinc-600 dark:text-zinc-400">
            {NEUTRAL_ERROR}
          </p>
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
