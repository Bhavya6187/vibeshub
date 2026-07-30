import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { Thread } from "../../components/trace/Thread";
import type { Session } from "../../components/trace/types";
import type { TraceDigest } from "../../types";

function makeSession(): Session {
  return {
    meta: {
      sessionId: "s", aiTitle: null, firstPrompt: null, cwd: "/repo",
      gitBranch: null, model: null, modelLabel: null, sourceFormat: null,
      version: null, permissionMode: null, startedAt: null, endedAt: null,
      prLink: null,
      tokens: { input: 0, cacheCreate: 0, cacheRead: 0, output: 0 },
      assistantThinkMs: 0, toolCounts: {}, toolCallCount: 1,
      userPromptCount: 1, assistantTextCount: 0, agents: [],
    },
    stream: [
      { kind: "user_prompt", text: "do it", ts: "2026-01-01T00:00:00Z", uuid: "p1" },
      {
        kind: "tool_use", name: "Bash", input: { command: "ls" },
        id: "tool1", ts: "2026-01-01T00:00:05Z", msgId: "m1", uuid: "tool1",
        result: null,
      },
    ],
  };
}

const digest: TraceDigest = {
  ask: "a", decisions: ["d"], dead_ends: ["e"], learnings: [], tests: "t",
  chapters: [{ anchor_uuid: "tool1", title: "Run it", caption: "Runs the command." }],
};

function makeMidRunSession(): Session {
  return {
    meta: {
      sessionId: "s", aiTitle: null, firstPrompt: null, cwd: "/repo",
      gitBranch: null, model: null, modelLabel: null, sourceFormat: null,
      version: null, permissionMode: null, startedAt: null, endedAt: null,
      prLink: null,
      tokens: { input: 0, cacheCreate: 0, cacheRead: 0, output: 0 },
      assistantThinkMs: 0, toolCounts: {}, toolCallCount: 3,
      userPromptCount: 1, assistantTextCount: 0, agents: [],
    },
    stream: [
      { kind: "user_prompt", text: "do it", ts: "2026-01-01T00:00:00Z", uuid: "p1" },
      {
        kind: "tool_use", name: "Bash", input: { command: "ls" },
        id: "t0", ts: "2026-01-01T00:00:01Z", msgId: "m1", uuid: "t0",
        result: null,
      },
      {
        kind: "tool_use", name: "Bash", input: { command: "pwd" },
        id: "t1", ts: "2026-01-01T00:00:02Z", msgId: "m1", uuid: "t1",
        result: null,
      },
      {
        kind: "tool_use", name: "Bash", input: { command: "whoami" },
        id: "t2", ts: "2026-01-01T00:00:03Z", msgId: "m1", uuid: "t2",
        result: null,
      },
    ],
  };
}

const midRunDigest: TraceDigest = {
  ask: "a", decisions: ["d"], dead_ends: ["e"], learnings: [], tests: "t",
  chapters: [{ anchor_uuid: "t1", title: "Run it", caption: "Runs the command." }],
};

function makeEditSession(): Session {
  const s = makeMidRunSession();
  s.stream = [
    { kind: "user_prompt", text: "do it", ts: "2026-01-01T00:00:00Z", uuid: "p1" },
    {
      kind: "tool_use", name: "Bash", input: { command: "ls" },
      id: "t0", ts: "2026-01-01T00:00:01Z", msgId: "m1", uuid: "t0",
      result: null,
    },
    {
      kind: "tool_use", name: "Edit",
      input: { file_path: "/repo/src/a.ts", old_string: "x", new_string: "y" },
      id: "e1", ts: "2026-01-01T00:00:02Z", msgId: "m1", uuid: "e1",
      result: null,
    },
    {
      kind: "tool_use", name: "Bash", input: { command: "pwd" },
      id: "t1", ts: "2026-01-01T00:00:03Z", msgId: "m1", uuid: "t1",
      result: null,
    },
  ] as Session["stream"];
  return s;
}

describe("Thread inline edit cards", () => {
  it("renders file edits as standalone cards outside collapsed runs", () => {
    const { container } = render(
      <Thread
        session={makeEditSession()}
        shortId="abc"
        showSystemEvents={false}
        expandToolCalls={false}
      />,
    );
    // The edit stands alone with its uuid anchor.
    expect(container.querySelector('.tool-card[data-uuid="e1"]')).not.toBeNull();
    // The surrounding Bash calls form two runs, split by the edit.
    expect(
      screen.getAllByRole("button", { name: /1 tool call/i }).length,
    ).toBe(2);
  });
});

describe("Thread chapter anchors", () => {
  it("emits a chapter divider for a tool_use anchor in a collapsed group", () => {
    const { container } = render(
      <Thread
        session={makeSession()}
        shortId="abc"
        showSystemEvents={false}
        expandToolCalls={false}
        digest={digest}
      />,
    );
    expect(container.querySelector("#chapter-tool1")).not.toBeNull();
  });

  it("splits a collapsed run into two groups around a mid-run anchor", () => {
    const { container } = render(
      <Thread
        session={makeMidRunSession()}
        shortId="abc"
        showSystemEvents={false}
        expandToolCalls={false}
        digest={midRunDigest}
      />,
    );
    expect(container.querySelector("#chapter-t1")).not.toBeNull();
    expect(
      screen.getAllByRole("button", { name: /\d+ tool call/i }).length,
    ).toBe(2);
  });
});
