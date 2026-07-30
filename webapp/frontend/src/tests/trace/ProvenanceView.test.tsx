import { describe, it, expect, vi } from "vitest";
import { render, screen, within, fireEvent } from "@testing-library/react";

import { ProvenanceView } from "../../components/trace/ProvenanceView";
import { buildProvenance } from "../../components/trace/provenance";
import type { Session, StreamEvent } from "../../components/trace/types";
import type { ToolResult } from "../../components/trace/types";
import type { TraceDigest } from "../../types";

function ev(): StreamEvent[] {
  return [
    { kind: "user_prompt", text: "tweak the css", ts: "2026-06-13T10:00:00Z", uuid: "p1" },
    {
      kind: "tool_use",
      name: "Edit",
      input: { file_path: "/r/faq.module.css", old_string: "a", new_string: "b" },
      id: "id-t1",
      ts: "2026-06-13T10:00:01Z",
      msgId: "m1",
      uuid: "t1",
      result: null,
    },
  ];
}

function retriedStream(): StreamEvent[] {
  return [
    { kind: "user_prompt", text: "write it", ts: "2026-06-13T10:00:00Z", uuid: "p1" },
    {
      kind: "tool_use",
      name: "Write",
      input: { file_path: "/r/a.ts", content: "v1" },
      id: "id-t1",
      ts: "2026-06-13T10:00:01Z",
      msgId: "m1",
      uuid: "t1",
      result: { content: "File has not been read yet.", isError: true },
    },
    {
      kind: "tool_use",
      name: "Read",
      input: { file_path: "/r/a.ts" },
      id: "id-t2",
      ts: "2026-06-13T10:00:02Z",
      msgId: "m1",
      uuid: "t2",
      result: null,
    },
    {
      kind: "tool_use",
      name: "Write",
      input: { file_path: "/r/a.ts", content: "v1" },
      id: "id-t3",
      ts: "2026-06-13T10:00:03Z",
      msgId: "m1",
      uuid: "t3",
      result: null,
    },
  ];
}

function netResult(originalFile: string | null, content: string): ToolResult {
  return {
    content: "ok",
    toolUseResult: {
      ...(originalFile === null ? {} : { originalFile }),
      content,
    },
  };
}

function netStream(): StreamEvent[] {
  return [
    { kind: "user_prompt", text: "edit it", ts: "2026-06-13T10:00:00Z", uuid: "p1" },
    {
      kind: "tool_use",
      name: "Edit",
      input: { file_path: "/r/a.ts", old_string: "b", new_string: "x" },
      id: "id-t1",
      ts: "2026-06-13T10:00:01Z",
      msgId: "m1",
      uuid: "t1",
      result: netResult("a\nb\nc", "a\nx\nc"),
    },
  ];
}

function session(stream: StreamEvent[] = ev()): Session {
  return {
    stream,
    meta: {
      sessionId: "s1", aiTitle: null, firstPrompt: null, cwd: "/r",
      gitBranch: null, model: null, modelLabel: null, sourceFormat: null,
      version: null, permissionMode: null, startedAt: null, endedAt: null,
      prLink: null, tokens: { input: 0, cacheCreate: 0, cacheRead: 0, output: 0 },
      assistantThinkMs: 0, toolCounts: {}, toolCallCount: 0, userPromptCount: 1,
      assistantTextCount: 0, agents: [],
    },
  };
}

const digest: TraceDigest = {
  ask: "a", decisions: ["b"], dead_ends: ["e"], learnings: [], tests: "d",
  chapters: [],
  file_notes: [{ path: "/r/faq.module.css", caption: "Tint hover states" }],
};

describe("ProvenanceView merged blocks", () => {
  it("shows the digest caption and renders no hunk boxes", () => {
    const model = buildProvenance(session(), [], "claude-code");
    render(
      <ProvenanceView
        model={model}
        session={session()}
        subagentsLoading={false}
        digest={digest}
        onJump={() => {}}
        onJumpChapter={vi.fn()}
      />,
    );
    expect(screen.getByText("Tint hover states")).toBeInTheDocument();
    expect(document.querySelector(".prov-hunk")).toBeNull();
    expect(document.querySelector(".prov-htitle")).toBeNull();
  });

  it("makes blame rows keyboard-focusable buttons", () => {
    const model = buildProvenance(session(), [], "claude-code");
    render(
      <ProvenanceView
        model={model}
        session={session()}
        subagentsLoading={false}
        digest={digest}
        onJump={() => {}}
        onJumpChapter={vi.fn()}
      />,
    );
    const rows = document.querySelectorAll('.prov-ln[role="button"]');
    expect(rows.length).toBeGreaterThan(0);
    expect((rows[0] as HTMLElement).tabIndex).toBe(0);
  });

  it("marks rows of a retried region with the retry class + title", () => {
    const model = buildProvenance(session(retriedStream()), [], "claude-code");
    render(
      <ProvenanceView
        model={model}
        session={session(retriedStream())}
        subagentsLoading={false}
        digest={digest}
        onJump={() => {}}
        onJumpChapter={vi.fn()}
      />,
    );
    const retriedRow = document.querySelector(".prov-ln.retried") as HTMLElement | null;
    expect(retriedRow).not.toBeNull();
    expect(retriedRow?.getAttribute("title")).toBe("This edit was retried");
  });

  it("renders no caption when the digest has none for the file", () => {
    const model = buildProvenance(session(), [], "claude-code");
    render(
      <ProvenanceView
        model={model}
        session={session()}
        subagentsLoading={false}
        digest={null}
        onJump={() => {}}
        onJumpChapter={vi.fn()}
      />,
    );
    expect(document.querySelector(".prov-fcaption")).toBeNull();
  });
});

describe("ProvenanceView net diff", () => {
  function renderNet() {
    const model = buildProvenance(session(netStream()), [], "claude-code");
    render(
      <ProvenanceView
        model={model}
        session={session(netStream())}
        subagentsLoading={false}
        digest={null}
        onJump={() => {}}
        onJumpChapter={vi.fn()}
      />,
    );
  }

  it("renders one consolidated diff with net add/del rows", () => {
    renderNet();
    const code = document.querySelector(".prov-code.net");
    expect(code).not.toBeNull();
    const add = [...document.querySelectorAll(".diff-row.diff-add")].find((el) =>
      el.textContent?.includes("x"),
    );
    expect(add).toBeTruthy();
    expect(document.querySelector(".diff-row.diff-del")).not.toBeNull();
  });

  it("makes added rows clickable buttons", () => {
    renderNet();
    const btn = document.querySelector('.diff-row.net-click[role="button"]');
    expect(btn).not.toBeNull();
    expect((btn as HTMLElement).tabIndex).toBe(0);
  });

  it("shows the net change counts in the file header", () => {
    renderNet();
    // The FilesIndex summary line also renders the aggregate +1/−1, so scope
    // the assertion to the file header (.prov-fhead) the test is about.
    const head = document.querySelector(".prov-fhead") as HTMLElement;
    expect(head).not.toBeNull();
    expect(within(head).getByText("+1")).toBeInTheDocument();
    expect(within(head).getByText("−1")).toBeInTheDocument();
  });
});

describe("ProvenanceView net panel", () => {
  function renderNet() {
    const model = buildProvenance(session(netStream()), [], "claude-code");
    render(
      <ProvenanceView
        model={model}
        session={session(netStream())}
        subagentsLoading={false}
        digest={null}
        onJump={() => {}}
        onJumpChapter={vi.fn()}
      />,
    );
  }

  it("opens the attributed prompt chain when an added line is clicked", () => {
    renderNet();
    // There is exactly one added line ("x"); target it specifically rather
    // than the first net-click row (which is the context row "a" and opens the
    // file-level panel instead of the per-op provenance chain).
    const add = document.querySelector(".diff-row.diff-add") as HTMLElement;
    fireEvent.click(add);
    // The per-op chain renders an "Instruction №1 · …" heading; the file-level
    // panel uses "Prompts that touched this file" and never says "Instruction",
    // so this proves the add-row click opened the per-op chain.
    expect(screen.getByText(/Instruction/)).toBeInTheDocument();
  });

  it("opens the file-level view when a context line is clicked", () => {
    renderNet();
    const ctx = [...document.querySelectorAll(".diff-row.diff-ctx")].find((el) =>
      el.textContent?.includes("a"),
    ) as HTMLElement;
    fireEvent.click(ctx);
    expect(screen.getByText(/Prompts that touched this file/)).toBeInTheDocument();
  });
});

describe("ProvenanceView chapter chips", () => {
  // ev()'s Edit lands at main-stream index 1, inside the span of a chapter
  // anchored at the prompt "p1" (index 0), so the file's surviving hunk
  // resolves to that chapter.
  const chapterDigest: TraceDigest = {
    ask: "a", decisions: ["b"], dead_ends: ["e"], learnings: [], tests: "d",
    chapters: [{ anchor_uuid: "p1", title: "Flip default behavior", caption: "" }],
    file_notes: [],
  };

  it("labels a changed file with the chapter that produced it and jumps on click", () => {
    const model = buildProvenance(session(), [], "claude-code");
    const onJumpChapter = vi.fn();
    render(
      <ProvenanceView
        model={model}
        session={session()}
        subagentsLoading={false}
        digest={chapterDigest}
        onJump={vi.fn()}
        onJumpChapter={onJumpChapter}
      />,
    );
    const chip = screen.getByRole("button", { name: /flip default behavior/i });
    fireEvent.click(chip);
    expect(onJumpChapter).toHaveBeenCalledWith("p1");
  });
});
