import io
import json
import ssl
import subprocess
import sys
from unittest.mock import patch
from urllib import error as urllib_error

import pytest

from vibeshub_client.upload import UploadError, upload_bundle


class _FakeResponse:
    def __init__(self, *, status: int, body: bytes):
        self.status = status
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _ok_response() -> _FakeResponse:
    return _FakeResponse(
        status=201,
        body=json.dumps(
            {
                "trace_id": "00000000-0000-0000-0000-000000000001",
                "short_id": "abc1234567",
                "trace_url": "https://vibeshub.test/alice/repo/pull/3/abc1234567",
            }
        ).encode("utf-8"),
    )


def _http_error(code: int, body: bytes = b"") -> urllib_error.HTTPError:
    return urllib_error.HTTPError(
        url="https://vibeshub.test/api/ingest",
        code=code,
        msg="error",
        hdrs=None,
        fp=io.BytesIO(body),
    )


@pytest.mark.asyncio
async def test_upload_success_sends_tar_and_headers():
    captured: dict = {}

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["headers"] = dict(req.header_items())
        captured["body"] = req.data
        captured["timeout"] = timeout
        return _ok_response()

    with patch("vibeshub_client.upload.urllib_request.urlopen", side_effect=fake_urlopen):
        result = await upload_bundle(
            server_url="https://vibeshub.test",
            token="ghp_test",
            tar_bytes=b"\x1f\x8b\x08\x00fake-tar-bytes",
            pr_url="https://github.com/alice/repo/pull/3",
            repo_full_name=None,
            plugin_version="0.2.0",
            session_id="abc",
            redaction_count_client=2,
        )

    assert result.short_id == "abc1234567"
    assert result.trace_url.endswith("abc1234567")
    assert captured["url"] == "https://vibeshub.test/api/ingest"
    # urllib title-cases header names
    headers = captured["headers"]
    assert headers["Authorization"] == "Bearer ghp_test"
    assert headers["Content-type"] == "application/x-tar"
    assert headers["X-vibeshub-pr-url"] == "https://github.com/alice/repo/pull/3"
    assert headers["X-vibeshub-platform"] == "claude-code"
    assert headers["X-vibeshub-plugin-version"] == "0.2.0"
    assert headers["X-vibeshub-client-redactions"] == "2"
    assert headers["X-vibeshub-session-id"] == "abc"
    # Body is the tar bytes verbatim.
    assert captured["body"] == b"\x1f\x8b\x08\x00fake-tar-bytes"


@pytest.mark.asyncio
async def test_upload_sends_codex_platform():
    captured: dict = {}

    def fake_urlopen(req, timeout=None):
        captured["headers"] = dict(req.header_items())
        return _ok_response()

    with patch("vibeshub_client.upload.urllib_request.urlopen", side_effect=fake_urlopen):
        await upload_bundle(
            server_url="https://vibeshub.test",
            token="t",
            tar_bytes=b"x",
            pr_url=None,
            repo_full_name=None,
            plugin_version="0.4.0",
            session_id=None,
            redaction_count_client=0,
            platform="codex",
        )

    # urllib title-cases header names
    assert captured["headers"]["X-vibeshub-platform"] == "codex"


@pytest.mark.asyncio
async def test_upload_omits_session_header_when_none():
    captured: dict = {}

    def fake_urlopen(req, timeout=None):
        captured["headers"] = dict(req.header_items())
        return _ok_response()

    with patch("vibeshub_client.upload.urllib_request.urlopen", side_effect=fake_urlopen):
        await upload_bundle(
            server_url="https://vibeshub.test",
            token="ghp_test",
            tar_bytes=b"tar",
            pr_url="https://github.com/alice/repo/pull/3",
            repo_full_name=None,
            plugin_version="0.2.0",
            session_id=None,
            redaction_count_client=0,
        )

    assert "X-vibeshub-session-id" not in captured["headers"]


@pytest.mark.asyncio
async def test_upload_default_timeout_is_60s():
    """Default socket-read timeout must be 60s so it doesn't starve the
    server's worst-case happy path (two sequential GitHub calls @ 10s
    each + blob put + DB commit). See plugins/cli investigation
    of the 30s read-timeout incident."""
    captured: dict = {}

    def fake_urlopen(req, timeout=None):
        captured["timeout"] = timeout
        return _ok_response()

    with patch("vibeshub_client.upload.urllib_request.urlopen", side_effect=fake_urlopen):
        await upload_bundle(
            server_url="https://vibeshub.test",
            token="ghp_test",
            tar_bytes=b"tar",
            pr_url="https://github.com/alice/repo/pull/3",
            repo_full_name=None,
            plugin_version="0.2.0",
            session_id=None,
            redaction_count_client=0,
        )

    assert captured["timeout"] == 60.0


@pytest.mark.asyncio
async def test_upload_401_raises_unauthorized():
    with patch(
        "vibeshub_client.upload.urllib_request.urlopen",
        side_effect=_http_error(401, b'{"detail":"x"}'),
    ):
        with pytest.raises(UploadError) as exc:
            await upload_bundle(
                server_url="https://vibeshub.test",
                token="bad",
                tar_bytes=b"tar",
                pr_url="https://github.com/alice/repo/pull/3",
                repo_full_name=None,
                plugin_version="0.2.0",
                session_id=None,
                redaction_count_client=0,
            )

    assert "401" in str(exc.value)


@pytest.mark.asyncio
async def test_upload_5xx_raises_server_error():
    with patch(
        "vibeshub_client.upload.urllib_request.urlopen",
        side_effect=_http_error(503),
    ):
        with pytest.raises(UploadError):
            await upload_bundle(
                server_url="https://vibeshub.test",
                token="ghp_test",
                tar_bytes=b"tar",
                pr_url="https://github.com/alice/repo/pull/3",
                repo_full_name=None,
                plugin_version="0.2.0",
                session_id=None,
                redaction_count_client=0,
            )


async def _upload(**overrides):
    kwargs = dict(
        server_url="https://vibeshub.test",
        token="ghp_test",
        tar_bytes=b"tar",
        pr_url="https://github.com/alice/repo/pull/3",
        repo_full_name=None,
        plugin_version="0.2.0",
        session_id=None,
        redaction_count_client=0,
    )
    kwargs.update(overrides)
    return await upload_bundle(**kwargs)


@pytest.mark.asyncio
async def test_upload_standalone_sends_no_pr_or_repo_header():
    captured: dict = {}

    def fake_urlopen(req, timeout=None):
        captured["headers"] = dict(req.header_items())
        return _ok_response()

    with patch("vibeshub_client.upload.urllib_request.urlopen", side_effect=fake_urlopen):
        await upload_bundle(
            server_url="https://vibeshub.test",
            token="ghp_test",
            tar_bytes=b"tar",
            pr_url=None,
            repo_full_name=None,
            plugin_version="0.2.0",
            session_id=None,
            redaction_count_client=0,
        )

    assert "X-vibeshub-pr-url" not in captured["headers"]
    assert "X-vibeshub-repo" not in captured["headers"]


@pytest.mark.asyncio
async def test_upload_repo_only_sends_repo_header_not_pr_header():
    captured: dict = {}

    def fake_urlopen(req, timeout=None):
        captured["headers"] = dict(req.header_items())
        return _ok_response()

    with patch("vibeshub_client.upload.urllib_request.urlopen", side_effect=fake_urlopen):
        await upload_bundle(
            server_url="https://vibeshub.test",
            token="ghp_test",
            tar_bytes=b"tar",
            pr_url=None,
            repo_full_name="alice/repo",
            plugin_version="0.2.0",
            session_id=None,
            redaction_count_client=0,
        )

    assert "X-vibeshub-pr-url" not in captured["headers"]
    assert captured["headers"]["X-vibeshub-repo"] == "alice/repo"


@pytest.mark.asyncio
async def test_upload_sends_plugin_user_agent():
    """Cloudflare bot protection bans urllib's default Python-urllib/x.y
    signature (403 error code 1010), so the uploader must identify itself
    with an explicit User-Agent."""
    captured: dict = {}

    def fake_urlopen(req, timeout=None):
        captured["headers"] = dict(req.header_items())
        return _ok_response()

    with patch("vibeshub_client.upload.urllib_request.urlopen", side_effect=fake_urlopen):
        await _upload(plugin_version="0.5.0")

    # urllib title-cases header names
    assert captured["headers"].get("User-agent") == "vibeshub-plugin/0.5.0"


@pytest.mark.asyncio
async def test_upload_sends_git_headers():
    """Branch/commit are captured client-side at share time, so the uploader
    forwards them as ingest headers."""
    captured: dict = {}

    def fake_urlopen(req, timeout=None):
        captured["headers"] = dict(req.header_items())
        return _ok_response()

    with patch("vibeshub_client.upload.urllib_request.urlopen", side_effect=fake_urlopen):
        await _upload(git_branch="main", git_commit="d" * 40)

    # urllib title-cases header names
    assert captured["headers"]["X-vibeshub-git-branch"] == "main"
    assert captured["headers"]["X-vibeshub-git-commit"] == "d" * 40


@pytest.mark.asyncio
async def test_upload_omits_git_headers_when_none():
    captured: dict = {}

    def fake_urlopen(req, timeout=None):
        captured["headers"] = dict(req.header_items())
        return _ok_response()

    with patch("vibeshub_client.upload.urllib_request.urlopen", side_effect=fake_urlopen):
        await _upload(git_branch=None, git_commit=None)

    assert "X-vibeshub-git-branch" not in captured["headers"]
    assert "X-vibeshub-git-commit" not in captured["headers"]


_CERT_ERR = urllib_error.URLError(
    "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: "
    "self signed certificate in certificate chain (_ssl.c:997)"
)


@pytest.mark.asyncio
async def test_upload_retries_with_os_trust_when_default_verify_fails():
    """A corporate proxy's root CA isn't in Python's bundled store, so the
    first attempt fails TLS verification. The retry trusts the OS keychain."""
    calls: list[dict] = []

    def fake_urlopen(req, **kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise _CERT_ERR
        return _ok_response()

    with patch(
        "vibeshub_client.upload._os_trust_context",
        return_value=ssl.create_default_context(),
    ), patch(
        "vibeshub_client.upload.urllib_request.urlopen", side_effect=fake_urlopen
    ):
        result = await _upload()

    assert result.short_id == "abc1234567"
    assert len(calls) == 2
    # First attempt uses the default context; the retry passes an explicit one.
    assert "context" not in calls[0]
    assert "context" in calls[1]


@pytest.mark.asyncio
async def test_upload_cert_failure_without_os_trust_raises_actionable_error():
    with patch(
        "vibeshub_client.upload._os_trust_context", return_value=None
    ), patch(
        "vibeshub_client.upload.urllib_request.urlopen", side_effect=_CERT_ERR
    ):
        with pytest.raises(UploadError) as exc:
            await _upload()

    msg = str(exc.value).lower()
    assert "intercept" in msg or "proxy" in msg


@pytest.mark.asyncio
async def test_upload_cert_failure_persisting_after_retry_raises_actionable_error():
    with patch(
        "vibeshub_client.upload._os_trust_context",
        return_value=ssl.create_default_context(),
    ), patch(
        "vibeshub_client.upload.urllib_request.urlopen", side_effect=_CERT_ERR
    ):
        with pytest.raises(UploadError) as exc:
            await _upload()

    msg = str(exc.value).lower()
    assert "intercept" in msg or "proxy" in msg


@pytest.mark.asyncio
async def test_upload_non_cert_network_error_raises_plain_error():
    with patch(
        "vibeshub_client.upload.urllib_request.urlopen",
        side_effect=urllib_error.URLError("connection refused"),
    ):
        with pytest.raises(UploadError) as exc:
            await _upload()

    assert "network error" in str(exc.value)
    assert "connection refused" in str(exc.value)


def test_keychain_ca_pem_returns_none_off_macos():
    from vibeshub_client import upload

    with patch.object(upload.sys, "platform", "linux"):
        assert upload._keychain_ca_pem() is None


def test_keychain_ca_pem_runs_security_on_macos():
    from vibeshub_client import upload

    fake = subprocess.CompletedProcess(
        args=[],
        returncode=0,
        stdout="-----BEGIN CERTIFICATE-----\nXXX\n-----END CERTIFICATE-----\n",
        stderr="",
    )
    with patch.object(upload.sys, "platform", "darwin"), patch.object(
        upload.subprocess, "run", return_value=fake
    ) as run:
        pem = upload._keychain_ca_pem()

    assert pem is not None and "BEGIN CERTIFICATE" in pem
    argv = run.call_args[0][0]
    assert argv[0] == "/usr/bin/security"
    assert argv[1] == "find-certificate"


def test_os_trust_context_is_none_when_no_trust_source_available():
    from vibeshub_client import upload

    with patch.object(
        upload, "_truststore_context", return_value=None
    ), patch.object(
        upload, "_keychain_ca_pem", return_value=None
    ), patch.object(upload, "_windows_ca_der", return_value=None):
        assert upload._os_trust_context() is None


@pytest.mark.skipif(
    sys.version_info < (3, 10), reason="truststore requires Python 3.10+"
)
def test_truststore_context_builds_on_modern_python():
    from vibeshub_client import upload

    ctx = upload._truststore_context()
    assert ctx is not None


def test_truststore_context_is_none_on_python_39():
    from vibeshub_client import upload

    with patch.object(upload.sys, "version_info", (3, 9, 18)):
        assert upload._truststore_context() is None


def test_os_trust_context_prefers_truststore_over_scraping():
    from vibeshub_client import upload

    marker = ssl.create_default_context()
    with patch.object(
        upload, "_truststore_context", return_value=marker
    ), patch.object(upload, "_keychain_ca_pem") as keychain:
        assert upload._os_trust_context() is marker
    keychain.assert_not_called()


def test_windows_ca_der_returns_none_off_windows():
    from vibeshub_client import upload

    with patch.object(upload.sys, "platform", "darwin"):
        assert upload._windows_ca_der() is None


def test_windows_ca_der_collects_trusted_der_from_cert_stores():
    from vibeshub_client import upload

    store_entries = {
        "ROOT": [
            (b"DER-ROOT-1", "x509_asn", True),
            (b"DER-ROOT-2", "x509_asn", {"1.3.6.1.5.5.7.3.1"}),
            (b"PKCS7-BLOB", "pkcs_7_asn", True),  # wrong encoding -> skipped
            (b"DER-DISTRUSTED", "x509_asn", False),  # distrusted -> skipped
        ],
        "CA": [(b"DER-INTERMEDIATE", "x509_asn", True)],
    }

    def fake_enum(store):
        return store_entries[store]

    with patch.object(upload.sys, "platform", "win32"), patch.object(
        upload.ssl, "enum_certificates", fake_enum, create=True
    ):
        der = upload._windows_ca_der()

    assert der == b"DER-ROOT-1" + b"DER-ROOT-2" + b"DER-INTERMEDIATE"


def test_upload_result_round_trips_digest():
    """UploadResult parses an optional digest dict from the backend response."""
    from vibeshub_client.upload import UploadResult, _parse_response
    payload = {
        "trace_id": "t1", "short_id": "abc12345",
        "trace_url": "https://vibeshub.test/t/abc12345",
        "ai_digest": {
            "ask": "test ask", "decisions": "d", "files": "f",
            "tests": "t", "dead_ends": "e",
            "chapters": [],
        },
    }
    result = _parse_response(payload)
    assert result.digest == payload["ai_digest"]


def test_upload_result_digest_optional():
    from vibeshub_client.upload import _parse_response
    payload = {
        "trace_id": "t1", "short_id": "abc12345",
        "trace_url": "https://vibeshub.test/t/abc12345",
    }
    result = _parse_response(payload)
    assert result.digest is None
