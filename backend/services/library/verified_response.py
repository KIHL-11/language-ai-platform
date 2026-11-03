from __future__ import annotations

import re

import anyio
from starlette.datastructures import Headers
from starlette.responses import Response
from starlette.types import Receive, Scope, Send

from services.library.owner_repository import OwnerScopedAudio


CHUNK_BYTES = 64 * 1024
_SINGLE_RANGE = re.compile(r"bytes=(\d*)-(\d*)", re.ASCII)
_UNSATISFIABLE = object()


class VerifiedAudioResponse(Response):
    """Send bytes only from the handle whose SHA-256 was verified.

    Built on the public ``Response`` ASGI contract instead of ``FileResponse``:
    nothing here knows the object's path, so neither a reopened path nor an
    ``http.response.pathsend`` message can serve bytes that were swapped after
    verification. A single byte range gets 206; several ranges, malformed or
    stale (``If-Range``) ranges get the whole body (RFC 9110 lets a server
    ignore Range); a range past the end gets 416. The handle is closed when the
    response finishes or fails.
    """

    def __init__(self, audio: OwnerScopedAudio):
        self._audio = audio
        self.status_code = 200
        self.media_type = audio.content_type
        self.background = None
        self.body = b""
        self._etag = f'"{audio.sha256}"'
        self.init_headers(
            {
                "accept-ranges": "bytes",
                "etag": self._etag,
                "content-length": str(audio.byte_size),
            }
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await self._send(scope, send)
        finally:
            self._audio.close()

    async def _send(self, scope: Scope, send: Send) -> None:
        size = self._audio.byte_size
        request_headers = Headers(scope=scope)
        selected = self._selected_range(request_headers, size)
        headers = [
            (b"content-type", self.media_type.encode("latin-1")),
            (b"accept-ranges", b"bytes"),
            (b"etag", self._etag.encode("latin-1")),
        ]
        if selected is _UNSATISFIABLE:
            headers += [
                (b"content-range", f"bytes */{size}".encode("latin-1")),
                (b"content-length", b"0"),
            ]
            await send({"type": "http.response.start", "status": 416, "headers": headers})
            await send({"type": "http.response.body", "body": b"", "more_body": False})
            return
        if selected is None:
            status, start, end = 200, 0, size
        else:
            status, (start, end) = 206, selected
            headers.append(
                (b"content-range", f"bytes {start}-{end - 1}/{size}".encode("latin-1"))
            )
        headers.append((b"content-length", str(end - start).encode("latin-1")))
        await send({"type": "http.response.start", "status": status, "headers": headers})
        if scope.get("method", "GET").upper() == "HEAD" or start == end:
            await send({"type": "http.response.body", "body": b"", "more_body": False})
            return
        await anyio.to_thread.run_sync(self._audio.file.seek, start)
        remaining = end - start
        while remaining:
            chunk = await anyio.to_thread.run_sync(
                self._audio.file.read, min(CHUNK_BYTES, remaining)
            )
            if not chunk:
                # The verified inode was shortened in place. Abort instead of
                # sending fewer bytes than the declared content-length.
                raise RuntimeError("Verified audio ended early")
            remaining -= len(chunk)
            await send(
                {"type": "http.response.body", "body": chunk, "more_body": bool(remaining)}
            )

    def _selected_range(self, headers: Headers, size: int):
        """Return None (whole body), _UNSATISFIABLE, or one (start, end)."""

        value = headers.get("range")
        if value is None:
            return None
        if_range = headers.get("if-range")
        if if_range is not None and if_range != self._etag:
            return None
        match = _SINGLE_RANGE.fullmatch(value.strip())
        if match is None:
            return None
        first, last = match.groups()
        if not first and not last:
            return None
        if not first:
            suffix = int(last)
            if suffix == 0:
                return _UNSATISFIABLE
            return max(size - suffix, 0), size
        start = int(first)
        if last and int(last) < start:
            return None
        if start >= size:
            return _UNSATISFIABLE
        end = size if not last else min(int(last) + 1, size)
        return start, end
