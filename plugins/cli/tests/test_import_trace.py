import json
import uuid
from pathlib import Path

import pytest

from vibeshub_client import gh_token as gh_token_module
from vibeshub_client import import_trace
from vibeshub_client.import_trace import (
    ImportTraceError,
    claude_dest,
    codex_dest,
    fetch_export,
    parse_trace_ref,
    place,
    re_id_claude,
    re_id_codex,
    run_import,
)


def _no_token():
    raise AssertionError("token must not be requested")


def test_parse_trace_ref_url_and_bare():
    assert parse_trace_ref(
        "https://vibeshub.ai/t/abc123", "https://vibeshub.ai"
    ) == ("https://vibeshub.ai", "abc123")
    assert parse_trace_ref("abc123", "https://vibeshub.ai") == (
        "https://vibeshub.ai", "abc123"
    )
    with pytest.raises(ImportTraceError):
        parse_trace_ref("https://vibeshub.ai/nope", "https://vibeshub.ai")


def test_codex_dest_uses_date_dirs():
    p = codex_dest(
        Path("/x/.codex"),
        "rollout-2026-07-24T18-00-00-0191a-b.jsonl",
    )
    assert str(p) == (
        "/x/.codex/sessions/2026/07/24/"
        "rollout-2026-07-24T18-00-00-0191a-b.jsonl"
    )


def test_claude_dest_encodes_cwd():
    p = claude_dest(Path("/x/.claude"), "/Users/y/repo", "abc-def")
    assert str(p) == "/x/.claude/projects/-Users-y-repo/abc-def.jsonl"


def test_re_id_codex_rewrites_session_meta_only():
    old, new = str(uuid.uuid4()), str(uuid.uuid4())
    blob = (
        json.dumps({"timestamp": "t", "type": "session_meta",
                    "payload": {"id": old}}) + "\n" +
        json.dumps({"timestamp": "t", "type": "response_item",
                    "payload": {"type": "message"}}) + "\n"
    ).encode()
    out = re_id_codex(blob, new)
    recs = [json.loads(l) for l in out.splitlines()]
    assert recs[0]["payload"]["id"] == new
    assert recs[1] == json.loads(blob.splitlines()[1])


def test_re_id_claude_rewrites_every_session_id():
    old, new = str(uuid.uuid4()), str(uuid.uuid4())
    blob = b"".join(
        json.dumps({"type": t, "sessionId": old, "uuid": f"u{i}"}).encode()
        + b"\n"
        for i, t in enumerate(["user", "assistant"])
    )
    recs = [json.loads(l) for l in re_id_claude(blob, new).splitlines()]
    assert all(r["sessionId"] == new for r in recs)


def test_place_never_overwrites(tmp_path):
    dest = tmp_path / "a.jsonl"
    assert place(dest, b"one") == dest
    with pytest.raises(FileExistsError):
        place(dest, b"two")
    assert dest.read_bytes() == b"one"


def test_parse_trace_ref_rejects_empty_id():
    with pytest.raises(ImportTraceError):
        parse_trace_ref("https://vibeshub.ai/t/", "https://vibeshub.ai")


def test_re_id_codex_tolerates_malformed_session_meta():
    """A session_meta whose payload is missing or not an object must not
    crash the import; the line is left alone."""
    for record in ({"type": "session_meta"},
                   {"type": "session_meta", "payload": "nope"}):
        blob = json.dumps(record).encode() + b"\n"
        assert re_id_codex(blob, str(uuid.uuid4())) == blob


def test_fetch_export_refuses_token_for_untrusted_host(monkeypatch):
    """A pasted look-alike URL that answers 401 must not receive the user's
    GitHub token."""
    seen = []

    def fake_get(url, token):
        seen.append(token)
        return 401, b"", {}

    monkeypatch.setattr(import_trace, "_get", fake_get)
    monkeypatch.setattr(gh_token_module, "get_gh_token", _no_token)

    with pytest.raises(ImportTraceError) as exc:
        fetch_export(
            "https://vibeshub.ai.evil.test", "abc123", "codex",
            trusted_netloc="vibeshub.ai",
        )
    message = str(exc.value)
    assert "vibeshub.ai.evil.test" in message
    assert "refusing to send your GitHub token" in message
    assert "VIBESHUB_SERVER_URL" in message
    assert seen == [None], "token must not be sent to an untrusted host"


def test_fetch_export_retries_with_bearer_on_trusted_host(monkeypatch):
    seen = []

    def fake_get(url, token):
        seen.append(token)
        if token is None:
            return 401, b"", {}
        return 200, b"blob", {"X-Vibeshub-Filename": "f.jsonl"}

    monkeypatch.setattr(import_trace, "_get", fake_get)
    monkeypatch.setattr(gh_token_module, "get_gh_token", lambda: "tok")

    # Host comparison is case-insensitive, so a differently cased configured
    # server is not mistaken for a look-alike.
    body, headers = fetch_export(
        "https://VibesHub.ai", "abc123", "codex",
        trusted_netloc="vibeshub.ai",
    )
    assert body == b"blob"
    assert headers == {"x-vibeshub-filename": "f.jsonl"}
    assert seen == [None, "tok"]


def test_fetch_export_without_trusted_host_never_sends_token(monkeypatch):
    """The default is fail closed: no trusted host configured, no token."""
    monkeypatch.setattr(
        import_trace, "_get", lambda url, token: (401, b"", {}),
    )
    monkeypatch.setattr(gh_token_module, "get_gh_token", _no_token)
    with pytest.raises(ImportTraceError) as exc:
        fetch_export("https://vibeshub.ai", "abc123", "codex")
    assert "refusing to send your GitHub token" in str(exc.value)


def test_fetch_export_surfaces_422_detail(monkeypatch):
    monkeypatch.setattr(
        import_trace, "_get",
        lambda url, token: (
            422, b'{"detail":"cursor_to_claude_unsupported"}', {},
        ),
    )
    with pytest.raises(ImportTraceError) as exc:
        fetch_export("https://vibeshub.ai", "abc123", "claude")
    assert "cursor_to_claude_unsupported" in str(exc.value)


def _codex_export(session_id):
    blob = (
        json.dumps({"timestamp": "t", "type": "session_meta",
                    "payload": {"id": session_id, "cwd": "/repo"}}) + "\n" +
        json.dumps({"timestamp": "t", "type": "response_item",
                    "payload": {"type": "message"}}) + "\n"
    ).encode()
    filename = f"rollout-2026-07-24T18-00-00-{session_id}.jsonl"
    return blob, {
        "x-vibeshub-filename": filename,
        "x-vibeshub-session-uuid": session_id,
    }


def _claude_export(session_id):
    blob = b"".join(
        json.dumps({"type": t, "sessionId": session_id,
                    "uuid": f"u{i}"}).encode() + b"\n"
        for i, t in enumerate(["user", "assistant"])
    )
    return blob, {
        "x-vibeshub-filename": f"{session_id}.jsonl",
        "x-vibeshub-session-uuid": session_id,
    }


def _stub_fetch(monkeypatch, payload):
    monkeypatch.setattr(
        import_trace, "fetch_export", lambda *a, **k: payload,
    )


def test_run_import_requires_export_headers(monkeypatch):
    _stub_fetch(monkeypatch, (b"x", {}))
    with pytest.raises(ImportTraceError) as exc:
        run_import(
            "abc123", "claude", server="https://vibeshub.ai",
            cwd="/repo", checkout=False,
        )
    assert "missing export headers" in str(exc.value)


def test_run_import_rejects_unknown_target(monkeypatch, tmp_path):
    # Homes are redirected so a regression cannot write into the real ones.
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / ".codex"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / ".claude"))
    _stub_fetch(monkeypatch, _claude_export(str(uuid.uuid4())))
    with pytest.raises(ImportTraceError) as exc:
        run_import(
            "abc123", "cursor", server="https://vibeshub.ai",
            cwd="/repo", checkout=False,
        )
    assert "unknown target" in str(exc.value)


def test_run_import_codex_collision_re_ids(monkeypatch, tmp_path, capsys):
    session_id = str(uuid.uuid4())
    blob, headers = _codex_export(session_id)
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / ".codex"))
    _stub_fetch(monkeypatch, (blob, headers))

    kwargs = dict(server="https://vibeshub.ai", cwd="/repo", checkout=False)
    assert run_import("abc123", "codex", **kwargs) == 0
    day_dir = tmp_path / ".codex" / "sessions" / "2026" / "07" / "24"
    first = day_dir / headers["x-vibeshub-filename"]
    assert first.read_bytes() == blob

    assert run_import("abc123", "codex", **kwargs) == 0
    placed = sorted(day_dir.iterdir())
    assert len(placed) == 2, placed
    dup = next(p for p in placed if p != first)
    meta = json.loads(dup.read_bytes().splitlines()[0])
    new_id = meta["payload"]["id"]
    assert new_id != session_id
    assert new_id in dup.name
    assert meta["payload"]["cwd"] == "/repo"  # rest of session_meta intact
    assert dup.read_bytes().splitlines()[1] == blob.splitlines()[1]
    assert f"codex resume {new_id}" in capsys.readouterr().out


def test_run_import_claude_collision_re_ids(monkeypatch, tmp_path, capsys):
    session_id = str(uuid.uuid4())
    blob, headers = _claude_export(session_id)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / ".claude"))
    _stub_fetch(monkeypatch, (blob, headers))

    kwargs = dict(
        server="https://vibeshub.ai", cwd="/Users/y/repo", checkout=False,
    )
    assert run_import("abc123", "claude", **kwargs) == 0
    project = tmp_path / ".claude" / "projects" / "-Users-y-repo"
    first = project / f"{session_id}.jsonl"
    assert first.read_bytes() == blob

    assert run_import("abc123", "claude", **kwargs) == 0
    placed = sorted(project.iterdir())
    assert len(placed) == 2, placed
    dup = next(p for p in placed if p != first)
    new_id = dup.stem
    records = [json.loads(line) for line in dup.read_bytes().splitlines()]
    assert [r["sessionId"] for r in records] == [new_id, new_id]
    assert [r["uuid"] for r in records] == ["u0", "u1"]
    assert f"claude --resume {new_id}" in capsys.readouterr().out
