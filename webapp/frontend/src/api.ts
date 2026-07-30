import type {
  AskEvent,
  GithubContributions,
  GithubPickerPr,
  GithubPickerRepo,
  GithubRepo,
  GithubRepoListPage,
  GithubUser,
  MeResponse,
  RepoOverview,
  TraceListResponse,
  TracePatch,
  TraceSummary,
  UploadResult,
  UserOverview,
} from "./types";

export class ApiError extends Error {
  constructor(
    public status: number,
    public body: string,
  ) {
    super(`API ${status}: ${body}`);
  }
}

async function jsonOrThrow<T>(response: Response): Promise<T> {
  if (!response.ok) {
    throw new ApiError(response.status, await response.text());
  }
  return (await response.json()) as T;
}

export async function fetchPrTraces(
  owner: string,
  repo: string,
  number: number,
): Promise<TraceListResponse> {
  const r = await fetch(`/api/traces/${owner}/${repo}/pull/${number}`, {
    credentials: "same-origin",
  });
  return jsonOrThrow<TraceListResponse>(r);
}

export async function fetchTrace(shortId: string): Promise<TraceSummary> {
  const r = await fetch(`/api/traces/${shortId}`, {
    credentials: "same-origin",
  });
  return jsonOrThrow<TraceSummary>(r);
}

export async function fetchSessionJsonl(shortId: string): Promise<string> {
  const r = await fetch(`/api/traces/${shortId}/session`, {
    credentials: "same-origin",
  });
  if (!r.ok) {
    throw new ApiError(r.status, await r.text());
  }
  return r.text();
}

export async function fetchAgentJsonl(
  shortId: string,
  agentId: string,
): Promise<string> {
  const r = await fetch(`/api/traces/${shortId}/agents/${agentId}`, {
    credentials: "same-origin",
  });
  if (!r.ok) {
    throw new ApiError(r.status, await r.text());
  }
  return r.text();
}

export async function fetchUserOverview(login: string): Promise<UserOverview> {
  const r = await fetch(`/api/users/${encodeURIComponent(login)}`, {
    credentials: "same-origin",
  });
  return jsonOrThrow<UserOverview>(r);
}

export async function fetchRepoOverview(
  owner: string,
  repo: string,
): Promise<RepoOverview> {
  const r = await fetch(
    `/api/repos/${encodeURIComponent(owner)}/${encodeURIComponent(repo)}`,
    { credentials: "same-origin" },
  );
  return jsonOrThrow<RepoOverview>(r);
}

export async function fetchMe(): Promise<MeResponse | null> {
  const r = await fetch("/api/auth/me", { credentials: "same-origin" });
  if (r.status === 204) return null;
  if (!r.ok) throw new ApiError(r.status, await r.text());
  return (await r.json()) as MeResponse;
}

export async function logout(): Promise<void> {
  const r = await fetch("/api/auth/logout", {
    method: "POST",
    credentials: "same-origin",
  });
  if (r.status !== 204) {
    throw new ApiError(r.status, await r.text());
  }
}

export async function fetchGithubUser(login: string): Promise<GithubUser> {
  const r = await fetch(`/api/github/users/${encodeURIComponent(login)}`, {
    credentials: "same-origin",
  });
  return jsonOrThrow<GithubUser>(r);
}

export async function fetchGithubUserRepos(
  login: string,
  page = 1,
): Promise<GithubRepoListPage> {
  const r = await fetch(
    `/api/github/users/${encodeURIComponent(login)}/repos?page=${page}`,
    { credentials: "same-origin" },
  );
  return jsonOrThrow<GithubRepoListPage>(r);
}

export async function fetchGithubRepo(
  owner: string,
  name: string,
): Promise<GithubRepo> {
  const r = await fetch(
    `/api/github/repos/${encodeURIComponent(owner)}/${encodeURIComponent(name)}`,
    { credentials: "same-origin" },
  );
  return jsonOrThrow<GithubRepo>(r);
}

export async function fetchGithubContributions(
  login: string,
): Promise<GithubContributions> {
  const r = await fetch(
    `/api/github/users/${encodeURIComponent(login)}/contributions`,
    { credentials: "same-origin" },
  );
  return jsonOrThrow<GithubContributions>(r);
}

export interface UploadTraceArgs {
  transcript: File;
  subagents?: File | null;
  // Original .txt terminal export, archived alongside the synthetic .jsonl
  // when the transcript was reconstructed from one. Omitted for .jsonl uploads.
  sourceExport?: File | null;
  isPrivate?: boolean;
  prUrl?: string | null;
  repoFullName?: string | null;
}

export async function uploadTrace(
  args: UploadTraceArgs,
): Promise<UploadResult> {
  const form = new FormData();
  form.append("transcript", args.transcript);
  if (args.subagents) form.append("subagents", args.subagents);
  if (args.sourceExport) form.append("source_export", args.sourceExport);
  form.append("is_private", String(args.isPrivate ?? false));
  if (args.prUrl) form.append("pr_url", args.prUrl);
  if (args.repoFullName) form.append("repo_full_name", args.repoFullName);
  const r = await fetch("/api/uploads", {
    method: "POST",
    body: form,
    credentials: "same-origin",
  });
  return jsonOrThrow<UploadResult>(r);
}

/**
 * Claim an anonymous (unclaimed) trace onto the signed-in user's profile.
 * `claimToken` is the one-time secret returned by the original anonymous
 * upload. Requires a session cookie (the viewer must be signed in).
 */
export async function claimTrace(
  shortId: string,
  claimToken: string,
): Promise<TraceSummary> {
  const r = await fetch(`/api/traces/${shortId}/claim`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ claim_token: claimToken }),
    credentials: "same-origin",
  });
  return jsonOrThrow<TraceSummary>(r);
}

export async function patchTrace(
  shortId: string,
  patch: TracePatch,
): Promise<TraceSummary> {
  const r = await fetch(`/api/traces/${shortId}`, {
    method: "PATCH",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(patch),
    credentials: "same-origin",
  });
  return jsonOrThrow<TraceSummary>(r);
}

export async function deleteTrace(shortId: string): Promise<void> {
  const r = await fetch(`/api/traces/${shortId}`, {
    method: "DELETE",
    credentials: "same-origin",
  });
  if (r.status !== 204) {
    throw new ApiError(r.status, await r.text());
  }
}

export async function fetchMyRepos(
  query = "",
): Promise<GithubPickerRepo[]> {
  const qs = query ? `?q=${encodeURIComponent(query)}` : "";
  const r = await fetch(`/api/github/my-repos${qs}`, {
    credentials: "same-origin",
  });
  const data = await jsonOrThrow<{ repos: GithubPickerRepo[] }>(r);
  return data.repos;
}

export async function fetchRepoPrs(
  repoFullName: string,
  query = "",
): Promise<GithubPickerPr[]> {
  const params = new URLSearchParams({ repo: repoFullName });
  if (query) params.set("q", query);
  const r = await fetch(`/api/github/repo-prs?${params.toString()}`, {
    credentials: "same-origin",
  });
  const data = await jsonOrThrow<{ prs: GithubPickerPr[] }>(r);
  return data.prs;
}

function parseAskFrame(frame: string): AskEvent | null {
  const lines = frame.split("\n");
  const eventLine = lines.find((l) => l.startsWith("event: "));
  const dataLine = lines.find((l) => l.startsWith("data: "));
  if (!eventLine || !dataLine) return null;
  const kind = eventLine.slice("event: ".length).trim();
  let data: Record<string, unknown>;
  try {
    data = JSON.parse(dataLine.slice("data: ".length));
  } catch {
    return null;
  }
  return { kind, ...data } as AskEvent;
}

/**
 * Ask the repo agent a question. Parses the SSE response and invokes
 * onEvent per frame. Throws ApiError when the request is rejected
 * before the stream opens (400/401/404/429).
 */
export async function askRepo(
  owner: string,
  repo: string,
  question: string,
  onEvent: (e: AskEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const r = await fetch(
    `/api/repos/${encodeURIComponent(owner)}/${encodeURIComponent(repo)}/ask`,
    {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ question }),
      credentials: "same-origin",
      signal,
    },
  );
  if (!r.ok || !r.body) {
    throw new ApiError(r.status, await r.text());
  }
  const reader = r.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let idx = buffer.indexOf("\n\n");
    while (idx >= 0) {
      const frame = buffer.slice(0, idx);
      buffer = buffer.slice(idx + 2);
      const ev = parseAskFrame(frame);
      if (ev) onEvent(ev);
      idx = buffer.indexOf("\n\n");
    }
  }
}
