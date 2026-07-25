"""Download a vibeshub trace as a resume-grade session and place it where
the target CLI looks. Stdlib-only, mirroring upload.py conventions."""
from __future__ import annotations

import json
import os
import re
import uuid
from pathlib import Path
from urllib import error as urllib_error
from urllib import request as urllib_request
from urllib.parse import urlsplit

_ROLLOUT_RE = re.compile(
    r"^rollout-(\d{4})-(\d{2})-(\d{2})T\d{2}-\d{2}-\d{2}-.+\.jsonl$"
)


class ImportTraceError(Exception):
    pass


def parse_trace_ref(arg: str, default_server: str) -> tuple[str, str]:
    value = arg.strip().rstrip("/")
    if "://" in value:
        if "/t/" not in value:
            raise ImportTraceError(
                f"not a trace URL (expected .../t/<id>): {arg}"
            )
        server, short_id = value.rsplit("/t/", 1)
        return server, short_id
    if "/" in value or not value:
        raise ImportTraceError(f"not a trace URL or short id: {arg}")
    return default_server.rstrip("/"), value


def _get(url: str, token: str | None) -> tuple[int, bytes, dict]:
    headers = {"User-Agent": "vibeshub-plugin-import"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib_request.Request(url, headers=headers)
    try:
        with urllib_request.urlopen(req, timeout=60.0) as resp:
            return resp.status, resp.read(), dict(resp.headers)
    except urllib_error.HTTPError as e:
        return e.code, e.read(), dict(e.headers)
    except (urllib_error.URLError, TimeoutError, OSError) as e:
        raise ImportTraceError(f"network error: {e}") from e


def fetch_export(
    server_url: str,
    short_id: str,
    target: str,
    *,
    trusted_netloc: str | None = None,
) -> tuple[bytes, dict]:
    """Download an export. The bearer retry only ever goes to
    `trusted_netloc`: a pasted look-alike URL that answers 401 would
    otherwise harvest the user's GitHub token."""
    url = f"{server_url.rstrip('/')}/api/traces/{short_id}/export/{target}"
    status, body, headers = _get(url, token=None)
    if status == 401:
        host = urlsplit(server_url).netloc
        # No trusted host configured means no host is trusted (fail closed).
        if not trusted_netloc or host.lower() != trusted_netloc.lower():
            raise ImportTraceError(
                f"this trace is private and lives on {host}; refusing to "
                "send your GitHub token there. If that server is really "
                f"yours, set VIBESHUB_SERVER_URL=https://{host} and re-run"
            )
        from vibeshub_client.gh_token import GhTokenError, get_gh_token
        try:
            token = get_gh_token()
        except GhTokenError as e:
            raise ImportTraceError(
                f"trace is private and GitHub auth failed: {e}"
            ) from e
        status, body, headers = _get(url, token=token)
    if status == 422:
        detail = ""
        try:
            detail = json.loads(body).get("detail", "")
        except (json.JSONDecodeError, AttributeError):
            pass
        raise ImportTraceError(
            f"this trace cannot be exported to {target}: {detail}"
        )
    if status != 200:
        raise ImportTraceError(
            f"export failed: HTTP {status} "
            f"{body[:200].decode('utf-8', errors='replace')}"
        )
    return body, {k.lower(): v for k, v in headers.items()}


def codex_dest(codex_home: Path, filename: str) -> Path:
    m = _ROLLOUT_RE.match(filename)
    if not m:
        raise ImportTraceError(f"unexpected rollout filename: {filename}")
    y, mo, d = m.group(1), m.group(2), m.group(3)
    return codex_home / "sessions" / y / mo / d / filename


def claude_dest(claude_home: Path, cwd: str, session_uuid: str) -> Path:
    encoded = cwd.replace("/", "-")
    return claude_home / "projects" / encoded / f"{session_uuid}.jsonl"


def re_id_codex(blob: bytes, new_uuid: str) -> bytes:
    lines = blob.split(b"\n")
    for i, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            break
        if (
            isinstance(rec, dict)
            and rec.get("type") == "session_meta"
            and isinstance(rec.get("payload"), dict)
        ):
            rec["payload"]["id"] = new_uuid
            lines[i] = json.dumps(
                rec, ensure_ascii=False, separators=(",", ":")
            ).encode()
        break  # session_meta is the first parseable line
    return b"\n".join(lines)


def re_id_claude(blob: bytes, new_uuid: str) -> bytes:
    out = []
    for line in blob.split(b"\n"):
        if not line.strip():
            out.append(line)
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            out.append(line)
            continue
        if isinstance(rec, dict) and "sessionId" in rec:
            rec["sessionId"] = new_uuid
            out.append(json.dumps(
                rec, ensure_ascii=False, separators=(",", ":")
            ).encode())
        else:
            out.append(line)
    return b"\n".join(out)


def place(dest: Path, blob: bytes) -> Path:
    if dest.exists():
        raise FileExistsError(str(dest))
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".vibeshub-tmp")
    tmp.write_bytes(blob)
    os.replace(tmp, dest)
    return dest


def run_import(
    ref: str, target: str, *, server: str, cwd: str, checkout: bool,
) -> int:
    from vibeshub_client.repo_state import repo_state_report
    server_url, short_id = parse_trace_ref(ref, server)
    blob, headers = fetch_export(
        server_url, short_id, target,
        trusted_netloc=urlsplit(server).netloc,
    )
    filename = headers.get("x-vibeshub-filename", "")
    session_uuid = headers.get("x-vibeshub-session-uuid", "")
    if not filename or not session_uuid:
        raise ImportTraceError(
            "server response is missing export headers; "
            "is the server up to date?"
        )

    if target == "codex":
        codex_home = Path(
            os.environ.get("CODEX_HOME", Path.home() / ".codex")
        )
        dest = codex_dest(codex_home, filename)
        if dest.exists():
            # Already imported: re-id both the bytes and the filename from the
            # header uuid so the copy resumes independently. The header id and
            # the id inside verbatim-served bytes can differ for hostile
            # uploads, so collisions key on the destination path, never on an
            # equality between the two.
            new_uuid = str(uuid.uuid4())
            blob = re_id_codex(blob, new_uuid)
            new_name = filename.replace(session_uuid, new_uuid)
            session_uuid, filename = new_uuid, new_name
            dest = codex_dest(codex_home, filename)
            print(f"session already imported; re-id as {new_uuid}")
        place(dest, blob)
        resume = f"codex resume {session_uuid}"
    elif target == "claude":
        claude_home = Path(
            os.environ.get("CLAUDE_CONFIG_DIR", Path.home() / ".claude")
        )
        dest = claude_dest(claude_home, cwd, session_uuid)
        if dest.exists():
            new_uuid = str(uuid.uuid4())
            blob = re_id_claude(blob, new_uuid)
            session_uuid = new_uuid
            dest = claude_dest(claude_home, cwd, session_uuid)
            print(f"session already imported; re-id as {new_uuid}")
        place(dest, blob)
        resume = f"claude --resume {session_uuid}"
    else:
        raise ImportTraceError(f"unknown target: {target}")

    print(f"placed {dest}")
    for line in repo_state_report(headers, cwd, checkout=checkout):
        print(line)
    print(f"resume with: {resume}")
    return 0
