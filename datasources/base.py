"""数据源适配器抽象基类"""
from __future__ import annotations

from abc import ABC, abstractmethod

from models.article import ArticleData
from datasources.models import SourceHealth


class BaseSourceAdapter(ABC):
    """数据源适配器抽象基类，定义统一接口"""

    @property
    @abstractmethod
    def name(self) -> str:
        """数据源标识名称"""

    @abstractmethod
    def fetch(self, **kwargs) -> list[ArticleData]:
        """获取数据，返回 ArticleData 列表"""

    @abstractmethod
    def health_check(self) -> SourceHealth:
        """检查数据源健康状态"""

    def is_available(self) -> bool:
        """检查数据源是否可用"""
        return self.health_check().status.value == "available"
