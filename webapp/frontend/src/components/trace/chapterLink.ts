import type { StreamEvent } from "./types";
import type { DigestChapter } from "../../types";
import type { BlameFile } from "./provenance";

// chapterLink.ts — maps digest chapters onto the provenance model. A chapter
// owns the stream span from its anchor to the next chapter's anchor, so any
// event position (and therefore any edit hunk) resolves to at most one chapter.

export interface ChapterRange {
  chapter: DigestChapter;
  startPos: number;
  endPos: number; // exclusive
}

// Mirrors chapterMetrics's anchor resolution: unresolved anchors are skipped,
// anchors are sorted by stream position so out-of-order chapters cannot
// produce negative spans.
export function chapterRanges(
  stream: StreamEvent[],
  chapters: DigestChapter[],
): ChapterRange[] {
  const index = new Map<string, number>();
  stream.forEach((e, i) => {
    const uuid = (e as { uuid?: string }).uuid;
    if (uuid && !index.has(uuid)) index.set(uuid, i);
  });
  const resolved = chapters
    .map((c) => ({ chapter: c, pos: index.get(c.anchor_uuid) }))
    .filter((r): r is { chapter: DigestChapter; pos: number } =>
      r.pos !== undefined,
    )
    .sort((a, b) => a.pos - b.pos);
  return resolved.map((r, k) => ({
    chapter: r.chapter,
    startPos: r.pos,
    endPos: k + 1 < resolved.length ? resolved[k + 1].pos : stream.length,
  }));
}

export function chapterForPos(
  ranges: ChapterRange[],
  pos: number,
): DigestChapter | null {
  if (pos < 0) return null;
  for (const r of ranges) {
    if (pos >= r.startPos && pos < r.endPos) return r.chapter;
  }
  return null;
}

export interface ChapterFileStat {
  path: string;
  adds: number;
  dels: number;
}

// Files each chapter touched: surviving hunks bucketed by the chapter whose
// span contains the edit's main-stream position. Keyed by anchor_uuid.
export function filesByChapter(
  files: BlameFile[],
  ranges: ChapterRange[],
): Map<string, ChapterFileStat[]> {
  const out = new Map<string, ChapterFileStat[]>();
  for (const f of files) {
    for (const h of f.hunks) {
      if (h.superseded) continue;
      const ch = chapterForPos(ranges, h.streamPos);
      if (!ch) continue;
      const list = out.get(ch.anchor_uuid) ?? [];
      let stat = list.find((s) => s.path === f.path);
      if (!stat) {
        stat = { path: f.path, adds: 0, dels: 0 };
        list.push(stat);
      }
      stat.adds += h.adds;
      stat.dels += h.dels;
      out.set(ch.anchor_uuid, list);
    }
  }
  return out;
}

// Deep-link support for ask citations (/t/<id>#chapter-<uuid>): scroll
// the anchored chapter divider into view once the thread has rendered.
export function scrollToHashAnchor(hash: string): void {
  const id = hash.startsWith("#") ? hash.slice(1) : hash;
  if (!id) return;
  const el = document.getElementById(id);
  if (el) el.scrollIntoView({ block: "start" });
}
