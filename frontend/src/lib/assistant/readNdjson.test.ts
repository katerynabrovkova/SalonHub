import { describe, expect, it } from "vitest";

import { readNdjson } from "./readNdjson";

// docs/DECISIONS.md § "Stage 17 sub-step order", 17.4a. Runs in vitest's
// default node environment on purpose: jsdom replaces TextDecoder with its
// own, while ReadableStream stays Node's (17.4 recon).

const encoder = new TextEncoder();

function streamOf(chunks: Uint8Array[]): ReadableStream<Uint8Array> {
  return new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) {
        controller.enqueue(chunk);
      }
      controller.close();
    },
  });
}

function textChunks(...parts: string[]): ReadableStream<Uint8Array> {
  return streamOf(parts.map((part) => encoder.encode(part)));
}

async function collect(stream: ReadableStream<Uint8Array>): Promise<unknown[]> {
  const events: unknown[] = [];
  for await (const event of readNdjson(stream)) {
    events.push(event);
  }
  return events;
}

describe("readNdjson", () => {
  it("test_one_event_per_chunk", async () => {
    const events = await collect(
      textChunks('{"type":"text","text":"Hi"}\n', '{"type":"done"}\n'),
    );

    expect(events).toEqual([{ type: "text", text: "Hi" }, { type: "done" }]);
  });

  it("test_several_events_in_one_chunk", async () => {
    const events = await collect(
      textChunks('{"type":"text","text":"a"}\n{"type":"text","text":"b"}\n{"type":"done"}\n'),
    );

    expect(events).toEqual([
      { type: "text", text: "a" },
      { type: "text", text: "b" },
      { type: "done" },
    ]);
  });

  it("test_one_line_split_across_chunks", async () => {
    const events = await collect(
      textChunks('{"type":"te', 'xt","text":"He', 'llo"}\n{"type":"done"}\n'),
    );

    expect(events).toEqual([{ type: "text", text: "Hello" }, { type: "done" }]);
  });

  it("test_cyrillic_character_split_mid_byte_across_chunks", async () => {
    const bytes = encoder.encode('{"type":"text","text":"Привіт"}\n');
    // "П" is two bytes in UTF-8; split between them.
    const split = encoder.encode('{"type":"text","text":"').length + 1;

    const events = await collect(streamOf([bytes.slice(0, split), bytes.slice(split)]));

    expect(events).toEqual([{ type: "text", text: "Привіт" }]);
  });

  it("test_final_line_without_newline_is_read", async () => {
    const events = await collect(textChunks('{"type":"text","text":"a"}\n{"type":"done"}'));

    expect(events).toEqual([{ type: "text", text: "a" }, { type: "done" }]);
  });

  it("test_blank_lines_are_skipped", async () => {
    const events = await collect(
      textChunks('\n{"type":"text","text":"a"}\n\n  \n{"type":"done"}\n\n'),
    );

    expect(events).toEqual([{ type: "text", text: "a" }, { type: "done" }]);
  });

  it("test_malformed_json_raises", async () => {
    await expect(collect(textChunks('{"type":"text","text":"a"}\n{not json}\n'))).rejects.toThrow();
  });
});
