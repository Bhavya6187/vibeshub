import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { AuthWidget } from "../components/AuthWidget";
import { SeoHead } from "../components/SeoHead";
import { fetchRepoOverview } from "../api";
import type { RepoOverview, TraceSummary } from "../types";
import styles from "./Landing.module.css";

// Keep in sync with plugins/cli/.claude-plugin/plugin.json.
const PLUGIN_VERSION = "0.6.1";
const PLUGIN_MINOR_VERSION = PLUGIN_VERSION.split(".").slice(0, 2).join(".");

// The runnable install commands - single source of truth for both copy buttons.
// Claude Code runs them as slash commands inside the CLI; Codex runs them in the
// terminal (note the different verbs: 'install' vs 'add').
const INSTALL_STEPS = [
  "/plugin marketplace add vibeshub/vibeshub",
  "/plugin install vibeshub@vibeshub",
];
const INSTALL_COPY = INSTALL_STEPS.join("\n");

const CODEX_INSTALL_STEPS = [
  "codex plugin marketplace add vibeshub/vibeshub",
  "codex plugin add vibeshub@vibeshub",
];
const CODEX_INSTALL_COPY = CODEX_INSTALL_STEPS.join("\n");

// The vibeshub repo that powers the Browse section.
const BROWSE_OWNER = "vibeshub";
const BROWSE_REPO = "vibeshub";
const BROWSE_FULL = `${BROWSE_OWNER}/${BROWSE_REPO}`;
const BROWSE_MAX = 6;

// schema.org SoftwareApplication for the homepage. Mirrors the JSON-LD baked
// into index.html (which SeoHead strips on hydration), so search engines keep
// the structured data once React takes over. Keep the two in sync.
const LANDING_JSONLD = {
  "@context": "https://schema.org",
  "@type": "SoftwareApplication",
  name: "vibeshub",
  url: "https://vibeshub.ai",
  applicationCategory: "DeveloperApplication",
  operatingSystem: "macOS, Linux, Windows",
  description:
    "Turn your Claude Code and Codex sessions, including every subagent they spawn, into shareable, replayable traces, each with an AI digest of the session. Public and private viewer with GitHub-mirrored access and automatic secret redaction.",
  offers: { "@type": "Offer", price: "0", priceCurrency: "USD" },
};

function useCopy() {
  const [copied, setCopied] = useState<string | null>(null);
  const copy = (key: string, text: string) => {
    navigator.clipboard?.writeText(text).then(
      () => {
        setCopied(key);
        window.setTimeout(() => setCopied((c) => (c === key ? null : c)), 1400);
      },
      () => {},
    );
  };
  return { copied, copy };
}

function compactCount(n: number): string {
  if (n >= 1_000_000) {
    return `${(n / 1_000_000).toFixed(1)}M`;
  }
  if (n >= 1000) {
    const k = n / 1000;
    return `${k.toFixed(k >= 10 ? 0 : 1)}k`;
  }
  return String(n);
}

function relativeWhen(iso: string): string {
  const then = new Date(iso).getTime();
  const diff = Math.max(0, Date.now() - then);
  const m = Math.round(diff / 60_000);
  if (m < 1) return "just now";
  if (m < 60) return `${m}m ago`;
  const h = Math.round(m / 60);
  if (h < 24) return `${h}h ago`;
  const d = Math.round(h / 24);
  if (d === 1) return "yesterday";
  if (d < 7) return `${d}d ago`;
  const w = Math.round(d / 7);
  if (w < 5) return `${w}w ago`;
  const mo = Math.round(d / 30);
  return `${mo}mo ago`;
}

/* Mono section header: title + hairline rule + NN / NN counter. */
function SectionHead({ title, count }: { title: string; count: string }) {
  return (
    <div className={styles.shead}>
      <h2>{title}</h2>
      <span className={styles.sep} />
      <span className={styles.scount}>{count}</span>
    </div>
  );
}

export function Landing() {
  const { copied, copy } = useCopy();
  const [browse, setBrowse] = useState<RepoOverview | null>(null);

  // Pull the real public traces for the vibeshub repo to fill the Browse
  // section. Errors are swallowed - the section degrades to skeletons.
  useEffect(() => {
    let alive = true;
    fetchRepoOverview(BROWSE_OWNER, BROWSE_REPO)
      .then((data) => {
        if (alive) setBrowse(data);
      })
      .catch(() => {
        /* keep skeleton */
      });
    return () => {
      alive = false;
    };
  }, []);

  return (
    <div className={`page-shell ${styles.ht}`}>
      <SeoHead
        title="vibeshub · share Claude Code & Codex sessions as replayable traces"
        description="Your Claude Code and Codex sessions, including every subagent they spawn, become shareable, replayable traces, each with an AI digest of the session. Public and private viewer with GitHub-mirrored access and automatic secret redaction."
        path="/"
        bareTitle
        jsonLd={LANDING_JSONLD}
      />
      <header className="topbar">
        <div className="topbar-inner">
          <Link className="brand" to="/">
            <span className="brand-mark">v</span>
            <span>vibeshub</span>
          </Link>

          {/* Real destinations only: the live viewer plus the blog. The blog
              lives outside the SPA, so it's a full-page link, not a router
              Link. Privacy/Contact stay in the footer. */}
          <nav className={styles.navLinks} aria-label="Main">
            <Link to="/vibeviewer">viewer</Link>
            <a href="/blog">blog</a>
          </nav>

          <div className="topbar-spacer" />

          <div className="topbar-actions">
            <AuthWidget />
            <a className="iconbtn primary" href="#install">
              $ install
            </a>
          </div>
        </div>
      </header>

      <main>
        {/* ====================== hero ====================== */}
        <section className={styles.section}>
          <div className={`${styles.wrap} ${styles.heroGrid}`}>
            <div className={styles.heroLeft}>
              <div className={styles.status}>
                <span className={styles.live}>
                  <i />
                  session traces for AI-built PRs
                </span>
                <span>public &amp; private</span>
                <span>for vibe coding teams</span>
              </div>
              <h1 className={styles.heroH1}>
                Don&rsquo;t just review the diff -{" "}
                <span className={styles.grn}>replay the session</span> that
                built it.
                <span className={styles.cursor} aria-hidden="true" />
              </h1>
              <p className={styles.sub}>
                Every PR your team opens can carry the Claude Code or Codex
                session that built it, every subagent included. Reviewers and
                new teammates replay how it actually shipped, instead of
                reverse-engineering the final diff.
              </p>
              <div className={styles.ctas}>
                <a className={styles.cta1} href="#install">
                  $ install plugin
                </a>
                <a className={styles.cta2} href="#browse">
                  browse traces
                </a>
              </div>
            </div>

            {/* right: how it works - compact 4-step timeline */}
            <div className={styles.heroRight}>
              <div className={styles.eyebrow}>
                <span className={styles.dot} /> How it works
              </div>
              <ol className={styles.heroFlow}>
                <li className={styles.heroFlowStep}>
                  <span
                    className={`${styles.heroFlowMark} ${styles.heroFlowMarkManual}`}
                  >
                    <IconTerminal />
                  </span>
                  <div>
                    <span className={styles.heroFlowNum}>
                      01 · Install the plugin
                    </span>
                    <p className={styles.heroFlowText}>
                      One command in Claude Code or Codex, and your GitHub auth
                      is your vibeshub identity.
                    </p>
                  </div>
                </li>
                <li className={styles.heroFlowStep}>
                  <span
                    className={`${styles.heroFlowMark} ${styles.heroFlowMarkManual}`}
                  >
                    <IconPrSvg />
                  </span>
                  <div>
                    <span className={styles.heroFlowNum}>02 · Open a PR</span>
                    <p className={styles.heroFlowText}>
                      Open a PR from your session, exactly the way you do today.
                    </p>
                  </div>
                </li>
                <li className={styles.heroFlowHandoff} aria-hidden="true">
                  <span className={styles.heroFlowHandoffLine} />
                  <span className={styles.heroFlowHandoffLabel}>
                    then vibeshub takes over
                  </span>
                </li>
                <li className={styles.heroFlowStep}>
                  <span className={styles.heroFlowMark}>
                    <IconCheck />
                  </span>
                  <div>
                    <span className={styles.heroFlowNum}>
                      03 · The trace lands on the PR
                    </span>
                    <p className={styles.heroFlowText}>
                      It uploads and the PR comment arrives with the link,
                      automatically. No commands to remember, no links to paste.
                    </p>
                  </div>
                </li>
                <li className={styles.heroFlowStep}>
                  <span className={styles.heroFlowMark}>
                    <IconSparkle />
                  </span>
                  <div>
                    <span className={styles.heroFlowNum}>
                      04 · The AI digest kicks in
                    </span>
                    <p className={styles.heroFlowText}>
                      vibeshub distills the whole session into five lines plus
                      jump links to the key moments. Reviewers start from the
                      story, not message one of 257.
                    </p>
                  </div>
                </li>
              </ol>
            </div>
          </div>
        </section>

        {/* ====================== collaborate (teams) ====================== */}
        <section className={styles.section} id="teams">
          <div className={`${styles.wrap} ${styles.collab}`}>
            <SectionHead title="Collaborate" count="01 / 05" />
            <div className={styles.collabGrid}>
              <div>
                <h3 className={styles.bigq}>
                  Your team&rsquo;s work, finally legible.
                </h3>
                <p className={styles.slede} style={{ marginTop: 14 }}>
                  Every PR your team ships can carry the session that produced
                  it. The whole team reads how it was built, not just what
                  changed.
                </p>
                <ul className={styles.pts}>
                  <li>
                    <span className={styles.n}>01</span>
                    <span>
                      <strong>Review starts from intent.</strong> Every trace
                      lands with an AI digest, the ask, key decisions, and dead
                      ends, plus chapters that jump straight to the moment.
                      Reviewers get the story before the diff.
                    </span>
                  </li>
                  <li>
                    <span className={styles.n}>02</span>
                    <span>
                      <strong>Onboarding without the shoulder-tap.</strong> New
                      teammates see how tricky changes were really built, with
                      the full session as context.
                    </span>
                  </li>
                  <li>
                    <span className={styles.n}>03</span>
                    <span>
                      <strong>Searchable team history.</strong> Every shipped
                      PR keeps its session attached, so each repo becomes a
                      browsable archive of how the team works.
                    </span>
                  </li>
                  <li>
                    <span className={styles.n}>04</span>
                    <span>
                      <strong>Every agent, one archive.</strong> Some of the
                      team runs Claude Code, some runs Codex. Every PR carries
                      its session either way, so it all lands in one searchable
                      place.
                    </span>
                  </li>
                  <li>
                    <span className={styles.n}>05</span>
                    <span>
                      <strong>Shared permissions, zero setup.</strong> Access
                      mirrors GitHub, so the right people already have
                      visibility, with no separate ACLs or accounts.
                    </span>
                  </li>
                </ul>
                <p className={styles.crosslink}>
                  Working solo and just want to show off a session?{" "}
                  <Link to="/vibeviewer">Try the vibeviewer →</Link>
                </p>
              </div>

              <div className={styles.prCard}>
                <div className={styles.prHead}>
                  <span className={styles.prAv}>v</span>
                  <b>vibeshub-bot</b>
                  <span className={styles.prBot}>bot</span>
                  <span className={styles.prTime}>commented just now</span>
                </div>
                <div className={styles.prBody}>
                  <div className={styles.prTitle}>
                    Claude Code session for this PR
                  </div>
                  {/* Mirrors the digest rows build_comment_body posts on real PRs. */}
                  <div className={styles.dRow}>
                    <span className={styles.dKey}>Ask</span>
                    <span className={styles.dVal}>
                      Add chapter navigation to the trace viewer
                    </span>
                  </div>
                  <div className={styles.dRow}>
                    <span className={styles.dKey}>Key decisions</span>
                    <span className={styles.dVal}>
                      Reuse digest anchors as the nav spine
                    </span>
                  </div>
                  <div className={styles.dRow}>
                    <span className={styles.dKey}>Dead ends</span>
                    <span className={styles.dVal}>
                      IntersectionObserver thrashed, switched to scroll math
                    </span>
                  </div>
                  <div className={styles.prStats}>
                    <span>
                      <b>257</b> messages
                    </span>
                    <span>
                      <b>12</b> file edits
                    </span>
                    <span>
                      <b>4</b> subagents
                    </span>
                  </div>
                  <a
                    className={styles.prLink}
                    href={`https://vibeshub.ai/${BROWSE_FULL}/pull/69/7ntgpt45el`}
                    target="_blank"
                    rel="noreferrer"
                  >
                    vibeshub.ai/{BROWSE_FULL}/pull/69/7ntgpt45el ↗
                  </a>
                </div>
              </div>
            </div>
          </div>
        </section>

        {/* ====================== three ways in ====================== */}
        <section className={styles.section} id="ways">
          <div className={`${styles.wrap} ${styles.ways}`}>
            <SectionHead title="Three ways in" count="02 / 05" />
            <p className={styles.slede}>
              Three ways to get a session onto vibeshub, from fully automatic
              to a one-off upload. Reach for whichever suits the moment, they
              all land in the same place.
            </p>
            <div className={styles.waysGrid}>
              <div className={styles.way}>
                <div className={styles.kicker}>01 / Automatic</div>
                <h3>Posts on every PR you open or update</h3>
                <p>
                  Open or update a PR from your Claude Code session and the
                  trace lands on the PR as a comment automatically. Reviewers
                  see how you actually built it before they read the diff. You
                  change nothing about how you work.
                </p>
                <pre className={styles.wayMini}>
                  <span className={styles.wayMiniCmd}>
                    &gt; open a PR for the navbar fix
                  </span>
                  {"\n"}
                  <span className={styles.ok}>
                    → uploaded · commented on PR ✓
                  </span>
                </pre>
              </div>
              <div className={styles.way}>
                <div className={styles.kicker}>02 / Slash command</div>
                <h3>
                  Share any session with <code>/share-trace</code>
                </h3>
                <p>
                  Share a single session on demand: a clever debug, a tough
                  refactor, a moment you want a second pair of eyes on.{" "}
                  <code>/share-trace delete</code> takes it down just as fast.
                </p>
                <pre className={styles.wayMini}>
                  <span className={styles.wayMiniCmd}>&gt; /share-trace</span>
                  {"\n"}
                  <span className={styles.ok}>
                    → vibeshub.ai/acme/…/8m2plq ✓
                  </span>
                </pre>
              </div>
              <div className={styles.way}>
                <div className={styles.kicker}>03 / Web upload</div>
                <h3>
                  Drop your <code>.jsonl</code> on the web
                </h3>
                <p>
                  Publish a trace from any browser. Useful when the session
                  lives on a teammate&rsquo;s laptop, or on a machine where
                  you&rsquo;d rather not install anything.
                </p>
                <Link to="/vibeviewer" className={styles.wayMini}>
                  <span className={styles.wayMiniCmd}>
                    drop .jsonl / .json here
                  </span>
                  {"\n"}
                  <span className={styles.ok}>↑ or click to choose a file</span>
                </Link>
              </div>
            </div>
          </div>
        </section>

        {/* ====================== browse ====================== */}
        <BrowseSection data={browse} />

        {/* ====================== privacy (access + redaction) ====================== */}
        <section className={styles.section} id="privacy">
          <div className={`${styles.wrap} ${styles.priv}`}>
            <SectionHead title="Privacy" count="04 / 05" />
            <div className={styles.privGrid}>
              <div>
                <h3 className={styles.bigq}>
                  Your GitHub permissions. Your secrets, stripped twice.
                </h3>
                <ul className={styles.pts}>
                  <li>
                    <span className={styles.n}>01</span>
                    <span>
                      <strong>Visibility mirrors GitHub</strong> - public stays
                      public, private stays private.
                    </span>
                  </li>
                  <li>
                    <span className={styles.n}>02</span>
                    <span>
                      <strong>Collaborators carry over</strong> - same read
                      access as the repo, no separate ACL.
                    </span>
                  </li>
                  <li>
                    <span className={styles.n}>03</span>
                    <span>
                      <strong>Secrets stripped twice</strong> - keys, tokens,
                      and <code>KEY=value</code> shapes, on your machine and on
                      the server.
                    </span>
                  </li>
                </ul>
                <p className={styles.privlink}>
                  <Link to="/privacy">Read the full privacy policy →</Link>
                </p>
              </div>

              <div className={styles.redact}>
                <div className={styles.rHead}>
                  <span className={styles.rBadge}>redaction preview</span>
                  <span className={styles.rPath}>
                    ~/.claude/projects/web/&lt;session-id&gt;.jsonl
                  </span>
                </div>
                <pre className={styles.rBody}>
                  <code>
                    <span className={styles.dim}>{"{"}</span>
                    {"\n  "}
                    <span className={styles.kv}>"role": "tool_result",</span>
                    {"\n  "}
                    <span className={styles.kv}>"name": "Bash",</span>
                    {"\n  "}
                    <span className={styles.kv}>
                      "output": "$ env | grep API
                    </span>
                    {"\n  GITHUB_TOKEN="}
                    <span className={styles.strike}>ghp_d4Kp9MZx2vQfJL8wB</span>
                    <span className={styles.chip}>[redacted:gh]</span>
                    {"\n  ANTHROPIC_API_KEY="}
                    <span className={styles.strike}>sk-ant-api03-9q…</span>
                    <span className={styles.chip}>[redacted:anth]</span>
                    {"\n  OPENAI_API_KEY="}
                    <span className={styles.strike}>sk-proj-2vF…ZxQ</span>
                    <span className={styles.chip}>[redacted:openai]</span>
                    {"\n  AWS_SECRET="}
                    <span className={styles.strike}>aQF/9qZxV7…</span>
                    <span className={styles.chip}>[redacted:aws]</span>
                    {`\n  USER_AGENT=vibeshub/${PLUGIN_MINOR_VERSION}"\n`}
                    <span className={styles.dim}>{"}"}</span>
                    {"\n\n"}
                    <span className={styles.comment}>
                      // 4 patterns matched · 0 high-entropy fallbacks · safe
                      to upload
                    </span>
                  </code>
                </pre>
              </div>
            </div>
          </div>
        </section>

        {/* ====================== install ====================== */}
        <section className={styles.section} id="install">
          <div className={`${styles.wrap} ${styles.install}`}>
            <SectionHead title="Install" count="05 / 05" />
            <div className={styles.installGrid}>
              <div>
                <h3 className={styles.bigq}>
                  Install once. Every PR you ship comes with a trace.
                </h3>
                <p className={styles.prereq}>
                  <strong>Before you start:</strong> Claude Code or Codex, the{" "}
                  <code>gh</code> CLI authenticated with{" "}
                  <code>gh auth login</code>, and <code>python3</code> 3.9+ on
                  your <code>PATH</code>. The hook uses only the Python
                  standard library, nothing to <code>pip install</code>.
                </p>
                <div className={styles.meta}>
                  <span>
                    <span className={styles.metaK}>version</span>
                    <span className={styles.metaV}>{PLUGIN_VERSION}</span>
                  </span>
                  <span>
                    <span className={styles.metaK}>license</span>
                    <span className={styles.metaV}>MIT</span>
                  </span>
                  <span>
                    <span className={styles.metaK}>deps</span>
                    <span className={styles.metaV}>gh · python3</span>
                  </span>
                </div>
              </div>

              <div>
                <div className={styles.codeBlock}>
                  <div className={styles.sessHead}>
                    <span>shell</span>
                    <button
                      type="button"
                      className={styles.cp}
                      onClick={() => copy("install", INSTALL_COPY)}
                    >
                      {copied === "install" ? "copied ✓" : "copy"}
                    </button>
                  </div>
                  <pre className={styles.codeBody}>
                    <div className={styles.cm}>
                      # 1 · in Claude Code, add the marketplace + install
                    </div>
                    <div>
                      <span className={styles.c}>/plugin marketplace add</span>{" "}
                      <span className={styles.acc}>vibeshub/vibeshub</span>
                    </div>
                    <div>
                      <span className={styles.c}>/plugin install</span>{" "}
                      <span className={styles.acc}>vibeshub@vibeshub</span>
                    </div>
                    <div>&nbsp;</div>
                    <div className={styles.cm}>
                      # 2 · that's it - next time your agent runs 'gh pr
                      create'
                    </div>
                    <div>
                      <span className={styles.cm}>$ </span>
                      <span className={styles.c}>gh pr create</span>{" "}
                      <span className={styles.acc}>--fill</span>
                    </div>
                    <div className={styles.echo}>
                      {"  ↳ vibeshub: redacted · uploaded · commented on #482 ✓"}
                    </div>
                  </pre>
                </div>

                <div className={styles.codexNote}>
                  <div className={styles.codexHead}>
                    using codex? run these in your terminal
                    <button
                      type="button"
                      className={styles.cp}
                      onClick={() => copy("codex", CODEX_INSTALL_COPY)}
                    >
                      {copied === "codex" ? "copied ✓" : "copy"}
                    </button>
                  </div>
                  <pre className={styles.codexBody}>
                    <div>
                      <span className={styles.d}>$ </span>
                      codex plugin marketplace add{" "}
                      <span className={styles.acc}>vibeshub/vibeshub</span>
                    </div>
                    <div>
                      <span className={styles.d}>$ </span>
                      codex plugin add{" "}
                      <span className={styles.acc}>vibeshub@vibeshub</span>
                    </div>
                  </pre>
                </div>
              </div>
            </div>
          </div>
        </section>

        {/* ====================== footer ====================== */}
        <footer>
          <div className={`${styles.wrap} ${styles.foot}`}>
            <span className={styles.blurb}>
              vibeshub · public &amp; private viewer for Claude Code &amp;
              Codex traces
            </span>
            <nav className={styles.footLinks} aria-label="Footer">
              <a href="https://github.com/vibeshub/vibeshub">GitHub</a>
              <a href="#browse">Browse</a>
              <Link to="/vibeviewer">Viewer</Link>
              <Link to="/faq">FAQ</Link>
              <Link to="/privacy">Privacy</Link>
              <Link to="/contact">Contact</Link>
              <a href="#install">Install</a>
            </nav>
          </div>
        </footer>
      </main>
    </div>
  );
}

/* ---------- browse section (uses real vibeshub repo data) ---------- */

interface BrowseSectionProps {
  data: RepoOverview | null;
}

function BrowseSection({ data }: BrowseSectionProps) {
  const traces = data?.traces?.slice(0, BROWSE_MAX) ?? [];
  const contributors = data?.contributors?.slice(0, 5) ?? [];

  return (
    <section className={styles.section} id="browse">
      <div className={`${styles.wrap} ${styles.browse}`}>
        <SectionHead title="Browse public" count="03 / 05" />
        <p className={styles.slede}>
          vibeshub, built with{" "}
          <Link to={`/${BROWSE_FULL}`} className={styles.grn}>
            vibeshub
          </Link>
          . Every PR here shipped with the Claude Code or Codex session that
          produced it. Open a card to read the prompts, tool calls, and
          reasoning behind the diff.
        </p>

        <div className={styles.stats}>
          <StatCell
            label="Public traces"
            value={data ? compactCount(data.stats.trace_count) : "-"}
            sub={
              data && data.stats.last_trace_at
                ? `latest ${relativeWhen(data.stats.last_trace_at)}`
                : "loading"
            }
          />
          <StatCell
            label="PRs covered"
            value={data ? compactCount(data.stats.pr_count) : "-"}
            sub="with a trace attached"
          />
          <StatCell
            label="Messages"
            value={data ? compactCount(data.stats.message_count) : "-"}
            sub="across all sessions"
          />
          <StatCell
            label="Contributors"
            value={data ? compactCount(data.stats.contributor_count) : "-"}
            sub="who shared a trace"
          />
        </div>

        <div className={styles.feedGrid}>
          <div className={styles.traceList}>
            {traces.length === 0 && (
              <div className={styles.traceListEmpty}>
                {data ? "No public traces yet." : "Loading public traces…"}
              </div>
            )}
            {traces.map((t) => (
              <BrowseRow key={t.short_id} trace={t} />
            ))}
            {data && data.stats.trace_count > traces.length && (
              <Link to={`/${BROWSE_FULL}`} className={styles.tMore}>
                see all {compactCount(data.stats.trace_count)} traces →
              </Link>
            )}
          </div>

          <aside className={styles.side}>
            <div className={styles.sideCard}>
              <div className={styles.sideHead}>
                <h4>Top uploaders</h4>
                <span className={styles.ct}>{BROWSE_FULL}</span>
              </div>
              <div className={styles.sideBody}>
                {contributors.length === 0 && (
                  <div className={styles.sideEmpty}>
                    {data ? "No uploads yet." : "Loading…"}
                  </div>
                )}
                {contributors.map((c) => (
                  <Link
                    key={c.login}
                    to={`/${c.login}`}
                    className={styles.cRow}
                  >
                    <span className={styles.cAv}>
                      {c.login.charAt(0).toLowerCase()}
                    </span>
                    <span className={styles.cName}>@{c.login}</span>
                    <span className={styles.cCount}>{c.trace_count}</span>
                  </Link>
                ))}
              </div>
            </div>

            <div className={styles.sideCard}>
              <div className={styles.sideHead}>
                <h4>Platforms</h4>
                <span className={styles.ct}>supported</span>
              </div>
              <div className={styles.sideBody}>
                <div className={styles.cRow}>
                  <span className={styles.cAv}>cc</span>
                  <span className={styles.cName}>claude-code</span>
                  <span className={styles.cTag}>active</span>
                </div>
                <div className={styles.cRow}>
                  <span className={styles.cAv}>cx</span>
                  <span className={styles.cName}>codex</span>
                  <span className={styles.cTag}>active</span>
                </div>
                <div className={`${styles.cRow} ${styles.cRowDim}`}>
                  <span className={styles.cAv}>cu</span>
                  <span className={styles.cName}>cursor</span>
                  <span className={`${styles.cTag} ${styles.cTagOff}`}>
                    contribute
                  </span>
                </div>
              </div>
            </div>
          </aside>
        </div>
      </div>
    </section>
  );
}

function StatCell({
  label,
  value,
  sub,
}: {
  label: string;
  value: string;
  sub: string;
}) {
  return (
    <div className={styles.stat}>
      <div className={styles.statLabel}>{label}</div>
      <div className={styles.statValue}>{value}</div>
      <div className={styles.statSub}>{sub}</div>
    </div>
  );
}

function BrowseRow({ trace }: { trace: TraceSummary }) {
  const sizeKb = Math.max(1, Math.round(trace.byte_size / 1024));
  return (
    <Link
      className={styles.tRow}
      to={
        trace.repo_full_name && trace.pr_number != null
          ? `/${trace.repo_full_name}/pull/${trace.pr_number}/${trace.short_id}`
          : `/t/${trace.short_id}`
      }
    >
      <span className={styles.tIcon}>&gt;_</span>
      <div className={styles.tBody}>
        <div className={styles.tTop}>
          {trace.repo_full_name && (
            <span className={styles.tRef}>{trace.repo_full_name}</span>
          )}
          {trace.pr_number != null ? (
            <span className={`${styles.tRef} ${styles.tRefPr}`}>
              #{trace.pr_number}
            </span>
          ) : (
            <span className={styles.tRef}>manual</span>
          )}
          <span className={styles.tTitle}>
            {trace.pr_title ??
              (trace.pr_number != null
                ? `PR #${trace.pr_number}`
                : `Trace ${trace.short_id}`)}
          </span>
        </div>
        <div className={styles.tMeta}>
          <span className={styles.pf}>● {trace.platform}</span>
          <span>·</span>
          <span>{trace.message_count} msgs</span>
          <span>·</span>
          <span>{sizeKb} KB</span>
          <span>·</span>
          <span>@{trace.owner_login}</span>
        </div>
      </div>
      <div className={styles.tRight}>
        <div>{relativeWhen(trace.created_at)}</div>
        <div>{trace.short_id}</div>
      </div>
    </Link>
  );
}

/* ---------- hero "how it works" timeline icons ---------- */

function IconTerminal() {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.8}
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <polyline points="4 17 10 11 4 5" />
      <line x1="12" y1="19" x2="20" y2="19" />
    </svg>
  );
}

function IconPrSvg() {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.8}
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <circle cx="18" cy="18" r="3" />
      <circle cx="6" cy="6" r="3" />
      <path d="M13 6h3a2 2 0 0 1 2 2v7" />
      <line x1="6" y1="9" x2="6" y2="21" />
    </svg>
  );
}

function IconCheck() {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2.4}
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="m5 12 5 5 9-11" />
    </svg>
  );
}

function IconSparkle() {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.8}
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M9.937 15.5A2 2 0 0 0 8.5 14.063l-6.135-1.582a.5.5 0 0 1 0-.962L8.5 9.936A2 2 0 0 0 9.937 8.5l1.582-6.135a.5.5 0 0 1 .963 0L14.063 8.5A2 2 0 0 0 15.5 9.937l6.135 1.581a.5.5 0 0 1 0 .964L15.5 14.063a2 2 0 0 0-1.437 1.437l-1.582 6.135a.5.5 0 0 1-.963 0z" />
      <path d="M20 3v4" />
      <path d="M22 5h-4" />
    </svg>
  );
}
