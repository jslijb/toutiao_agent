"""全局配置管理，从系统环境变量加载密钥，从 models.yaml 加载模型配置"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional

import yaml


# 项目根目录
PROJECT_ROOT = Path(__file__).resolve().parent.parent


class _EnvSettings:
    """从系统环境变量加载密钥类配置（不再依赖 .env 文件）"""

    def __init__(self):
        self.dashscope_api_key: str = os.environ.get("DASHSCOPE_API_KEY", "")
        self.http_proxy: str = os.environ.get("HTTP_PROXY", "")
        self.https_proxy: str = os.environ.get("HTTPS_PROXY", "")


# 全局单例
env_settings = _EnvSettings()


def _load_yaml(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


# ── 模型配置 ──────────────────────────────────────────
class LLMConfig:
    def __init__(self, data: dict):
        self.name: str = data.get("name", "qwen3-max-2026-01-23")
        self.api_base: str = data.get("api_base", "https://dashscope.aliyuncs.com/compatible-mode/v1")
        self.max_tokens: int = data.get("max_tokens", 8192)
        self.temperature: float = data.get("temperature", 0.7)
        self.top_p: float = data.get("top_p", 0.9)


class EmbeddingConfig:
    def __init__(self, data: dict):
        self.name: str = data.get("name", "text-embedding-v3")
        self.dimension: int = data.get("dimension", 1024)
        self.batch_size: int = data.get("batch_size", 25)
        self.batch_interval: float = data.get("batch_interval", 1.0)


class ImageGenConfig:
    def __init__(self, data: dict):
        self.name: str = data.get("name", "wanx2.1-t2i-turbo")
        self.style: str = data.get("style", "3D卡通")
        self.size: str = data.get("size", "1024*1024")
        self.n: int = data.get("n", 4)
        self.async_mode: bool = data.get("async_mode", True)
        self.poll_interval: int = data.get("poll_interval", 5)
        self.max_poll_times: int = data.get("max_poll_times", 60)


class ModelsConfig:
    """模型配置容器，从 models.yaml 读取"""

    def __init__(self, data: dict):
        models = data.get("models", {})
        self.llm = LLMConfig(models.get("llm", {}))
        self.embedding = EmbeddingConfig(models.get("embedding", {}))
        self.image_gen = ImageGenConfig(models.get("image_gen", {}))


# ── 爬虫配置 ──────────────────────────────────────────
class CrawlerConfig:
    def __init__(self, data: dict):
        crawler = data.get("crawler", {})
        self.min_delay: int = crawler.get("min_delay", 5)
        self.max_delay: int = crawler.get("max_delay", 10)
        self.detail_min_delay: float = crawler.get("detail_min_delay", 1.0)
        self.detail_max_delay: float = crawler.get("detail_max_delay", 2.0)
        self.detail_max_concurrent: int = crawler.get("detail_max_concurrent", 3)
        self.max_articles_per_platform: int = crawler.get("max_articles_per_platform", 50)
        self.max_concurrent: int = crawler.get("max_concurrent", 1)
        self.max_retries: int = crawler.get("max_retries", 3)
        self.timeout: int = crawler.get("timeout", 30)
        self.keywords: list[str] = crawler.get("keywords", ["职场", "副业", "个人成长"])
        self.platform_keywords: dict = crawler.get("platform_keywords", {})
        self.article_ttl_days: int = crawler.get("article_ttl_days", 30)  # 文章保留天数
        self.fetch_detail: bool = crawler.get("fetch_detail", True)  # 是否抓取详情页全文
        self.max_article_age_days: int = crawler.get("max_article_age_days", 365)  # 文章最大天数（超过则过滤）


# ── 生成配置 ──────────────────────────────────────────
class GenerationConfig:
    def __init__(self, data: dict):
        gen = data.get("generation", {})
        self.target_word_count: int = gen.get("target_word_count", 1000)
        self.min_word_count: int = gen.get("min_word_count", 900)
        self.max_word_count: int = gen.get("max_word_count", 1100)
        self.rag_top_k: int = gen.get("rag_top_k", 10)
        self.title_candidates: int = gen.get("title_candidates", 5)
        self.sensitive_words_file: str = gen.get("sensitive_words_file", "")


# ── 热点配置 ──────────────────────────────────────────
class HotTopicsConfig:
    def __init__(self, data: dict):
        ht = data.get("hot_topics", {})
        self.sources: list[str] = ht.get("sources", ["toutiao", "weibo", "baidu"])
        self.max_topics: int = ht.get("max_topics", 15)
        self.use_llm_filter: bool = ht.get("use_llm_filter", True)


# ── 发布器配置 ────────────────────────────────────────
class PublisherConfig:
    def __init__(self, data: dict):
        pub = data.get("publisher", {})
        self.auto_publish: bool = pub.get("auto_publish", False)
        self.headless: bool = pub.get("headless", True)
        self.publish_type: str = pub.get("publish_type", "article")
        self.default_category: str = pub.get("default_category", "职场")
        self.default_location: str = pub.get("default_location", "")


# ── 去重配置 ──────────────────────────────────────────
class DedupConfig:
    def __init__(self, data: dict):
        dedup = data.get("dedup", {})
        self.title_threshold: float = dedup.get("title_threshold", 0.6)
        self.content_hamming_threshold: int = dedup.get("content_hamming_threshold", 3)
        self.min_content_length: int = dedup.get("min_content_length", 50)
        self.enable_semantic_dedup: bool = dedup.get("enable_semantic_dedup", True)
        self.check_rag_duplicates: bool = dedup.get("check_rag_duplicates", True)


# ── WebUI 配置 ────────────────────────────────────────
class WebUIConfig:
    def __init__(self, data: dict):
        ui = data.get("webui", {})
        self.host: str = ui.get("host", "127.0.0.1")
        self.port: int = ui.get("port", 7860)
        self.share: bool = ui.get("share", False)


# ── 全局配置单例 ──────────────────────────────────────
class Settings:
    """全局配置，统一入口"""

    def __init__(self):
        yaml_path = PROJECT_ROOT / "config" / "models.yaml"
        raw = _load_yaml(yaml_path) if yaml_path.exists() else {}

        self.models = ModelsConfig(raw)
        self.crawler = CrawlerConfig(raw)
        self.generation = GenerationConfig(raw)
        self.hot_topics = HotTopicsConfig(raw)
        self.publisher = PublisherConfig(raw)
        self.webui = WebUIConfig(raw)
        self.dedup = DedupConfig(raw)

    @property
    def dashscope_api_key(self) -> str:
        return env_settings.dashscope_api_key

    @property
    def http_proxy(self) -> str:
        return env_settings.http_proxy

    @property
    def https_proxy(self) -> str:
        return env_settings.https_proxy

    def reload(self):
        """重新加载配置（修改 models.yaml 后调用）"""
        yaml_path = PROJECT_ROOT / "config" / "models.yaml"
        raw = _load_yaml(yaml_path) if yaml_path.exists() else {}
        self.models = ModelsConfig(raw)
        self.crawler = CrawlerConfig(raw)
        self.generation = GenerationConfig(raw)
        self.hot_topics = HotTopicsConfig(raw)
        self.publisher = PublisherConfig(raw)
        self.webui = WebUIConfig(raw)
        self.dedup = DedupConfig(raw)


# 全局单例
settings = Settings()
