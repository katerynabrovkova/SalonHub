/**
 * Browser client for the assistant endpoints (docs/DECISIONS.md § "Stage 17
 * sub-step order", 17.4a; Stage 16 "Sub-step 7 details"):
 *
 *   POST /api/v1/salons/<slug>/assistant/messages/?lang=<site language>  (NDJSON stream)
 *   GET  /api/v1/salons/<slug>/assistant/history/
 *
 * Both endpoints are public and ignore cookies, so requests carry no
 * credentials and use NEXT_PUBLIC_API_URL, like the guest client
 * (lib/api/guestClient.ts). apiRequest is not reused: it always parses
 * JSON, sends credentials and CSRF, and renews sessions on 401.
 *
 * The session id travels in X-Assistant-Session both ways. Reading it from
 * a cross-origin response works only because the backend exposes it
 * (CORS_EXPOSE_HEADERS); mocked tests cannot prove that, the real-browser
 * check in 17.4b does.
 */
import { apiErrorFromBody } from "@/lib/api/errors";
import { getSiteLanguage } from "@/lib/i18n/siteLanguage";

import { readNdjson } from "./readNdjson";

const SESSION_HEADER = "X-Assistant-Session";

/**
 * Stream events as the backend sends them today. The backend may add types
 * later; the widget ignores any type it does not know.
 */
export type AssistantEvent =
  | { type: "text"; text: string }
  | { type: "status"; step: string }
  | { type: "proposal"; card: unknown }
  | { type: "error"; code: string }
  | { type: "done" };

export interface AssistantHistoryMessage {
  role: "user" | "assistant";
  content: string;
  // Present on an assistant message whose turn produced a proposal card
  // (Stage 17.2b); shown from 17.6.
  card?: unknown;
}

export interface AssistantReply {
  /** The response's X-Assistant-Session; store it only after `done`. */
  sessionId: string | null;
  events: AsyncIterable<AssistantEvent>;
}

function salonUrl(slug: string, path: string): string {
  return `${process.env.NEXT_PUBLIC_API_URL}/api/v1/salons/${slug}${path}`;
}

async function readBody(response: Response): Promise<unknown> {
  return response.json().catch(() => null);
}

async function* noEvents(): AsyncGenerator<AssistantEvent> {
  // A 200 without a body: the stream simply has no events.
}

/**
 * Sends one customer message. Resolves once the response headers arrive;
 * the reply is read from `events`. A refusal before the stream (400, 404,
 * 429, 503) rejects with ApiError carrying the envelope's code and the
 * HTTP status.
 */
export async function sendAssistantMessage(
  slug: string,
  message: string,
  sessionId: string | null,
  signal?: AbortSignal,
): Promise<AssistantReply> {
  const headers = new Headers({ "Content-Type": "application/json" });
  if (sessionId !== null) {
    headers.set(SESSION_HEADER, sessionId);
  }

  // The site language, the same as `<html lang>` and the widget's salon fetch.
  const lang = encodeURIComponent(getSiteLanguage());
  const response = await fetch(salonUrl(slug, `/assistant/messages/?lang=${lang}`), {
    method: "POST",
    headers,
    body: JSON.stringify({ message }),
    credentials: "omit",
    signal,
  });

  if (!response.ok) {
    throw apiErrorFromBody(response.status, await readBody(response));
  }

  return {
    sessionId: response.headers.get(SESSION_HEADER),
    events:
      response.body === null
        ? noEvents()
        : (readNdjson(response.body) as AsyncIterable<AssistantEvent>),
  };
}

/**
 * The stored conversation for a session id, oldest first. No id means no
 * conversation, so nothing is requested. An unknown or expired id comes
 * back as []. A non-OK response rejects with ApiError. `signal` aborts the
 * request (17.7h: the widget's 10 s timeout and unmount).
 */
export async function getAssistantHistory(
  slug: string,
  sessionId: string | null,
  signal?: AbortSignal,
): Promise<AssistantHistoryMessage[]> {
  if (sessionId === null) {
    return [];
  }

  const response = await fetch(salonUrl(slug, "/assistant/history/"), {
    headers: { [SESSION_HEADER]: sessionId },
    credentials: "omit",
    signal,
  });
  const body = await readBody(response);

  if (!response.ok) {
    throw apiErrorFromBody(response.status, body);
  }

  return (body as { messages: AssistantHistoryMessage[] }).messages;
}
