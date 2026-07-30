import { useRef, useState } from "react";
import { Link } from "react-router-dom";
import { ApiError, askRepo } from "../../api";
import type { AskCitation } from "../../types";
import { Markdown } from "../trace/Markdown";
import styles from "./RepoAsk.module.css";

type Phase = "idle" | "streaming" | "done" | "error";

interface Props {
  owner: string;
  repo: string;
  traceCount: number;
  active: boolean;
  onActiveChange: (active: boolean) => void;
}

function citationHref(c: AskCitation): string | null {
  if (c.type === "session" || c.type === "chapter") {
    if (!c.trace_short_id) return null;
    const hash = c.anchor_uuid ? `#chapter-${c.anchor_uuid}` : "";
    return `/t/${c.trace_short_id}${hash}`;
  }
  return c.url;
}

const GLYPH: Record<AskCitation["type"], string> = {
  session: "◍",
  chapter: "§",
  pr: "⇄",
  commit: "○",
  file: "▤",
};

export function RepoAsk({
  owner,
  repo,
  traceCount,
  active,
  onActiveChange,
}: Props) {
  const [question, setQuestion] = useState("");
  const [phase, setPhase] = useState<Phase>("idle");
  const [statusText, setStatusText] = useState("");
  const [notice, setNotice] = useState<string | null>(null);
  const [answer, setAnswer] = useState("");
  const [citations, setCitations] = useState<AskCitation[]>([]);
  const [bestEffort, setBestEffort] = useState(false);
  const [error, setError] = useState<{ code: string; message: string } | null>(
    null,
  );
  const abortRef = useRef<AbortController | null>(null);

  if (traceCount === 0) return null;

  const reset = () => {
    abortRef.current?.abort();
    setPhase("idle");
    setStatusText("");
    setNotice(null);
    setAnswer("");
    setCitations([]);
    setBestEffort(false);
    setError(null);
  };

  const close = () => {
    reset();
    setQuestion("");
    onActiveChange(false);
  };

  const submit = async () => {
    const q = question.trim();
    if (!q || phase === "streaming") return;
    reset();
    onActiveChange(true);
    setPhase("streaming");
    setStatusText("thinking");
    const controller = new AbortController();
    abortRef.current = controller;
    try {
      await askRepo(
        owner,
        repo,
        q,
        (ev) => {
          if (controller.signal.aborted) return;
          if (ev.kind === "status") setStatusText(ev.text);
          if (ev.kind === "notice") setNotice(ev.message);
          if (ev.kind === "delta") setAnswer((prev) => prev + ev.text);
          if (ev.kind === "citations") setCitations(ev.citations);
          if (ev.kind === "error") {
            setError({ code: ev.code, message: ev.message });
            setPhase("error");
          }
          if (ev.kind === "done") {
            setBestEffort(ev.best_effort);
            setPhase("done");
          }
        },
        controller.signal,
      );
      setPhase((p) => (p === "streaming" ? "done" : p));
    } catch (e) {
      if (controller.signal.aborted) return;
      const message =
        e instanceof ApiError && e.status === 429
          ? "You have hit the ask limit for now. Try again in a bit."
          : "Something went wrong asking about this repo. Try again soon.";
      setError({ code: "request_failed", message });
      setPhase("error");
    }
  };

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter") void submit();
    if (e.key === "Escape") close();
  };

  const loginHref = `/api/auth/github/login?next=${encodeURIComponent(
    `/${owner}/${repo}`,
  )}`;

  return (
    <div className={styles.wrap}>
      <div className={styles.inputRow}>
        <input
          className={styles.input}
          type="text"
          value={question}
          maxLength={500}
          placeholder="Ask about this repo"
          onChange={(e) => setQuestion(e.target.value)}
          onKeyDown={onKeyDown}
        />
      </div>

      {active && phase !== "idle" && (
        <div className={styles.panel}>
          {notice && <div className={styles.notice}>{notice}</div>}
          {phase === "streaming" && !answer && (
            <div className={styles.status}>{statusText}…</div>
          )}
          {answer && (
            <div className={styles.answer}>
              <Markdown text={answer} />
            </div>
          )}
          {phase === "done" && bestEffort && (
            <div className={styles.bestEffort}>
              Partial answer: the agent ran out of time and answered with
              what it had found.
            </div>
          )}
          {phase === "done" && citations.length > 0 && (
            <div className={styles.citations}>
              {citations.map((c, i) => {
                const href = citationHref(c);
                if (!href) return null;
                const label = `${GLYPH[c.type]} ${c.title}`;
                return c.type === "session" || c.type === "chapter" ? (
                  <Link key={i} className={styles.citation} to={href}>
                    {label}
                  </Link>
                ) : (
                  <a
                    key={i}
                    className={styles.citation}
                    href={href}
                    target="_blank"
                    rel="noreferrer"
                  >
                    {label}
                  </a>
                );
              })}
            </div>
          )}
          {phase === "error" && error && (
            <div className={styles.error}>
              <div>{error.message}</div>
              {error.code === "github_auth_required" && (
                <a className={styles.signin} href={loginHref}>
                  Sign in with GitHub
                </a>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
