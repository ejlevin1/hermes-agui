"""HTTP client for the Hermes API server (``/p/<profile>/v1/runs``): start, stream events, stop (S7)."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import httpx

__all__ = ["HttpHermesClient", "HermesError"]

_TERMINAL = {"run.completed", "run.failed", "run.cancelled", "run.interrupted"}


class HermesError(RuntimeError):
    """The Hermes API server refused or failed a request."""


class HttpHermesClient:
    """:class:`~hermes_agui.api.HermesClient` over HTTP. ``base_url`` e.g. ``http://127.0.0.1:8642``.

    Pass ``client`` to inject an ``httpx.AsyncClient`` (tests use ``httpx.MockTransport``).
    ``events`` re-attaches with ``Last-Event-ID`` when the stream drops before a terminal frame (Hermes replays by seq).
    """

    def __init__(
        self,
        base_url: str,
        api_key: str = "",
        *,
        client: httpx.AsyncClient | None = None,
        reconnects: int = 2,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        # Hermes sends an SSE keepalive every 10 s (S7), so a 60 s read timeout means a dead stream.
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(30.0, read=60.0)
        )
        self._owns = client is None
        self._reconnects = reconnects

    def _url(self, profile: str, path: str) -> str:
        return f"{self._base}/p/{profile}{path}"

    async def aclose(self) -> None:
        if self._owns:
            await self._client.aclose()

    async def start_run(
        self, *, profile: str, session_id: str, input_text: str, instructions: str
    ) -> str:
        body = {
            "input": input_text,
            "session_id": session_id,
            "instructions": instructions,
        }
        r = await self._client.post(
            self._url(profile, "/v1/runs"), json=body, headers=self._headers
        )
        if r.status_code >= 300:
            raise HermesError(f"POST /v1/runs -> {r.status_code}: {r.text[:200]}")
        run_id = r.json().get("run_id")
        if not run_id:
            raise HermesError("POST /v1/runs: response has no run_id")
        return str(run_id)

    async def events(self, *, profile: str, run_id: str) -> AsyncIterator[dict]:
        last_seq = -1
        attempts = 0
        url = self._url(profile, f"/v1/runs/{run_id}/events")
        while True:
            headers = dict(self._headers, Accept="text/event-stream")
            if last_seq >= 0:
                headers["Last-Event-ID"] = str(last_seq)
            ended = False
            try:
                async with self._client.stream("GET", url, headers=headers) as r:
                    if r.status_code >= 300:
                        raise HermesError(f"GET events -> {r.status_code}")
                    async for line in r.aiter_lines():
                        if not line.startswith("data:"):
                            continue  # comments (": open", ": keepalive"), "id:" lines, blanks
                        try:
                            frame = json.loads(line[5:].strip())
                        except ValueError:
                            continue
                        if isinstance(frame.get("seq"), int):
                            last_seq = max(last_seq, frame["seq"])
                        yield frame
                        if frame.get("event") in _TERMINAL:
                            ended = True
                            break
            except (httpx.TransportError, httpx.RemoteProtocolError):
                if attempts >= self._reconnects:
                    raise
            if ended:
                return
            attempts += 1
            if attempts > self._reconnects:
                return  # stream closed without a terminal frame: the consumer reports it

    async def stop(self, *, profile: str, run_id: str) -> None:
        r = await self._client.post(
            self._url(profile, f"/v1/runs/{run_id}/stop"), headers=self._headers
        )
        if (
            r.status_code >= 300 and r.status_code != 404
        ):  # 404 = already gone: idempotent
            raise HermesError(f"POST /v1/runs/{run_id}/stop -> {r.status_code}")
