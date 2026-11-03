/**
 * Reads an NDJSON response body as parsed events, one JSON value per line
 * (docs/DECISIONS.md § "Stage 17 sub-step order", 17.4a; Stage 16 "Sub-step
 * 7 details").
 *
 * - Chunks do not follow line boundaries: a partial line is kept until its
 *   newline arrives.
 * - The decoder runs with `{ stream: true }`, so a multi-byte character
 *   split across chunks (all Cyrillic text) is decoded once whole.
 * - A final line without a newline is still read.
 * - Blank lines are skipped. A malformed line raises; the caller treats it
 *   like any other broken stream.
 *
 * If the consumer stops early (break, abort), the stream is cancelled.
 */
export async function* readNdjson(body: ReadableStream<Uint8Array>): AsyncGenerator<unknown> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let finished = false;

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) {
        break;
      }
      buffer += decoder.decode(value, { stream: true });

      let newline = buffer.indexOf("\n");
      while (newline !== -1) {
        const line = buffer.slice(0, newline);
        buffer = buffer.slice(newline + 1);
        if (line.trim() !== "") {
          yield JSON.parse(line);
        }
        newline = buffer.indexOf("\n");
      }
    }

    buffer += decoder.decode();
    finished = true;
    if (buffer.trim() !== "") {
      yield JSON.parse(buffer);
    }
  } finally {
    if (!finished) {
      await reader.cancel().catch(() => undefined);
    }
    reader.releaseLock();
  }
}
