"""发布器基类 - 预留多平台扩展"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class PublishResult:
    """发布结果"""
    success: bool = False
    article_id: str = ""
    platform: str = ""
    published_url: str = ""
    published_at: str = field(default_factory=lambda: datetime.now().isoformat())
    message: str = ""
    error: str = ""


class PublisherBase(ABC):
    """发布器基类"""

    platform: str = "unknown"

    @abstractmethod
    def login(self) -> bool:
        """登录/验证登录状态，返回是否已登录"""
        ...

    @abstractmethod
    def is_logged_in(self) -> bool:
        """检查是否已登录"""
        ...

    @abstractmethod
    def publish_article(
        self,
        title: str,
        content: str,
        image_paths: Optional[list[str]] = None,
        category: str = "",
        **kwargs,
    ) -> PublishResult:
        """发布文章"""
        ...

    @abstractmethod
    def close(self):
        """关闭浏览器/释放资源"""
        ...
