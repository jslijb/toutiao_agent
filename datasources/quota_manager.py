"""配额管理 - Bing API月度配额 + 天行天豆余额"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Optional

from loguru import logger

from config.settings import PROJECT_ROOT
from datasources.models import QuotaState


class QuotaManager:
    """配额持久化管理（Bing月度计数 + 天豆余额）"""

    def __init__(self):
        self._state_dir = PROJECT_ROOT / "data" / "quota"
        self._state_file = self._state_dir / "quota_state.json"
        self._state_dir.mkdir(parents=True, exist_ok=True)
        self._state = self._load_state()
        self._sync_config()

    @property
    def state(self) -> QuotaState:
        return self._state

    def check_bing_quota(self, required: int = 1) -> bool:
        """检查Bing API剩余配额是否充足"""
        self._auto_reset_if_new_month()
        remaining = self.get_bing_remaining()
        if remaining < required:
            logger.warning(
                f"Bing API 配额不足: 剩余 {remaining}, 需要 {required}, "
                f"本月已用 {self._state.bing_used_count}/{self._state.bing_monthly_limit}"
            )
            return False
        return True

    def consume_bing(self, count: int = 1) -> None:
        """消耗Bing API配额"""
        self._auto_reset_if_new_month()
        self._state.bing_used_count += count
        self._state.last_updated = datetime.now().isoformat()
        self._save_state()
        logger.debug(f"Bing API 配额消耗 {count}, 本月已用 {self._state.bing_used_count}/{self._state.bing_monthly_limit}")

    def consume_tianapi_beans(self, beans: float) -> None:
        """消耗天行天豆"""
        self._state.tianapi_used_beans += beans
        self._state.last_updated = datetime.now().isoformat()
        self._save_state()

    def get_bing_remaining(self) -> int:
        """返回Bing本月剩余调用次数"""
        self._auto_reset_if_new_month()
        return max(0, self._state.bing_monthly_limit - self._state.bing_used_count)

    def get_tianapi_remaining_beans(self) -> float:
        """返回天豆剩余余额"""
        return max(0.0, self._state.tianapi_total_beans - self._state.tianapi_used_beans)

    def reset_bing_quota(self) -> None:
        """手动重置Bing月度计数器"""
        self._state.bing_used_count = 0
        self._state.bing_reset_month = datetime.now().strftime("%Y-%m")
        self._state.last_updated = datetime.now().isoformat()
        self._save_state()
        logger.info("Bing API 月度配额已手动重置")

    def _auto_reset_if_new_month(self) -> None:
        """检测跨月自动重置Bing计数器"""
        current_month = datetime.now().strftime("%Y-%m")
        if self._state.bing_reset_month != current_month:
            logger.info(
                f"检测到跨月 ({self._state.bing_reset_month} → {current_month}), "
                f"自动重置 Bing API 月度配额"
            )
            self._state.bing_used_count = 0
            self._state.bing_reset_month = current_month
            self._state.last_updated = datetime.now().isoformat()
            self._save_state()

    def _load_state(self) -> QuotaState:
        """从磁盘加载配额状态"""
        if not self._state_file.exists():
            return QuotaState(
                bing_reset_month=datetime.now().strftime("%Y-%m"),
                last_updated=datetime.now().isoformat(),
            )
        try:
            with open(self._state_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            return QuotaState(**data)
        except Exception as e:
            logger.warning(f"配额状态文件损坏，重置为零: {e}")
            return QuotaState(
                bing_reset_month=datetime.now().strftime("%Y-%m"),
                last_updated=datetime.now().isoformat(),
            )

    def _sync_config(self) -> None:
        """从配置文件同步配额上限（配置变更时自动生效）"""
        from config.settings import settings
        cfg = getattr(settings, "datasource", None)
        if cfg:
            self._state.bing_monthly_limit = getattr(cfg, "bing_monthly_quota", 1000)
            total_beans = getattr(cfg, "tianapi_total_beans", 0)
            if total_beans > 0:
                self._state.tianapi_total_beans = total_beans

    def _save_state(self) -> None:
        """持久化配额状态到磁盘"""
        from dataclasses import asdict
        try:
            with open(self._state_file, "w", encoding="utf-8") as f:
                json.dump(asdict(self._state), f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"配额状态持久化失败: {e}")


_quota_manager: Optional[QuotaManager] = None


def get_quota_manager() -> QuotaManager:
    """QuotaManager 单例工厂"""
    global _quota_manager
    if _quota_manager is None:
        _quota_manager = QuotaManager()
    return _quota_manager
