from __future__ import annotations

import asyncio
import json
import ssl
import subprocess
import sys
from dataclasses import dataclass
from urllib import error as urllib_error
from urllib import request as urllib_request


class UploadError(Exception):
    pass


_CERT_HELP = (
    "TLS certificate verification failed. A corporate proxy or VPN is likely "
    "intercepting HTTPS traffic with a private root CA that Python does not "
    "trust. Export that CA to a PEM file and point the SSL_CERT_FILE "
    "environment variable at it."
)


def _is_cert_verify_error(exc: BaseException) -> bool:
    """True if a urllib failure was caused by TLS certificate verification."""
    return "CERTIFICATE_VERIFY_FAILED" in str(exc)


def _keychain_ca_pem() -> str | None:
    """Every CA certificate the macOS keychain trusts, as concatenated PEM.

    A network that intercepts TLS installs its root CA into the OS keychain
    (which the browser uses) but not into Python's bundled CA store. Returns
    None off macOS or on any failure.
    """
    if sys.platform != "darwin":
        return None
    try:
        proc = subprocess.run(
            ["/usr/bin/security", "find-certificate", "-a", "-p"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0 or not proc.stdout.strip():
        return None
    return proc.stdout


def _windows_ca_der() -> bytes | None:
    """Every trusted CA certificate in the Windows cert stores, as concatenated
    DER. A network that intercepts TLS installs its root CA into the system
    store (which the browser uses) but not into Python's bundled CA store.
    Returns None off Windows or on any failure.
    """
    if sys.platform != "win32" or not hasattr(ssl, "enum_certificates"):
        return None
    der_blobs: list[bytes] = []
    for store in ("ROOT", "CA"):
        try:
            entries = ssl.enum_certificates(store)
        except (OSError, ValueError):
            continue
        for cert_bytes, encoding, trust in entries:
            # Skip non-X.509 blobs and certs Windows explicitly distrusts.
            if encoding == "x509_asn" and trust is not False:
                der_blobs.append(cert_bytes)
    return b"".join(der_blobs) if der_blobs else None


def _truststore_context() -> ssl.SSLContext | None:
    """An SSL context that verifies against the native OS trust store via the
    vendored `truststore` package.

    This is the same trust evaluation the system browser uses, so it sees CAs
    installed by MDM configuration profiles or a proxy/VPN client — which a
    raw keychain scrape can miss. Returns None on Python < 3.10 (truststore's
    floor) or if truststore cannot be loaded for any reason; the caller falls
    back to `_scraped_ca_context`.
    """
    if sys.version_info < (3, 10):
        return None
    try:
        from vibeshub_client._vendor import truststore

        return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    except Exception:
        # Best-effort fallback path — must never propagate.
        return None


def _scraped_ca_context() -> ssl.SSLContext | None:
    """Fallback for Python < 3.10 (no truststore): the default SSL context
    augmented with CAs scraped from the macOS keychain or Windows cert stores.
    `load_verify_locations` accepts the keychain PEM (str) or store DER
    (bytes). None if no extra CAs can be loaded."""
    ca_data = _keychain_ca_pem() or _windows_ca_der()
    if not ca_data:
        return None
    ctx = ssl.create_default_context()
    try:
        ctx.load_verify_locations(cadata=ca_data)
    except ssl.SSLError:
        return None
    return ctx


def _os_trust_context() -> ssl.SSLContext | None:
    """An SSL context that trusts the OS-managed trust store, or None if one
    cannot be built. Prefers `truststore`; falls back to a keychain scrape."""
    return _truststore_context() or _scraped_ca_context()


def _request(
    req: urllib_request.Request, *, timeout: float, context: ssl.SSLContext | None,
) -> tuple[int, bytes]:
    kwargs: dict = {"timeout": timeout}
    if context is not None:
        kwargs["context"] = context
    with urllib_request.urlopen(req, **kwargs) as resp:
        return resp.status, resp.read()


@dataclass
class UploadResult:
    trace_id: str
    short_id: str
    trace_url: str
    created: bool = True
    digest: dict | None = None


def _parse_response(data: dict) -> UploadResult:
    return UploadResult(
        trace_id=data["trace_id"],
        short_id=data["short_id"],
        trace_url=data["trace_url"],
        created=data.get("created", True),
        digest=data.get("ai_digest"),
    )


def _post_bytes(
    url: str, *, headers: dict, body: bytes, timeout: float,
) -> tuple[int, bytes]:
    req = urllib_request.Request(url, data=body, headers=headers, method="POST")
    try:
        return _request(req, timeout=timeout, context=None)
    except urllib_error.HTTPError as e:
        # Non-2xx response: surface status + body so the caller can format an error.
        return e.code, e.read()
    except (urllib_error.URLError, TimeoutError, OSError) as e:
        if not _is_cert_verify_error(e):
            raise UploadError(f"network error: {e}") from e
        # Default TLS verification failed — most likely a corporate proxy or
        # VPN intercepting HTTPS. Retry once trusting the OS trust store,
        # where the interception root CA lives (the browser uses it too).
        ctx = _os_trust_context()
        if ctx is None:
            raise UploadError(f"network error: {_CERT_HELP}") from e
        try:
            return _request(req, timeout=timeout, context=ctx)
        except urllib_error.HTTPError as e2:
            return e2.code, e2.read()
        except (urllib_error.URLError, TimeoutError, OSError) as e2:
            if _is_cert_verify_error(e2):
                raise UploadError(f"network error: {_CERT_HELP}") from e2
            raise UploadError(f"network error: {e2}") from e2


async def upload_bundle(
    *,
    server_url: str,
    token: str,
    tar_bytes: bytes,
    pr_url: str | None,
    repo_full_name: str | None,
    plugin_version: str,
    session_id: str | None,
    redaction_count_client: int,
    platform: str = "claude-code",
    timeout: float = 60.0,
    git_branch: str | None = None,
    git_commit: str | None = None,
) -> UploadResult:
    url = f"{server_url.rstrip('/')}/api/ingest"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/x-tar",
        # Cloudflare bot protection bans urllib's default Python-urllib/x.y
        # signature (403 error code 1010).
        "User-Agent": f"vibeshub-plugin/{plugin_version}",
        "X-Vibeshub-Platform": platform,
        "X-Vibeshub-Plugin-Version": plugin_version,
        "X-Vibeshub-Client-Redactions": str(redaction_count_client),
    }
    if pr_url:
        headers["X-Vibeshub-Pr-Url"] = pr_url
    if repo_full_name:
        headers["X-Vibeshub-Repo"] = repo_full_name
    if session_id:
        headers["X-Vibeshub-Session-Id"] = session_id
    if git_branch:
        headers["X-Vibeshub-Git-Branch"] = git_branch
    if git_commit:
        headers["X-Vibeshub-Git-Commit"] = git_commit

    status, raw = await asyncio.to_thread(
        _post_bytes, url, headers=headers, body=tar_bytes, timeout=timeout,
    )

    if status != 201:
        text = raw.decode("utf-8", errors="replace")
        raise UploadError(f"upload failed: {status} {text}")

    data = json.loads(raw.decode("utf-8"))
    return _parse_response(data)
