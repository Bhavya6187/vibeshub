import { describe, expect, it, vi, beforeEach } from "vitest";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";

vi.mock("../../auth/AuthContext", () => ({
  useAuth: vi.fn(),
}));

import { TraceView } from "../../routes/TraceView";
import { useAuth } from "../../auth/AuthContext";

const mockUseAuth = useAuth as unknown as ReturnType<typeof vi.fn>;

const anonAuth = {
  loading: false,
  user: null,
  refresh: vi.fn(),
  signOut: vi.fn(),
};

function authAs(login: string) {
  return {
    loading: false,
    user: { id: "u1", login, name: login, avatar_url: null },
    refresh: vi.fn(),
    signOut: vi.fn(),
  };
}

const __dirname = dirname(fileURLToPath(import.meta.url));
const FIXTURE = readFileSync(
  join(__dirname, "../fixtures/sample-session.jsonl"),
  "utf-8",
);

const SHORT_ID = "abcd123456";

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="t/:shortId" element={<TraceView />} />
        <Route
          path=":owner/:repo/pull/:number/:shortId"
          element={<TraceView />}
        />
      </Routes>
    </MemoryRouter>,
  );
}

function mockFetchSequence(traceSummary: object) {
  vi.spyOn(global, "fetch").mockImplementation((input) => {
    const url = typeof input === "string" ? input : (input as Request).url;
    if (url.endsWith(`/api/traces/${SHORT_ID}`)) {
      return Promise.resolve(
        new Response(JSON.stringify(traceSummary), {
          status: 200,
          headers: { "content-type": "application/json" },
        }),
      );
    }
    if (url.endsWith(`/api/traces/${SHORT_ID}/session`)) {
      return Promise.resolve(
        new Response(FIXTURE, {
          status: 200,
          headers: { "content-type": "text/plain" },
        }),
      );
    }
    return Promise.reject(new Error(`unexpected URL: ${url}`));
  });
}


// The provenance diff is now the default mode; conversation-specific tests
// flip back via the view tabs first. The tab label carries a count chip
// ("Conversation 17 prompts"), so match by the leading label.
async function toConversation() {
  fireEvent.click(await screen.findByRole("tab", { name: /Conversation/ }));
}

describe("TraceView", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    mockUseAuth.mockReturnValue(anonAuth);
    window.history.replaceState(null, "", "/");
  });

  it("renders the hero title and at least one tool card from the parsed trace", async () => {
    mockFetchSequence({
      trace_id: "id",
      short_id: SHORT_ID,
      owner_login: "alice",
      repo_full_name: "alice/repo",
      pr_number: 7,
      pr_url: "https://github.com/alice/repo/pull/7",
      pr_title: "Add thing",
      platform: "claude-code",
      byte_size: FIXTURE.length,
      message_count: 100,
      created_at: "2026-05-17T00:00:00Z",
      is_private: false,
    });

    renderAt(`/alice/repo/pull/7/${SHORT_ID}`);

    await waitFor(() => {
      expect(
        screen.getAllByText(/SESSION ·/i).length,
      ).toBeGreaterThan(0);
    });

    // Hero title is the aiTitle from the fixture.
    const heroTitle = await screen.findByText(
      "Add startup credential smoke-check",
    );
    expect(heroTitle.tagName.toLowerCase()).toBe("h1");

    await toConversation();

    // At least one tool head button should appear.
    const toolButtons = screen.getAllByRole("button", { expanded: false });
    expect(toolButtons.length).toBeGreaterThan(0);

    // Thread controls render.
    expect(
      screen.getByRole("button", { name: /show system events/i }),
    ).toBeInTheDocument();
  });

  it("points og:image at the per-trace social card for a public trace", async () => {
    mockFetchSequence({
      trace_id: "id",
      short_id: SHORT_ID,
      owner_login: "alice",
      repo_full_name: "alice/repo",
      pr_number: 7,
      pr_title: "Add thing",
      platform: "claude-code",
      byte_size: FIXTURE.length,
      message_count: 100,
      created_at: "2026-05-17T00:00:00Z",
      is_private: false,
    });

    renderAt(`/alice/repo/pull/7/${SHORT_ID}`);
    await waitFor(() => {
      expect(screen.getAllByText(/SESSION ·/i).length).toBeGreaterThan(0);
    });

    const images = Array.from(
      document.head.querySelectorAll('meta[property="og:image"]'),
    ).map((m) => m.getAttribute("content") ?? "");
    expect(images.some((c) => c.includes(`/api/og/${SHORT_ID}.png`))).toBe(true);
  });

  it("renders an Expand tool calls toggle in the thread controls", async () => {
    mockFetchSequence({
      trace_id: "id",
      short_id: SHORT_ID,
      owner_login: "alice",
      repo_full_name: "alice/repo",
      pr_number: 7,
      pr_url: "https://github.com/alice/repo/pull/7",
      pr_title: "Add thing",
      platform: "claude-code",
      byte_size: FIXTURE.length,
      message_count: 100,
      created_at: "2026-05-17T00:00:00Z",
      is_private: false,
    });

    renderAt(`/alice/repo/pull/7/${SHORT_ID}`);
    await toConversation();

    expect(
      await screen.findByRole("button", { name: /expand tool calls/i }),
    ).toBeInTheDocument();
  });

  it("folds consecutive tool calls into group lines when Expand tool calls is off", async () => {
    mockFetchSequence({
      trace_id: "id",
      short_id: SHORT_ID,
      owner_login: "alice",
      repo_full_name: "alice/repo",
      pr_number: 7,
      pr_url: "https://github.com/alice/repo/pull/7",
      pr_title: "Add thing",
      platform: "claude-code",
      byte_size: FIXTURE.length,
      message_count: 100,
      created_at: "2026-05-17T00:00:00Z",
      is_private: false,
    });

    renderAt(`/alice/repo/pull/7/${SHORT_ID}`);
    await toConversation();

    const toggle = await screen.findByRole("button", {
      name: /expand tool calls/i,
    });

    // Off by default — runs of consecutive tool calls collapse into group lines.
    expect(
      screen.getAllByRole("button", { name: /\d+ tool call/i }).length,
    ).toBeGreaterThan(0);

    fireEvent.click(toggle);

    // On — tool cards render individually, no group summary lines remain.
    expect(
      screen.queryAllByRole("button", { name: /\d+ tool call/i }),
    ).toHaveLength(0);
  });

  it("expands a tool group to reveal the individual tool cards", async () => {
    mockFetchSequence({
      trace_id: "id",
      short_id: SHORT_ID,
      owner_login: "alice",
      repo_full_name: "alice/repo",
      pr_number: 7,
      pr_url: "https://github.com/alice/repo/pull/7",
      pr_title: "Add thing",
      platform: "claude-code",
      byte_size: FIXTURE.length,
      message_count: 100,
      created_at: "2026-05-17T00:00:00Z",
      is_private: false,
    });

    renderAt(`/alice/repo/pull/7/${SHORT_ID}`);
    await toConversation();

    // Wait for the controls to render — groups are visible by default.
    await screen.findByRole("button", { name: /expand tool calls/i });

    const groups = screen.getAllByRole("button", { name: /\d+ tool call/i });
    const before = screen.getAllByRole("button").length;

    fireEvent.click(groups[0]);

    expect(groups[0]).toHaveAttribute("aria-expanded", "true");
    expect(screen.getAllByRole("button").length).toBeGreaterThan(before);
  });

  it("renders a lone tool call as its own group by default", async () => {
    mockFetchSequence({
      trace_id: "id",
      short_id: SHORT_ID,
      owner_login: "alice",
      repo_full_name: "alice/repo",
      pr_number: 7,
      pr_url: "https://github.com/alice/repo/pull/7",
      pr_title: "Add thing",
      platform: "claude-code",
      byte_size: FIXTURE.length,
      message_count: 100,
      created_at: "2026-05-17T00:00:00Z",
      is_private: false,
    });

    renderAt(`/alice/repo/pull/7/${SHORT_ID}`);
    await toConversation();

    await screen.findByRole("button", { name: /expand tool calls/i });

    // The fixture has tool calls isolated between assistant text — each
    // renders as a group of one.
    expect(screen.getAllByText("1 tool call").length).toBeGreaterThan(0);
  });

  it("shows an error state when the trace summary fetch fails", async () => {
    vi.spyOn(global, "fetch").mockImplementation((input) => {
      const url = typeof input === "string" ? input : (input as Request).url;
      if (url.endsWith(`/api/traces/${SHORT_ID}`)) {
        return Promise.resolve(new Response("nope", { status: 500 }));
      }
      return Promise.resolve(new Response("", { status: 200 }));
    });

    renderAt(`/alice/repo/pull/7/${SHORT_ID}`);

    await waitFor(() => {
      expect(screen.getByText(/500/)).toBeInTheDocument();
    });
  });

  it("shows a sign-in gate when the trace summary returns 401", async () => {
    vi.spyOn(global, "fetch").mockImplementation((input) => {
      const url = typeof input === "string" ? input : (input as Request).url;
      if (url.endsWith(`/api/traces/${SHORT_ID}`)) {
        return Promise.resolve(
          new Response(JSON.stringify({ detail: "auth_required" }), {
            status: 401,
          }),
        );
      }
      return Promise.resolve(new Response("", { status: 200 }));
    });

    renderAt(`/alice/repo/pull/7/${SHORT_ID}`);

    const link = await screen.findByRole("link", {
      name: /sign in with github/i,
    });
    expect(link.getAttribute("href")).toContain("/api/auth/github/login");
  });

  it("shows an enable-private gate when the summary returns 403", async () => {
    vi.spyOn(global, "fetch").mockImplementation((input) => {
      const url = typeof input === "string" ? input : (input as Request).url;
      if (url.endsWith(`/api/traces/${SHORT_ID}`)) {
        return Promise.resolve(
          new Response(
            JSON.stringify({ detail: "private_scope_required" }),
            { status: 403 },
          ),
        );
      }
      return Promise.resolve(new Response("", { status: 200 }));
    });

    renderAt(`/alice/repo/pull/7/${SHORT_ID}`);

    const link = await screen.findByRole("link", {
      name: /enable private repositories/i,
    });
    expect(link.getAttribute("href")).toContain("scope=private");
  });

  it("renders a clean not-found state when the summary returns 404", async () => {
    mockUseAuth.mockReturnValue(authAs("bob"));
    vi.spyOn(global, "fetch").mockImplementation((input) => {
      const url = typeof input === "string" ? input : (input as Request).url;
      if (url.endsWith(`/api/traces/${SHORT_ID}`)) {
        return Promise.resolve(
          new Response(JSON.stringify({ detail: "not_found" }), {
            status: 404,
          }),
        );
      }
      if (url.endsWith(`/api/traces/${SHORT_ID}/session`)) {
        return Promise.resolve(
          new Response(JSON.stringify({ detail: "not_found" }), {
            status: 404,
          }),
        );
      }
      return Promise.resolve(new Response("", { status: 200 }));
    });

    renderAt(`/t/${SHORT_ID}`);

    expect(
      await screen.findByRole("heading", { name: /not found/i }),
    ).toBeInTheDocument();
    // No raw ApiError string leaks through from either fetch.
    expect(screen.queryByText(/ApiError/)).not.toBeInTheDocument();
    expect(screen.queryByText(/404/)).not.toBeInTheDocument();
  });

  it("renders a standalone trace with no repo or PR", async () => {
    mockFetchSequence({
      trace_id: "id",
      short_id: SHORT_ID,
      owner_login: "alice",
      repo_full_name: null,
      pr_number: null,
      pr_url: null,
      pr_title: null,
      platform: "claude-code",
      byte_size: FIXTURE.length,
      message_count: 100,
      created_at: "2026-05-17T00:00:00Z",
      is_private: false,
    });

    const { container } = renderAt(`/t/${SHORT_ID}`);

    await waitFor(() =>
      expect(screen.queryByText(/Loading trace/i)).not.toBeInTheDocument(),
    );
    // The hero H1 still reads the parsed aiTitle even with no repo/PR.
    const heading = await screen.findByRole("heading", {
      name: "Add startup credential smoke-check",
      level: 1,
    });
    // Eyebrow above the title shows session + Raw JSONL but never a
    // GitHub link when there's no linked PR.
    const heroSection = heading.closest("section");
    expect(heroSection).not.toBeNull();
    const heroEyebrow = heroSection!.querySelector(".hero-eyebrow");
    expect(heroEyebrow).not.toBeNull();
    expect(
      heroEyebrow!.textContent?.toLowerCase(),
    ).not.toContain("view on github");
    expect(heroEyebrow!.textContent).toContain("Raw JSONL");
    void container;
  });

  it("renders a Private badge for a private trace", async () => {
    mockFetchSequence({
      trace_id: "id",
      short_id: SHORT_ID,
      owner_login: "alice",
      repo_full_name: "alice/repo",
      pr_number: 7,
      pr_url: "https://github.com/alice/repo/pull/7",
      pr_title: "Add thing",
      platform: "claude-code",
      byte_size: FIXTURE.length,
      message_count: 100,
      created_at: "2026-05-17T00:00:00Z",
      is_private: true,
    });

    renderAt(`/alice/repo/pull/7/${SHORT_ID}`);

    const badge = await screen.findByText(/Private/);
    expect(badge).toBeInTheDocument();
    expect(badge.textContent).toContain("🔒");
  });

  it("renders the manage menu for the trace owner", async () => {
    mockUseAuth.mockReturnValue(authAs("alice"));
    mockFetchSequence({
      trace_id: "id",
      short_id: SHORT_ID,
      owner_login: "alice",
      repo_full_name: null,
      pr_number: null,
      pr_url: null,
      pr_title: null,
      platform: "claude-code",
      byte_size: FIXTURE.length,
      message_count: 100,
      created_at: "2026-05-17T00:00:00Z",
      is_private: false,
    });

    renderAt(`/t/${SHORT_ID}`);

    const trigger = await screen.findByRole("button", { name: /^owner$/i });
    expect(trigger).toBeInTheDocument();
    fireEvent.click(trigger);
    expect(
      await screen.findByRole("dialog", { name: /manage trace/i }),
    ).toBeInTheDocument();
  });

  it("hides the manage menu from a non-owner", async () => {
    mockUseAuth.mockReturnValue(authAs("bob"));
    mockFetchSequence({
      trace_id: "id",
      short_id: SHORT_ID,
      owner_login: "alice",
      repo_full_name: null,
      pr_number: null,
      pr_url: null,
      pr_title: null,
      platform: "claude-code",
      byte_size: FIXTURE.length,
      message_count: 100,
      created_at: "2026-05-17T00:00:00Z",
      is_private: false,
    });

    renderAt(`/t/${SHORT_ID}`);

    await waitFor(() =>
      expect(screen.queryByText(/Loading trace/i)).not.toBeInTheDocument(),
    );
    expect(
      screen.queryByRole("button", { name: /^owner$/i }),
    ).not.toBeInTheDocument();
  });

  it("hides the manage menu from an anonymous visitor", async () => {
    mockUseAuth.mockReturnValue(anonAuth);
    mockFetchSequence({
      trace_id: "id",
      short_id: SHORT_ID,
      owner_login: "alice",
      repo_full_name: null,
      pr_number: null,
      pr_url: null,
      pr_title: null,
      platform: "claude-code",
      byte_size: FIXTURE.length,
      message_count: 100,
      created_at: "2026-05-17T00:00:00Z",
      is_private: false,
    });

    renderAt(`/t/${SHORT_ID}`);

    await waitFor(() =>
      expect(screen.queryByText(/Loading trace/i)).not.toBeInTheDocument(),
    );
    expect(
      screen.queryByRole("button", { name: /^owner$/i }),
    ).not.toBeInTheDocument();
  });

  it("renders two outcome cards (Result, Files Touched) and surfaces tokens on the metaline", async () => {
    mockFetchSequence({
      trace_id: "id",
      short_id: SHORT_ID,
      owner_login: "alice",
      repo_full_name: "alice/repo",
      pr_number: 7,
      pr_url: "https://github.com/alice/repo/pull/7",
      pr_title: "Add thing",
      platform: "claude-code",
      byte_size: FIXTURE.length,
      message_count: 100,
      created_at: "2026-05-17T00:00:00Z",
      is_private: false,
    });

    const { container } = renderAt(`/alice/repo/pull/7/${SHORT_ID}`);

    await waitFor(() =>
      expect(screen.queryByText(/Loading trace/i)).not.toBeInTheDocument(),
    );

    // Two outcome cards now — Result and Files Touched. Tokens moved to MetaLine.
    expect(container.querySelectorAll(".outcome-card")).toHaveLength(2);

    // Neither the standalone stats strip nor the side-stack wrapper survives.
    expect(container.querySelector(".meta-strip")).toBeNull();
    expect(container.querySelector(".outcome-side")).toBeNull();

    // The Result card links straight to the PR now; session stats moved to the
    // hero badges strip, so the old stat labels no longer live in this card.
    const cards = Array.from(container.querySelectorAll(".outcome-card"));
    const [resultCard, filesCard] = cards;
    expect(resultCard.textContent).toMatch(/Linked PR #7/i);
    expect(resultCard.textContent).not.toMatch(/Active Time/i);
    expect(resultCard.textContent).not.toMatch(/Tool calls/i);
    expect(filesCard.textContent).toMatch(/Files touched/i);

    // No token bar visualization anywhere.
    expect(container.querySelector(".outcome-token-seg")).toBeNull();

    // Tokens now appears as a kv pair on the metaline below the outcome grid.
    const metaline = container.querySelector(".metaline");
    expect(metaline).not.toBeNull();
    expect(metaline!.textContent).toMatch(/tokens/i);
  });
});

describe("DigestPanel integration", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    mockUseAuth.mockReturnValue(anonAuth);
    window.history.replaceState(null, "", "/");
  });

  it("renders DigestPanel when ai_digest is present", async () => {
    const digest = {
      ask: "test ask", decisions: ["d"], dead_ends: ["e"], learnings: [], tests: "t",
      chapters: [
        { anchor_uuid: "u1", title: "Frame", caption: "ask" },
      ],
    };
    mockFetchSequence({
      trace_id: "id",
      short_id: SHORT_ID,
      owner_login: "alice",
      repo_full_name: "alice/repo",
      pr_number: 7,
      pr_url: "https://github.com/alice/repo/pull/7",
      pr_title: "Add thing",
      platform: "claude-code",
      byte_size: FIXTURE.length,
      message_count: 100,
      created_at: "2026-05-17T00:00:00Z",
      is_private: false,
      ai_digest: digest,
    });
    renderAt(`/alice/repo/pull/7/${SHORT_ID}`);
    await toConversation();
    await waitFor(() => {
      expect(screen.getByText("test ask")).toBeInTheDocument();
    });
    // The chapter title renders in the ChapterRail (and, when the anchor
    // resolves in the stream, the inline ChapterDivider). The DigestPanel
    // no longer renders chapter chips.
    expect(screen.getAllByText("Frame").length).toBeGreaterThanOrEqual(1);
  });

  it("does not render DigestPanel when ai_digest is absent", async () => {
    mockFetchSequence({
      trace_id: "id",
      short_id: SHORT_ID,
      owner_login: "alice",
      repo_full_name: "alice/repo",
      pr_number: 7,
      pr_url: "https://github.com/alice/repo/pull/7",
      pr_title: "Add thing",
      platform: "claude-code",
      byte_size: FIXTURE.length,
      message_count: 100,
      created_at: "2026-05-17T00:00:00Z",
      is_private: false,
    });
    renderAt(`/alice/repo/pull/7/${SHORT_ID}`);
    await waitFor(() => {
      expect(
        screen.getAllByText(/SESSION ·/i).length,
      ).toBeGreaterThan(0);
    });
    // The "Key decisions" label only renders inside the DigestPanel.
    // ("Ask" collides with the Ask tool-category chip in the timeline legend.)
    expect(screen.queryByText(/^Key decisions$/)).not.toBeInTheDocument();
  });

  it("renders ChapterDivider above the anchored event when chapters present", async () => {
    // 779d4aa7-6138-4b55-93bb-0747bbebb8fa is the uuid of the first user
    // prompt in webapp/frontend/src/tests/fixtures/sample-session.jsonl.
    // Picking a real fixture uuid means the Thread actually renders an
    // anchored event, and the divider sits above it.
    const digest = {
      ask: "a", decisions: ["d"], dead_ends: ["e"], learnings: [], tests: "t",
      chapters: [
        { anchor_uuid: "779d4aa7-6138-4b55-93bb-0747bbebb8fa",
          title: "Frame the ask",
          caption: "User opens the conversation." },
      ],
    };
    mockFetchSequence({
      trace_id: "id",
      short_id: SHORT_ID,
      owner_login: "alice",
      repo_full_name: "alice/repo",
      pr_number: 7,
      pr_url: "https://github.com/alice/repo/pull/7",
      pr_title: "Add thing",
      platform: "claude-code",
      byte_size: FIXTURE.length,
      message_count: 100,
      created_at: "2026-05-17T00:00:00Z",
      is_private: false,
      ai_digest: digest,
    });
    renderAt(`/alice/repo/pull/7/${SHORT_ID}`);
    await toConversation();
    // The caption renders only inside ChapterDivider. The title appears in
    // both the ChapterRail and the ChapterDivider above the anchored event.
    await waitFor(() => {
      expect(
        screen.getByText("User opens the conversation."),
      ).toBeInTheDocument();
    });
    // The title appears twice — once in the ChapterRail, once in the
    // ChapterDivider above the anchored event.
    expect(screen.getAllByText("Frame the ask").length).toBeGreaterThanOrEqual(2);
  });

  it("renders the chapter rail (not the prompt rail) when chapters present", async () => {
    const digest = {
      ask: "a", decisions: ["d"], dead_ends: ["e"], learnings: [], tests: "t",
      chapters: [
        { anchor_uuid: "779d4aa7-6138-4b55-93bb-0747bbebb8fa",
          title: "Frame the ask", caption: "x" },
      ],
    };
    mockFetchSequence({
      trace_id: "id", short_id: SHORT_ID, owner_login: "alice",
      repo_full_name: "alice/repo", pr_number: 7,
      pr_url: "https://github.com/alice/repo/pull/7", pr_title: "Add thing",
      platform: "claude-code", byte_size: FIXTURE.length, message_count: 100,
      created_at: "2026-05-17T00:00:00Z", is_private: false, ai_digest: digest,
    });
    const { container } = renderAt(`/alice/repo/pull/7/${SHORT_ID}`);
    await toConversation();
    await waitFor(() =>
      expect(container.querySelector(".chapterrail")).not.toBeNull(),
    );
    expect(container.querySelector(".promptrail")).toBeNull();
  });

  it("falls back to the prompt rail when the digest has no chapters", async () => {
    const digest = {
      ask: "a", decisions: ["d"], dead_ends: ["e"], learnings: [], tests: "t",
      chapters: [],
    };
    mockFetchSequence({
      trace_id: "id", short_id: SHORT_ID, owner_login: "alice",
      repo_full_name: "alice/repo", pr_number: 7,
      pr_url: "https://github.com/alice/repo/pull/7", pr_title: "Add thing",
      platform: "claude-code", byte_size: FIXTURE.length, message_count: 100,
      created_at: "2026-05-17T00:00:00Z", is_private: false, ai_digest: digest,
    });
    const { container } = renderAt(`/alice/repo/pull/7/${SHORT_ID}`);
    await toConversation();
    await waitFor(() =>
      expect(screen.queryByText(/Loading trace/i)).not.toBeInTheDocument(),
    );
    expect(container.querySelector(".promptrail")).not.toBeNull();
    expect(container.querySelector(".chapterrail")).toBeNull();
  });
});
