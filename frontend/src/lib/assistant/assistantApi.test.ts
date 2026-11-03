import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/lib/api/errors";

import { getAssistantHistory, sendAssistantMessage } from "./assistantApi";

// docs/DECISIONS.md § "Stage 17 sub-step order", 17.4a, and Stage 16
// "Sub-step 7 details": the assistant endpoints are public and ignore
// cookies, so requests carry no credentials and use NEXT_PUBLIC_API_URL,
// like the guest client. Node environment: real Response and ReadableStream.

const API_BASE = "http://localhost:8000";
const MESSAGES_URL = `${API_BASE}/api/v1/salons/bella-demo/assistant/messages/?lang=uk`;
const HISTORY_URL = `${API_BASE}/api/v1/salons/bella-demo/assistant/history/`;

const encoder = new TextEncoder();

function streamResponse(lines: string[], sessionId: string | null = "server-id"): Response {
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const line of lines) {
        controller.enqueue(encoder.encode(line));
      }
      controller.close();
    },
  });
  const headers = new Headers({ "Content-Type": "application/x-ndjson" });
  if (sessionId !== null) {
    headers.set("X-Assistant-Session", sessionId);
  }
  return new Response(body, { status: 200, headers });
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function lastCall(): { url: string; init: RequestInit; headers: Headers } {
  const calls = vi.mocked(fetch).mock.calls;
  const [url, init = {}] = calls[calls.length - 1] as [string, RequestInit | undefined];
  return { url, init, headers: new Headers(init.headers) };
}

async function collect(events: AsyncIterable<unknown>): Promise<unknown[]> {
  const result: unknown[] = [];
  for await (const event of events) {
    result.push(event);
  }
  return result;
}

beforeEach(() => {
  process.env.NEXT_PUBLIC_API_URL = API_BASE;
  vi.stubGlobal("fetch", vi.fn());
});

describe("sendAssistantMessage", () => {
  it("test_posts_to_the_messages_url_with_lang_uk", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(streamResponse(['{"type":"done"}\n']));

    await sendAssistantMessage("bella-demo", "Привіт", null);

    const { url, init } = lastCall();
    expect(url).toBe(MESSAGES_URL);
    expect(init.method).toBe("POST");
  });

  it("test_sends_the_message_as_json", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(streamResponse(['{"type":"done"}\n']));

    await sendAssistantMessage("bella-demo", "Скільки коштує манікюр?", null);

    const { init, headers } = lastCall();
    expect(JSON.parse(init.body as string)).toEqual({ message: "Скільки коштує манікюр?" });
    expect(headers.get("Content-Type")).toBe("application/json");
  });

  it("test_sends_the_session_header_when_an_id_is_given", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(streamResponse(['{"type":"done"}\n']));

    await sendAssistantMessage("bella-demo", "Привіт", "stored-id");

    expect(lastCall().headers.get("X-Assistant-Session")).toBe("stored-id");
  });

  it("test_omits_the_session_header_without_an_id", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(streamResponse(['{"type":"done"}\n']));

    await sendAssistantMessage("bella-demo", "Привіт", null);

    expect(lastCall().headers.has("X-Assistant-Session")).toBe(false);
  });

  it("test_sends_no_credentials", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(streamResponse(['{"type":"done"}\n']));

    await sendAssistantMessage("bella-demo", "Привіт", null);

    expect(lastCall().init.credentials).toBe("omit");
  });

  it("test_returns_the_response_session_id_and_the_events", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(
      streamResponse(['{"type":"text","text":"Вітаю"}\n', '{"type":"done"}\n'], "new-id"),
    );

    const result = await sendAssistantMessage("bella-demo", "Привіт", null);

    expect(result.sessionId).toBe("new-id");
    expect(await collect(result.events)).toEqual([
      { type: "text", text: "Вітаю" },
      { type: "done" },
    ]);
  });

  it("test_passes_the_given_abort_signal_to_fetch", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(streamResponse(['{"type":"done"}\n']));
    const controller = new AbortController();

    await sendAssistantMessage("bella-demo", "Привіт", null, controller.signal);

    expect(lastCall().init.signal).toBe(controller.signal);
  });

  it("test_non_ok_response_throws_api_error_with_code_and_status", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(
      jsonResponse(
        { error: { code: "assistant_busy", message: "The assistant is busy.", details: {} } },
        503,
      ),
    );

    const sending = sendAssistantMessage("bella-demo", "Привіт", null);

    await expect(sending).rejects.toBeInstanceOf(ApiError);
    await expect(sending).rejects.toMatchObject({ code: "assistant_busy", status: 503 });
  });
});

describe("getAssistantHistory", () => {
  it("test_gets_history_with_the_session_header_and_returns_the_messages", async () => {
    const messages = [
      { role: "user", content: "Привіт" },
      { role: "assistant", content: "Вітаю!" },
    ];
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse({ messages }));

    const result = await getAssistantHistory("bella-demo", "stored-id");

    const { url, headers } = lastCall();
    expect(url).toBe(HISTORY_URL);
    expect(headers.get("X-Assistant-Session")).toBe("stored-id");
    expect(result).toEqual(messages);
  });

  it("test_without_an_id_returns_empty_and_does_not_fetch", async () => {
    const result = await getAssistantHistory("bella-demo", null);

    expect(result).toEqual([]);
    expect(fetch).not.toHaveBeenCalled();
  });
});
