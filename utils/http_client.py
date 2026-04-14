"""HTTP 客户端封装，支持重试、超时、代理"""
from __future__ import annotations

import time
from typing import Any, Optional

import httpx
from fake_useragent import UserAgent
from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential

from config.settings import settings

_ua = UserAgent(
    browsers=["chrome", "firefox", "edge"],
    os=["windows", "macos"],
    min_percentage=80.0,
)


def get_random_ua() -> str:
    """获取随机 User-Agent"""
    try:
        return _ua.random
    except Exception:
        return "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"


def get_headers(extra: dict | None = None) -> dict:
    """获取带随机 UA 的请求头"""
    headers = {
        "User-Agent": get_random_ua(),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, deflate",
        "Connection": "keep-alive",
    }
    if extra:
        headers.update(extra)
    return headers


def get_proxies() -> dict | None:
    """获取代理配置"""
    if settings.http_proxy or settings.https_proxy:
        proxies = {}
        if settings.http_proxy:
            proxies["http://"] = settings.http_proxy
        if settings.https_proxy:
            proxies["https://"] = settings.https_proxy
        return proxies
    return None


def _get_proxy_url() -> str | None:
    """Get single proxy URL for httpx (proxy=, not proxies=)"""
    proxy_map = get_proxies()
    if proxy_map:
        return proxy_map.get("https://") or proxy_map.get("http://")
    return None


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
def http_get(
    url: str,
    params: dict | None = None,
    headers: dict | None = None,
    timeout: int = 30,
    **kwargs,
) -> httpx.Response:
    """带重试的 GET 请求"""
    with httpx.Client(proxy=_get_proxy_url(), timeout=timeout) as client:
        resp = client.get(url, params=params, headers=headers or get_headers(), **kwargs)
        resp.raise_for_status()
        return resp


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
def http_post(
    url: str,
    json: dict | None = None,
    data: dict | None = None,
    headers: dict | None = None,
    timeout: int = 30,
    **kwargs,
) -> httpx.Response:
    """带重试的 POST 请求"""
    with httpx.Client(proxy=_get_proxy_url(), timeout=timeout) as client:
        resp = client.post(url, json=json, data=data, headers=headers or get_headers(), **kwargs)
        resp.raise_for_status()
        return resp


def download_file(url: str, save_path: str, timeout: int = 60) -> str:
    """下载文件到本地"""
    with httpx.Client(proxy=_get_proxy_url(), timeout=timeout, follow_redirects=True) as client:
        resp = client.get(url)
        resp.raise_for_status()
        with open(save_path, "wb") as f:
            f.write(resp.content)
    logger.info(f"文件已下载: {save_path}")
    return save_path
