import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, askRepo } from "../api";
import type { AskEvent } from "../types";

function sseResponse(frames: string[]): Response {
  const encoder = new TextEncoder();
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const f of frames) controller.enqueue(encoder.encode(f));
      controller.close();
    },
  });
  return new Response(stream, {
    status: 200,
    headers: { "content-type": "text/event-stream" },
  });
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("askRepo", () => {
  it("parses SSE frames into events, across chunk boundaries", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        sseResponse([
          'event: status\ndata: {"text":"searching sessions"}\n\n',
          'event: delta\ndata: {"te',
          'xt":"Because."}\n\nevent: done\ndata: {"best_effort":false}\n\n',
        ]),
      ),
    );
    const events: AskEvent[] = [];
    await askRepo("alice", "x", "why?", (e) => events.push(e));
    expect(events).toEqual([
      { kind: "status", text: "searching sessions" },
      { kind: "delta", text: "Because." },
      { kind: "done", best_effort: false },
    ]);
    const call = vi.mocked(fetch).mock.calls[0];
    expect(call[0]).toBe("/api/repos/alice/x/ask");
    expect(JSON.parse((call[1] as RequestInit).body as string)).toEqual({
      question: "why?",
    });
  });

  it("throws ApiError on non-2xx", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response("rate_limited", { status: 429 }),
      ),
    );
    await expect(
      askRepo("alice", "x", "why?", () => {}),
    ).rejects.toBeInstanceOf(ApiError);
  });
});
