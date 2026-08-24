"""Supabase 접속 설정."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import httpx
from supabase import Client, create_client
from supabase.lib.client_options import SyncClientOptions

# PostgREST / Storage 호출 타임아웃 (Windows 일시적 소켓 지연 대비)
_HTTP_TIMEOUT = httpx.Timeout(
    connect=30.0,
    read=90.0,
    write=90.0,
    pool=90.0,
)


def _read_secrets_file() -> dict[str, Any]:
    path = Path(__file__).resolve().parent.parent / ".streamlit" / "secrets.toml"
    if not path.exists():
        return {}
    try:
        import tomllib
    except ModuleNotFoundError:  # pragma: no cover
        return {}
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    block = data.get("supabase") or {}
    return dict(block) if isinstance(block, dict) else {}


def load_supabase_settings() -> tuple[str, str]:
    url = (os.environ.get("SUPABASE_URL") or "").strip()
    key = (
        os.environ.get("SUPABASE_KEY")
        or os.environ.get("SUPABASE_ANON_KEY")
        or ""
    ).strip()
    if not url or not key:
        file_cfg = _read_secrets_file()
        url = url or str(file_cfg.get("url") or "").strip()
        key = key or str(file_cfg.get("key") or "").strip()
    if not url or not key:
        try:
            import streamlit as st

            block = st.secrets.get("supabase", {})
            url = url or str(block.get("url") or "").strip()
            key = key or str(block.get("key") or "").strip()
        except Exception:  # noqa: BLE001
            pass
    return url, key


def is_transient_network_error(exc: BaseException) -> bool:
    """일시적 네트워크/소켓 오류 여부 (재시도·사용자 안내용)."""
    if isinstance(
        exc,
        (
            httpx.ReadError,
            httpx.ConnectError,
            httpx.ConnectTimeout,
            httpx.ReadTimeout,
            httpx.WriteTimeout,
            httpx.PoolTimeout,
            httpx.TimeoutException,
            httpx.RemoteProtocolError,
            httpx.NetworkError,
            ConnectionError,
            TimeoutError,
            BrokenPipeError,
        ),
    ):
        return True
    if isinstance(exc, OSError):
        # WinError 10035 (WSAEWOULDBLOCK), 10053, 10054, 10060 등
        win = getattr(exc, "winerror", None)
        if win in {10035, 10053, 10054, 10060, 10061}:
            return True
        if getattr(exc, "errno", None) in {11, 35, 54, 60, 61, 104, 110}:
            return True
    text = str(exc).lower()
    needles = (
        "10035",
        "would block",
        "wouldblock",
        "비동기 소켓",
        "timed out",
        "timeout",
        "connection reset",
        "connection aborted",
        "temporarily unavailable",
        "network is unreachable",
        "name or service not known",
        "getaddrinfo failed",
    )
    return any(n in text for n in needles)


def reset_supabase_client() -> None:
    """캐시된 클라이언트를 버리고 다음 호출에서 재생성."""
    get_supabase_client.cache_clear()


@lru_cache(maxsize=1)
def get_supabase_client() -> Client:
    url, key = load_supabase_settings()
    if not url or not key:
        raise RuntimeError(
            "Supabase 설정이 없습니다. .streamlit/secrets.toml 의 "
            "[supabase] url, key 를 확인하세요."
        )
    # create_client 는 SyncClientOptions 를 요구함 (ClientOptions 에는 storage 없음)
    options = SyncClientOptions(
        postgrest_client_timeout=_HTTP_TIMEOUT,
        storage_client_timeout=90,
        function_client_timeout=60,
        httpx_client=httpx.Client(timeout=_HTTP_TIMEOUT),
    )
    return create_client(url, key, options=options)
