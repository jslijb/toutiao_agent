"""爬虫基类，定义统一接口，包含反爬策略、延迟、日志、Playwright 工具"""
from __future__ import annotations

import abc
import asyncio
import json
import random
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Callable, Generator, Optional, TypeVar

from loguru import logger

from config.settings import PROJECT_ROOT, settings
from crawlers.quality_scorer import score_article
from models.article import ArticleData, ArticleMetrics
from utils.http_client import get_headers

T = TypeVar("T")

# 全局线程池，用于在 asyncio 事件循环中安全运行 Playwright 同步 API
_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="pw")


def _is_running_in_async_loop() -> bool:
    """检测当前是否在 asyncio 事件循环中（如 Gradio WebUI）"""
    try:
        asyncio.get_running_loop()
        return True
    except RuntimeError:
        return False


def run_sync_in_thread(fn: Callable[..., T], *args, **kwargs) -> T:
    """在线程中运行同步函数，避免与 asyncio 事件循环冲突。"""
    if _is_running_in_async_loop():
        future = _executor.submit(fn, *args, **kwargs)
        return future.result(timeout=300)
    else:
        return fn(*args, **kwargs)


# ── 浏览器实例池（按线程复用，避免反复启动） ──────────────
class _BrowserPool:
    """线程安全的 Playwright 浏览器池。

    同一线程内复用同一个浏览器实例（不同页面），
    线程结束时自动关闭。大幅减少浏览器启动/关闭开销。
    """

    def __init__(self):
        self._local = threading.local()
        self._lock = threading.Lock()
        self._instances: list[tuple] = []  # (pw, browser) 用于清理

    def get_page(self, headless: bool = True):
        """获取一个新页面（复用同线程的浏览器实例）"""
        from playwright.sync_api import sync_playwright

        # 检查当前线程是否已有浏览器实例
        pw = getattr(self._local, "pw", None)
        browser = getattr(self._local, "browser", None)

        if browser is None or not browser.is_connected():
            # 首次或浏览器已断开，创建新实例
            if browser is not None:
                try:
                    browser.close()
                except Exception:
                    pass
            if pw is not None:
                try:
                    pw.stop()
                except Exception:
                    pass

            pw = sync_playwright().start()
            browser = pw.chromium.launch(headless=headless)

            with self._lock:
                self._instances.append((pw, browser))

            self._local.pw = pw
            self._local.browser = browser
            self._local.headless = headless
            logger.debug(f"[BrowserPool] 新建浏览器实例 (headless={headless}, thread={threading.current_thread().name})")

        # 创建新的浏览器上下文和页面
        context = browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent=get_headers()["User-Agent"],
            locale="zh-CN",
        )
        page = context.new_page()

        # 抑制 playwright-stealth 的噪音 console 输出
        # ("Error occurred during getting browser(s): random, but was suppressed with fallback.")
        def _on_console(msg):
            text = msg.text
            if "suppressed with fallback" not in text and "getting browser" not in text:
                logger.debug(f"[Browser] console: {text[:200]}")

        page.on("console", _on_console)

        # 注入 stealth
        try:
            from playwright_stealth import stealth_sync
            stealth_sync(page)
        except ImportError:
            pass

        return page, context

    def get_browser(self, headless: bool = True):
        """获取当前线程的浏览器实例（用于创建多个页面/上下文）"""
        from playwright.sync_api import sync_playwright

        pw = getattr(self._local, "pw", None)
        browser = getattr(self._local, "browser", None)

        if browser is None or not browser.is_connected():
            if browser is not None:
                try:
                    browser.close()
                except Exception:
                    pass
            if pw is not None:
                try:
                    pw.stop()
                except Exception:
                    pass

            pw = sync_playwright().start()
            browser = pw.chromium.launch(headless=headless)

            with self._lock:
                self._instances.append((pw, browser))

            self._local.pw = pw
            self._local.browser = browser
            self._local.headless = headless
            logger.debug(f"[BrowserPool] 新建浏览器实例 (headless={headless}, thread={threading.current_thread().name})")

        return browser

    def release_page(self, page, context):
        """释放页面（关闭 context，保留浏览器）"""
        try:
            context.close()
        except Exception:
            pass

    def cleanup(self):
        """清理所有浏览器实例（爬取结束时调用）"""
        with self._lock:
            for pw, browser in self._instances:
                try:
                    browser.close()
                except Exception:
                    pass
                try:
                    pw.stop()
                except Exception:
                    pass
            self._instances.clear()
        logger.debug("[BrowserPool] 已清理所有浏览器实例")


# 全局浏览器池
_browser_pool = _BrowserPool()


def get_browser_page(headless: bool = True):
    """获取 Playwright 浏览器页面（带 stealth），复用浏览器实例。

    用法：
        with get_browser_page() as page:
            page.goto(...)
    """
    page, context = _browser_pool.get_page(headless=headless)

    class _PageCtx:
        def __enter__(self):
            return page

        def __exit__(self, *args):
            _browser_pool.release_page(page, context)

    return _PageCtx()


def get_browser_pool() -> _BrowserPool:
    """获取全局浏览器池实例（子类需要创建多页面时使用）"""
    return _browser_pool


def cleanup_browser_pool():
    """清理浏览器池（爬取全部结束后调用）"""
    _browser_pool.cleanup()


class BaseCrawler(abc.ABC):
    """爬虫基类"""

    platform: str = ""  # 子类必须设置

    def __init__(self):
        self.min_delay = settings.crawler.min_delay
        self.max_delay = settings.crawler.max_delay
        self.max_retries = settings.crawler.max_retries
        self.timeout = settings.crawler.timeout
        self.detail_min_delay = settings.crawler.detail_min_delay
        self.detail_max_delay = settings.crawler.detail_max_delay
        self.detail_max_concurrent = settings.crawler.detail_max_concurrent
        self.raw_dir = PROJECT_ROOT / "data" / "raw" / self.platform
        self.raw_dir.mkdir(parents=True, exist_ok=True)

    def delay(self):
        """随机延迟（搜索间隔）"""
        sleep_time = random.uniform(self.min_delay, self.max_delay)
        logger.debug(f"[{self.platform}] 等待 {sleep_time:.1f}s")
        time.sleep(sleep_time)

    def detail_delay(self):
        """随机延迟（详情页间隔，比搜索间隔短）"""
        sleep_time = random.uniform(self.detail_min_delay, self.detail_max_delay)
        logger.debug(f"[{self.platform}] 详情页等待 {sleep_time:.1f}s")
        time.sleep(sleep_time)

    def get_keywords(self) -> list[str]:
        """获取当前平台的搜索关键词"""
        platform_kw = settings.crawler.platform_keywords.get(self.platform, [])
        if platform_kw:
            return platform_kw
        return settings.crawler.keywords

    def save_raw(self, data: list | dict, filename: str = ""):
        """保存原始爬取数据到 JSON"""
        if not filename:
            filename = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        filepath = self.raw_dir / filename
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        logger.info(f"原始数据已保存: {filepath}")

    @abc.abstractmethod
    def search(self, keyword: str, max_count: int = 50) -> list[ArticleData]:
        """搜索文章，返回 ArticleData 列表"""
        ...

    def crawl(self, keywords: list[str] | None = None, max_count: int | None = None) -> list[ArticleData]:
        """执行爬取（遍历所有关键词），自动检测异步环境并在线程中运行"""
        keywords = keywords or self.get_keywords()
        max_count = max_count or settings.crawler.max_articles_per_platform

        # 在线程中运行整个 crawl 过程，避免 asyncio 冲突
        if _is_running_in_async_loop():
            logger.debug(f"[{self.platform}] 检测到 asyncio 环境，在线程池中运行爬取")
            future = _executor.submit(self._crawl_sync, keywords, max_count)
            return future.result(timeout=600)
        else:
            return self._crawl_sync(keywords, max_count)

    def _crawl_sync(self, keywords: list[str], max_count: int) -> list[ArticleData]:
        """同步爬取实现：搜索 → 去重 → 时效过滤 → 详情页批量抓取 → 存储"""
        from models.article_store import get_article_store

        store = get_article_store()
        all_articles: list[ArticleData] = []
        seen_urls = set()

        for keyword in keywords:
            logger.info(f"[{self.platform}] 搜索关键词: {keyword}")
            try:
                articles = self.search(keyword, max_count=max_count)
                new_count = 0
                for art in articles:
                    if art.url and art.url not in seen_urls:
                        seen_urls.add(art.url)
                        # 跳过已存在的文章（去重）
                        if store.is_duplicate(art):
                            logger.debug(f"[{self.platform}] 跳过重复文章: {art.title[:30]}")
                            continue
                        all_articles.append(art)
                        new_count += 1
                logger.info(f"[{self.platform}] 关键词 '{keyword}': 搜索 {len(articles)} 篇, 新增 {new_count} 篇")
                self.delay()
            except Exception as e:
                logger.error(f"[{self.platform}] 搜索 '{keyword}' 失败: {e}")

        # ── 时效过滤：丢弃发布时间过旧的文章 ──
        max_age_days = getattr(settings.crawler, 'max_article_age_days', 0)
        if max_age_days > 0:
            before_filter = len(all_articles)
            all_articles = self._filter_by_age(all_articles, max_age_days)
            filtered = before_filter - len(all_articles)
            if filtered > 0:
                logger.info(f"[{self.platform}] 时效过滤: 移除 {filtered} 篇超过 {max_age_days} 天的旧文章")

        # 详情页批量抓取：HTTP优先 + 并发标签页 + 页面复用
        need_fetch = [a for a in all_articles if len(a.content) < 200]
        if need_fetch:
            self._fetch_details_batch(need_fetch)

        # 设置 TTL
        ttl_days = getattr(settings.crawler, 'article_ttl_days', 30)
        for art in all_articles:
            art.ttl_days = ttl_days

        # 持久化到 ArticleStore
        added = store.add_many(all_articles)
        logger.info(f"[{self.platform}] 共爬取 {len(all_articles)} 篇, 新增 {added} 篇（去重后）")

        # 按 quality_score × 时间衰减因子 排序（新文章获得加权）
        all_articles.sort(key=lambda x: x.quality_score * self._time_decay(x.publish_time), reverse=True)
        return all_articles

    @staticmethod
    def _filter_by_age(articles: list[ArticleData], max_age_days: int) -> list[ArticleData]:
        """过滤发布时间过旧的文章

        支持多种日期格式：ISO格式、Unix时间戳、中文日期（如"2023年5月1日"）、
        相对时间（如"3天前"、"2小时前"）。
        """
        from datetime import datetime, timedelta
        import re

        cutoff = datetime.now() - timedelta(days=max_age_days)
        result = []

        for art in articles:
            pt = art.publish_time.strip() if art.publish_time else ""
            if not pt:
                # 无发布时间的文章，默认保留（可能是搜索页未提取到时间）
                result.append(art)
                continue

            pub_dt = None

            # 1. ISO 格式 (2026-04-13T10:00:00 / 2026-04-13 10:00:00)
            try:
                pub_dt = datetime.fromisoformat(pt.replace("Z", "+00:00").replace(" ", "T"))
            except (ValueError, TypeError):
                pass

            # 2. Unix 时间戳 (秒)
            if pub_dt is None and pt.isdigit() and len(pt) >= 10:
                try:
                    pub_dt = datetime.fromtimestamp(int(pt[:10]))
                except (ValueError, OSError):
                    pass

            # 3. 中文日期 (2023年5月1日 / 2023-05-01)
            if pub_dt is None:
                m = re.match(r'(\d{4})[年\-](\d{1,2})[月\-](\d{1,2})', pt)
                if m:
                    try:
                        pub_dt = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
                    except ValueError:
                        pass

            # 4. 相对时间 (3天前 / 2小时前 / 昨天)
            if pub_dt is None:
                rel_match = re.match(r'(\d+)\s*(天|小时|分钟|周|月|年)前', pt)
                if rel_match:
                    try:
                        num = int(rel_match.group(1))
                        unit = rel_match.group(2)
                        if unit == "分钟":
                            pub_dt = datetime.now() - timedelta(minutes=num)
                        elif unit == "小时":
                            pub_dt = datetime.now() - timedelta(hours=num)
                        elif unit == "天":
                            pub_dt = datetime.now() - timedelta(days=num)
                        elif unit == "周":
                            pub_dt = datetime.now() - timedelta(weeks=num)
                        elif unit == "月":
                            pub_dt = datetime.now() - timedelta(days=num * 30)
                        elif unit == "年":
                            pub_dt = datetime.now() - timedelta(days=num * 365)
                    except (ValueError, TypeError):
                        pass
                elif "昨天" in pt:
                    pub_dt = datetime.now() - timedelta(days=1)
                elif "前天" in pt:
                    pub_dt = datetime.now() - timedelta(days=2)
                elif "刚刚" in pt or "刚刚" in pt:
                    pub_dt = datetime.now()

            if pub_dt is None:
                # 无法解析时间的文章，默认保留
                result.append(art)
                continue

            if pub_dt >= cutoff:
                result.append(art)
            else:
                logger.debug(f"[过滤] 旧文章: '{art.title[:30]}' 发布于 {pt}")

        return result

    @staticmethod
    def _time_decay(publish_time: str, half_life_days: float = 180) -> float:
        """时间衰减因子：新文章得分更高

        使用指数衰减：decay = 2^(-age_days / half_life_days)
        half_life_days=180 意味着180天前的文章质量分减半
        """
        from datetime import datetime
        import re

        if not publish_time or not publish_time.strip():
            return 0.8  # 无时间的文章给予中等权重

        pt = publish_time.strip()
        pub_dt = None

        try:
            pub_dt = datetime.fromisoformat(pt.replace("Z", "+00:00").replace(" ", "T"))
        except (ValueError, TypeError):
            pass

        if pub_dt is None and pt.isdigit() and len(pt) >= 10:
            try:
                pub_dt = datetime.fromtimestamp(int(pt[:10]))
            except (ValueError, OSError):
                pass

        if pub_dt is None:
            m = re.match(r'(\d{4})[年\-](\d{1,2})[月\-](\d{1,2})', pt)
            if m:
                try:
                    pub_dt = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
                except ValueError:
                    pass

        if pub_dt is None:
            return 0.8

        age_days = (datetime.now() - pub_dt).total_seconds() / 86400
        return 2.0 ** (-age_days / half_life_days)

    # ── 详情页批量抓取（优化核心） ──────────────────────────────

    def _fetch_details_batch(self, articles: list[ArticleData]):
        """批量抓取详情页：HTTP优先 → 页面复用+并发标签页

        优化策略：
        1. 先尝试 HTTP 批量抓取（无浏览器开销）
        2. HTTP 失败的文章用 Playwright 并发标签页抓取
        3. fetch_detail_from_page 合并 content+metrics，一次访问完成
        4. 详情页间延迟更短（detail_delay）
        """
        logger.info(f"[{self.platform}] 开始批量抓取 {len(articles)} 篇文章详情页...")

        # 阶段1: HTTP 批量抓取（无浏览器开销，极快）
        http_failed: list[ArticleData] = []
        for art in articles:
            try:
                http_content = self.fetch_content_http(art.url)
                if http_content and len(http_content) > len(art.content):
                    art.content = http_content
                # HTTP 成功也尝试提取 metrics（部分平台可在 HTTP 中获取）
                if len(art.content) >= 200:
                    # 内容已够，但还需要 metrics
                    pass
                else:
                    http_failed.append(art)
            except Exception:
                http_failed.append(art)

        # 统计 HTTP 成功率
        http_ok = len(articles) - len(http_failed)
        if http_ok > 0:
            logger.info(f"[{self.platform}] HTTP 详情页: {http_ok}/{len(articles)} 成功")

        # 还需要 Playwright 抓取的文章 = HTTP失败的 + HTTP成功但缺metrics的
        # 统一用 Playwright 补齐 content + metrics
        need_pw = [a for a in articles if len(a.content) < 200 or not (a.metrics.likes or a.metrics.comments or a.metrics.views)]
        if not need_pw:
            logger.info(f"[{self.platform}] 所有文章 HTTP 抓取完成，无需 Playwright")
            return

        logger.info(f"[{self.platform}] {len(need_pw)} 篇需要 Playwright 抓取（内容或互动数据）")

        # 阶段2: Playwright 并发标签页抓取
        try:
            self._fetch_via_playwright_batch(need_pw)
        except Exception as e:
            logger.error(f"[{self.platform}] Playwright 批量抓取失败: {e}")

    def _fetch_via_playwright_batch(self, articles: list[ArticleData]):
        """用 Playwright 并发标签页批量抓取详情页

        在一个浏览器上下文中，同时打开多个标签页，
        每个标签页独立导航，实现并发抓取。
        """
        browser = get_browser_pool().get_browser(headless=True)

        # 设置子类的页面初始化（如注入 Cookie）
        setup_fn = self.setup_page  # 子类可覆盖

        # 按并发数分组
        concurrent = self.detail_max_concurrent
        total = len(articles)
        fetched = 0

        for batch_start in range(0, total, concurrent):
            batch = articles[batch_start:batch_start + concurrent]

            # 为每篇文章创建一个独立的 context + page
            pages_info = []
            for art in batch:
                try:
                    context = browser.new_context(
                        viewport={"width": 1920, "height": 1080},
                        user_agent=get_headers()["User-Agent"],
                        locale="zh-CN",
                    )
                    page = context.new_page()

                    # 抑制 stealth 噪音
                    def _on_console(msg):
                        text = msg.text
                        if "suppressed with fallback" not in text and "getting browser" not in text:
                            logger.debug(f"[Browser] console: {text[:200]}")
                    page.on("console", _on_console)

                    # 注入 stealth
                    try:
                        from playwright_stealth import stealth_sync
                        stealth_sync(page)
                    except ImportError:
                        pass

                    # 子类初始化（如注入 Cookie）
                    setup_fn(page)

                    # 并发导航（不等待加载完成）
                    try:
                        page.goto(art.url, timeout=30000, wait_until="domcontentloaded")
                    except Exception as e:
                        logger.debug(f"[{self.platform}] 导航失败: {art.title[:20]} - {e}")

                    pages_info.append((art, page, context))
                except Exception as e:
                    logger.debug(f"[{self.platform}] 创建页面失败: {art.title[:20]} - {e}")

            # 等待所有页面加载完成，然后提取内容
            for art, page, context in pages_info:
                try:
                    # 合并提取 content + metrics（一次页面访问）
                    content, metrics = self.fetch_detail_from_page(page, art.url)

                    if content and len(content) > len(art.content):
                        art.content = content

                    # 合并 metrics
                    if metrics:
                        if metrics.likes is not None:
                            art.metrics.likes = metrics.likes
                        if metrics.comments is not None:
                            art.metrics.comments = metrics.comments
                        if metrics.views is not None:
                            art.metrics.views = metrics.views
                        if metrics.favorites is not None:
                            art.metrics.favorites = metrics.favorites
                        art.quality_score = score_article(art)

                    fetched += 1
                    if fetched % 10 == 0:
                        logger.info(f"[{self.platform}] 详情页进度: {fetched}/{total}")

                except Exception as e:
                    logger.debug(f"[{self.platform}] 详情页提取失败: {art.title[:20]} - {e}")
                finally:
                    try:
                        context.close()
                    except Exception:
                        pass

            # 批次间延迟
            if batch_start + concurrent < total:
                self.detail_delay()

        logger.info(f"[{self.platform}] Playwright 详情页抓取完成: {fetched}/{total}")

    # ── 子类可覆盖的钩子方法 ──────────────────────────────────

    def setup_page(self, page):
        """页面初始化钩子（如注入 Cookie）。子类可覆盖。"""
        pass

    def fetch_content_http(self, url: str) -> str:
        """HTTP 方式抓取详情页全文（无浏览器开销）。子类可覆盖。

        默认实现尝试通用 HTTP 提取，失败返回空串。
        """
        if not url:
            return ""
        try:
            from rag.cleaner import TextCleaner
            headers = get_headers({"Accept": "text/html,application/xhtml+xml"})
            from utils.http_client import http_get
            resp = http_get(url, headers=headers, timeout=15)
            if resp.status_code == 200 and len(resp.text) > 200:
                return TextCleaner.extract_main_content(resp.text)
        except Exception:
            pass
        return ""

    def fetch_detail_from_page(self, page, url: str) -> tuple[str, ArticleMetrics | None]:
        """从已加载的 Playwright 页面提取内容+互动数据（一次访问完成）。

        子类应覆盖此方法以适配特定平台 DOM。
        默认实现使用通用选择器提取。

        Returns:
            (content, metrics) 元组。metrics 为 None 表示未提取到。
        """
        content = ""
        metrics = None

        try:
            # 等待核心内容加载
            try:
                page.wait_for_selector(
                    "article, div[class*='content'], div[class*='article'], div.RichText, div#article",
                    timeout=8000,
                )
            except Exception:
                try:
                    page.wait_for_load_state("networkidle", timeout=5000)
                except Exception:
                    pass

            # 提取正文
            from rag.cleaner import TextCleaner
            html = page.content()
            content = TextCleaner.extract_main_content(html)

            # 尝试提取 metrics（默认空，子类覆盖）
            metrics = self.extract_metrics_from_page(page)

        except Exception as e:
            logger.debug(f"[{self.platform}] 页面提取失败: {e}")

        return content, metrics

    def extract_metrics_from_page(self, page) -> ArticleMetrics | None:
        """从已加载的 Playwright 页面提取互动数据。

        子类可覆盖以适配特定平台 DOM。
        默认返回 None（不提取）。
        """
        return None

    # ── 旧接口保留（兼容，不再由 _crawl_sync 调用） ──────────

    def fetch_content(self, url: str) -> str:
        """抓取文章详情页全文。子类可覆盖以适配特定平台 DOM 结构。

        注意：此方法仍可用于单独抓取，但 _crawl_sync 已改用
        fetch_content_http + fetch_detail_from_page 的批量优化流程。
        """
        if not url:
            return ""

        try:
            from rag.cleaner import TextCleaner

            with get_browser_page(headless=True) as page:
                page.goto(url, timeout=30000, wait_until="domcontentloaded")

                # 等待页面核心内容加载（而非 networkidle，避免被反爬卡住）
                try:
                    page.wait_for_selector(
                        "article, div[class*='content'], div[class*='article'], div.RichText, div#article",
                        timeout=10000,
                    )
                except Exception:
                    # 降级等 networkidle
                    try:
                        page.wait_for_load_state("networkidle", timeout=8000)
                    except Exception:
                        pass

                html = page.content()
                content = TextCleaner.extract_main_content(html)
                return content
        except Exception as e:
            logger.debug(f"[{self.platform}] 详情页抓取失败: {e}")
            return ""

    def fetch_metrics(self, url: str) -> ArticleMetrics:
        """从详情页提取互动数据。子类可覆盖以适配特定平台。

        注意：旧接口，新流程使用 fetch_detail_from_page 合并提取。
        """
        return ArticleMetrics()

    def _fetch_and_update_metrics(self, article: ArticleData):
        """提取互动数据并重算质量评分

        注意：旧接口，新流程已在 fetch_detail_from_page 中合并处理。
        """
        if not article.url:
            return
        try:
            metrics = self.fetch_metrics(article.url)
            if metrics.likes or metrics.comments or metrics.views or metrics.favorites:
                # 合并到已有 metrics（保留非空值）
                if metrics.likes is not None:
                    article.metrics.likes = metrics.likes
                if metrics.comments is not None:
                    article.metrics.comments = metrics.comments
                if metrics.views is not None:
                    article.metrics.views = metrics.views
                if metrics.favorites is not None:
                    article.metrics.favorites = metrics.favorites
                # 重算评分
                article.quality_score = score_article(article)
        except Exception as e:
            logger.debug(f"[{self.platform}] 互动数据提取失败: {e}")
