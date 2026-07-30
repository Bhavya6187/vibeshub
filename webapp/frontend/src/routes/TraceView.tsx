import { useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { ApiError, fetchSessionJsonl, fetchTrace } from "../api";
import type { TraceSummary } from "../types";
import { ErrorState } from "../components/ErrorState";
import { LoadingState } from "../components/LoadingState";
import { NotFound } from "./NotFound";
import { PrivateTraceGate } from "../components/PrivateTraceGate";
import { SeoHead } from "../components/SeoHead";
import { TraceManageMenu } from "../components/TraceManageMenu";
import { TraceViewer } from "../components/trace/TraceViewer";
import { buildSessionFromRaw } from "../components/trace/sessionFromRaw";
import { scrollToHashAnchor } from "../components/trace/chapterLink";
import type { Session } from "../components/trace/types";
import { useAuth } from "../auth/AuthContext";
import styles from "./TraceView.module.css";

type HeadState =
  | { kind: "loading" }
  | { kind: "ready"; trace: TraceSummary }
  | { kind: "gate"; gate: "signin" | "enable" }
  | { kind: "notfound" }
  | { kind: "error"; message: string };

type BodyState =
  | { kind: "loading" }
  | { kind: "ready"; jsonl: string }
  | { kind: "error"; message: string };

export function TraceView() {
  const { shortId } = useParams<{ shortId: string }>();
  const auth = useAuth();
  const navigate = useNavigate();
  const [head, setHead] = useState<HeadState>({ kind: "loading" });
  const [body, setBody] = useState<BodyState>({ kind: "loading" });

  useEffect(() => {
    if (!shortId) return;
    setHead({ kind: "loading" });
    fetchTrace(shortId)
      .then((trace) => setHead({ kind: "ready", trace }))
      .catch((e) => {
        if (e instanceof ApiError && e.status === 401) {
          setHead({ kind: "gate", gate: "signin" });
        } else if (e instanceof ApiError && e.status === 403) {
          setHead({ kind: "gate", gate: "enable" });
        } else if (e instanceof ApiError && e.status === 404) {
          setHead({ kind: "notfound" });
        } else {
          setHead({ kind: "error", message: String(e) });
        }
      });
  }, [shortId]);

  useEffect(() => {
    if (!shortId) return;
    setBody({ kind: "loading" });
    fetchSessionJsonl(shortId)
      .then((jsonl) => setBody({ kind: "ready", jsonl }))
      .catch((e) => setBody({ kind: "error", message: String(e) }));
  }, [shortId]);

  const trace = head.kind === "ready" ? head.trace : null;

  const session: Session | null = useMemo(() => {
    if (body.kind !== "ready") return null;
    const built = buildSessionFromRaw(body.jsonl);
    if (trace?.agents) {
      built.meta.agents = trace.agents;
    }
    return built;
  }, [body, trace]);

  // Ask citations link to /t/<id>#chapter-<uuid>; scroll there once the
  // thread exists. rAF lets TraceViewer paint the dividers first.
  useEffect(() => {
    if (!session) return;
    const frame = requestAnimationFrame(() =>
      scrollToHashAnchor(window.location.hash),
    );
    return () => cancelAnimationFrame(frame);
  }, [session]);

  if (head.kind === "gate") return <PrivateTraceGate kind={head.gate} />;
  if (head.kind === "notfound") return <NotFound />;
  if (head.kind === "error") return <ErrorState message={head.message} />;
  if (head.kind === "loading") return <LoadingState label="Loading trace…" />;

  const repoParts = head.trace.repo_full_name?.split("/") ?? [];
  const isOwner = auth.user?.login === head.trace.owner_login;

  const ownerControls = isOwner ? (
    <TraceManageMenu
      trace={head.trace}
      onUpdated={(updated) => setHead({ kind: "ready", trace: updated })}
      onDeleted={() => navigate("/" + head.trace.owner_login)}
    />
  ) : undefined;

  // The canonical URL collapses the two ways to reach a trace
  // (/t/:shortId vs /:owner/:repo/pull/:n/:shortId) onto the repo-attached
  // form when there's a PR — otherwise the standalone /t URL.
  const canonicalPath =
    head.trace.repo_full_name && head.trace.pr_number != null
      ? `/${head.trace.repo_full_name}/pull/${head.trace.pr_number}/${head.trace.short_id}`
      : `/t/${head.trace.short_id}`;

  const titleSubject =
    head.trace.pr_title ??
    (head.trace.repo_full_name && head.trace.pr_number != null
      ? `${head.trace.repo_full_name} #${head.trace.pr_number}`
      : `Trace ${head.trace.short_id}`);
  const description =
    `Claude Code session by @${head.trace.owner_login} · ` +
    `${head.trace.message_count} messages` +
    (head.trace.repo_full_name ? ` · ${head.trace.repo_full_name}` : "");

  // Public traces get a dynamic per-trace social card (rendered server-side
  // at /api/og/<id>.png). Keep the client-injected og:image in sync with the
  // SSR meta from spa_seo so JS-executing preview tools see the same card.
  const cardImage =
    head.trace.is_private || typeof window === "undefined"
      ? undefined
      : `${window.location.origin}/api/og/${head.trace.short_id}.png`;

  return (
    <div className={styles.container}>
      <SeoHead
        title={titleSubject}
        description={description}
        path={canonicalPath}
        ogType="article"
        image={cardImage}
        noindex={head.trace.is_private}
      />
      {body.kind === "loading" && <LoadingState label="Loading trace…" />}
      {body.kind === "error" && <ErrorState message={body.message} />}
      {body.kind === "ready" && session && (
        <TraceViewer
          trace={head.trace}
          session={session}
          shortId={head.trace.short_id}
          rawHref={`/api/traces/${head.trace.short_id}/raw`}
          repoOwner={repoParts[0]}
          repoName={repoParts[1]}
          ownerControls={ownerControls}
          canEditTitle={isOwner}
          onTraceUpdated={(updated) =>
            setHead({ kind: "ready", trace: updated })
          }
        />
      )}
    </div>
  );
}
