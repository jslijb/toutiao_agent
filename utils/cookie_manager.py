"""Cookie 管理器，各平台 Cookie 持久化到本地 JSON"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from loguru import logger

from config.settings import PROJECT_ROOT


class CookieManager:
    """Cookie 持久化管理器"""

    def __init__(self, cookie_dir: str | Path | None = None):
        self.cookie_dir = Path(cookie_dir) if cookie_dir else PROJECT_ROOT / "data" / "cookies"
        self.cookie_dir.mkdir(parents=True, exist_ok=True)

    def _cookie_file(self, platform: str) -> Path:
        return self.cookie_dir / f"{platform}_cookies.json"

    def save_cookies(self, platform: str, cookies: list[dict] | dict | str):
        """保存 Cookie 到本地文件"""
        path = self._cookie_file(platform)
        if isinstance(cookies, str):
            # 字符串格式 Cookie（如 "key=value; key2=value2"）
            cookies = self._parse_cookie_string(cookies)
        elif isinstance(cookies, dict):
            # 单个 cookie dict 转为列表
            cookies = [cookies]
        with open(path, "w", encoding="utf-8") as f:
            json.dump(cookies, f, ensure_ascii=False, indent=2)
        logger.info(f"Cookie 已保存: {platform} -> {path}")

    def load_cookies(self, platform: str) -> list[dict] | None:
        """加载 Cookie，不存在返回 None"""
        path = self._cookie_file(platform)
        if not path.exists():
            logger.warning(f"Cookie 文件不存在: {path}")
            return None
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def has_cookies(self, platform: str) -> bool:
        """检查平台是否有已保存的 Cookie"""
        return self._cookie_file(platform).exists()

    def clear_cookies(self, platform: str):
        """清除指定平台的 Cookie"""
        path = self._cookie_file(platform)
        if path.exists():
            path.unlink()
            logger.info(f"Cookie 已清除: {platform}")

    def _parse_cookie_string(self, cookie_str: str) -> list[dict]:
        """将 'key=value; key2=value2' 格式转为 cookie list"""
        cookies = []
        for item in cookie_str.split(";"):
            item = item.strip()
            if "=" in item:
                key, value = item.split("=", 1)
                cookies.append({"name": key.strip(), "value": value.strip(), "domain": "", "path": "/"})
        return cookies

    def to_cookie_header(self, platform: str) -> Optional[str]:
        """将 Cookie 转为 HTTP Header 格式 'key=value; key2=value2'"""
        cookies = self.load_cookies(platform)
        if not cookies:
            return None
        return "; ".join(f"{c.get('name', '')}={c.get('value', '')}" for c in cookies)


# 全局单例
cookie_manager = CookieManager()
