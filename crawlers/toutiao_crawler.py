"""今日头条爬虫（自动获取 ttwid Cookie + API搜索 + HTTP优先 + 合并详情页访问）"""
from __future__ import annotations

import re
import json
from typing import Optional
from urllib.parse import quote

from bs4 import BeautifulSoup
from loguru import logger

from crawlers.base import BaseCrawler, get_browser_page
from crawlers.quality_scorer import score_article
from models.article import ArticleData, ArticleMetrics
from utils.http_client import http_get, get_headers
from utils.cookie_manager import cookie_manager


class ToutiaoCrawler(BaseCrawler):
    platform = "toutiao"

    SEARCH_URL = "https://www.toutiao.com/search/"
    API_URL = "https://www.toutiao.com/api/search/content/"

    # ── HTTP 优先抓取详情页 ──────────────────────────────────

    def fetch_content_http(self, url: str) -> str:
        """HTTP 方式抓取头条文章全文（无浏览器开销，极快）"""
        if not url:
            return ""
        try:
            from rag.cleaner import TextCleaner
            headers = get_headers({
                "Referer": "https://www.toutiao.com/",
                "Accept": "text/html,application/xhtml+xml",
            })
            resp = http_get(url, headers=headers, timeout=15)
            if resp.status_code == 200:
                return TextCleaner.extract_main_content(resp.text)
        except Exception as e:
            logger.debug(f"[头条] HTTP 抓取失败: {e}")
        return ""

    # ── 合并详情页访问（内容+互动数据一次完成） ────────────────

    def fetch_detail_from_page(self, page, url: str) -> tuple[str, ArticleMetrics | None]:
        """从已加载的头条详情页同时提取正文+互动数据（一次页面访问）"""
        content = ""
        metrics = ArticleMetrics()

        try:
            # 等待内容加载
            try:
                page.wait_for_selector(
                    "article, div.article-content, div[class*='content'], div.detail-like",
                    timeout=10000,
                )
            except Exception:
                try:
                    page.wait_for_load_state("networkidle", timeout=5000)
                except Exception:
                    pass

            # 提取正文
            from rag.cleaner import TextCleaner
            for sel in ["article", "div.article-content", "div[class*='articleContent']"]:
                el = page.query_selector(sel)
                if el:
                    text = el.inner_text()
                    if len(text) > 100:
                        content = TextCleaner.clean(text)
                        break

            if not content:
                html = page.content()
                content = TextCleaner.extract_main_content(html)

            # 提取互动数据（点赞、评论）— 与正文在同一页面
            try:
                like_el = page.query_selector("div.detail-like")
                if like_el:
                    like_text = like_el.inner_text().strip()
                    nums = re.findall(r'\d+', like_text.replace(',', '').replace('万', '0000'))
                    if nums:
                        metrics.likes = int(nums[0])
            except Exception:
                pass

            try:
                comment_el = page.query_selector("div.detail-interaction-comment")
                if comment_el:
                    comment_text = comment_el.inner_text().strip()
                    nums = re.findall(r'\d+', comment_text.replace(',', '').replace('万', '0000'))
                    if nums:
                        metrics.comments = int(nums[0])
            except Exception:
                pass

            if metrics.likes or metrics.comments:
                logger.debug(f"[头条] 互动数据: likes={metrics.likes}, comments={metrics.comments}")

        except Exception as e:
            logger.debug(f"[头条] 详情页合并提取失败: {e}")

        return content, metrics if (metrics.likes or metrics.comments) else None

    # ── 旧接口保留（兼容） ────────────────────────────────────

    def fetch_content(self, url: str) -> str:
        """头条详情页抓取 — 优先 HTTP，降级 Playwright"""
        if not url:
            return ""

        from rag.cleaner import TextCleaner

        # 优先用 HTTP 请求
        http_content = self.fetch_content_http(url)
        if http_content and len(http_content) > 100:
            return http_content

        # 降级到 Playwright
        try:
            with get_browser_page(headless=True) as page:
                page.goto(url, timeout=30000, wait_until="domcontentloaded")

                try:
                    page.wait_for_selector(
                        "article, div.article-content, div[class*='content']",
                        timeout=10000,
                    )
                except Exception:
                    try:
                        page.wait_for_load_state("networkidle", timeout=8000)
                    except Exception:
                        pass

                for sel in ["article", "div.article-content", "div[class*='articleContent']"]:
                    el = page.query_selector(sel)
                    if el:
                        text = el.inner_text()
                        if len(text) > 100:
                            return TextCleaner.clean(text)

                html = page.content()
                return TextCleaner.extract_main_content(html)

        except Exception as e:
            logger.debug(f"[头条] 详情页抓取失败: {e}")
            return ""

    def fetch_metrics(self, url: str) -> ArticleMetrics:
        """从头条详情页提取互动数据（点赞、评论）

        注意：旧接口，新流程已在 fetch_detail_from_page 中合并处理。
        """
        metrics = ArticleMetrics()
        if not url:
            return metrics

        try:
            with get_browser_page(headless=True) as page:
                page.goto(url, timeout=30000, wait_until="domcontentloaded")
                try:
                    page.wait_for_selector(
                        "div.detail-like, div.detail-interaction",
                        timeout=8000,
                    )
                except Exception:
                    try:
                        page.wait_for_load_state("networkidle", timeout=5000)
                    except Exception:
                        pass

                # 点赞数
                try:
                    like_el = page.query_selector("div.detail-like")
                    if like_el:
                        like_text = like_el.inner_text().strip()
                        nums = re.findall(r'\d+', like_text.replace(',', '').replace('万', '0000'))
                        if nums:
                            metrics.likes = int(nums[0])
                except Exception:
                    pass

                # 评论数
                try:
                    comment_el = page.query_selector("div.detail-interaction-comment")
                    if comment_el:
                        comment_text = comment_el.inner_text().strip()
                        nums = re.findall(r'\d+', comment_text.replace(',', '').replace('万', '0000'))
                        if nums:
                            metrics.comments = int(nums[0])
                except Exception:
                    pass

                if metrics.likes or metrics.comments:
                    logger.debug(f"[头条] 互动数据: likes={metrics.likes}, comments={metrics.comments}")

        except Exception as e:
            logger.debug(f"[头条] 互动数据提取失败: {e}")

        return metrics

    # ── 搜索接口 ──────────────────────────────────────────────

    def search(self, keyword: str, max_count: int = 50) -> list[ArticleData]:
        """搜索文章列表，优先用 API，失败则用 Playwright 渲染"""
        # 确保有 ttwid Cookie
        ttwid = self._ensure_ttwid()
        if not ttwid:
            logger.warning("[头条] 无法获取 ttwid Cookie，回退到 Playwright 模式")

        # 尝试 API 方式
        articles = self._search_via_api(keyword, max_count, ttwid)

        # API 拿到数据就用 API 结果，否则回退 Playwright
        if not articles:
            logger.info("[头条] API 无数据，使用 Playwright 渲染搜索...")
            articles = self._search_via_playwright(keyword, max_count)

        return articles[:max_count]

    def _ensure_ttwid(self) -> str | None:
        """确保有 ttwid Cookie，从已有 Cookie 中查找或自动获取"""
        # 先从已保存的 Cookie 中查找
        cookies = cookie_manager.load_cookies(self.platform)
        if cookies:
            for c in cookies:
                if c.get("name") == "ttwid":
                    return c["value"]

        # 自动获取 ttwid
        return self._fetch_ttwid()

    def _fetch_ttwid(self) -> str | None:
        """通过访问头条首页自动获取 ttwid Cookie"""
        try:
            logger.info("[头条] 正在获取 ttwid Cookie...")
            with get_browser_page(headless=True) as page:
                page.goto("https://www.toutiao.com/", timeout=30000)
                page.wait_for_load_state("networkidle", timeout=15000)

                # 从浏览器 Cookie 中提取 ttwid
                browser_cookies = page.context.cookies()
                for cookie in browser_cookies:
                    if cookie.get("name") == "ttwid":
                        ttwid = cookie["value"]
                        logger.info(f"[头条] 成功获取 ttwid Cookie: {ttwid[:20]}...")

                        # 持久化保存
                        all_cookies = cookie_manager.load_cookies(self.platform) or []
                        all_cookies = [c for c in all_cookies if c.get("name") != "ttwid"]
                        all_cookies.append({
                            "name": "ttwid",
                            "value": ttwid,
                            "domain": ".toutiao.com",
                            "path": "/",
                        })
                        cookie_manager.save_cookies(self.platform, all_cookies)
                        return ttwid

            logger.warning("[头条] 未能获取 ttwid Cookie")
            return None
        except Exception as e:
            logger.warning(f"[头条] 获取 ttwid 失败: {e}")
            return None

    def _search_via_api(self, keyword: str, max_count: int, ttwid: str | None) -> list[ArticleData]:
        """通过头条搜索 API 获取文章列表（按时间排序，优先最新）"""
        from datetime import datetime, timedelta

        articles = []
        offset = 0
        seen_urls = set()

        # 计算一年前的时间戳（头条搜索只取最近1年）
        one_year_ago = int((datetime.now() - timedelta(days=365)).timestamp())

        while len(articles) < max_count:
            try:
                params = {
                    "keyword": keyword,
                    "autoload": "true",
                    "count": 20,
                    "offset": offset,
                    "cur_tab": 1,
                    "from": "search_tab",
                    "pd": "information",  # 资讯类型
                    "min_behot_time": one_year_ago,  # 最早发布时间
                }
                headers = get_headers({
                    "Referer": f"https://www.toutiao.com/search/?keyword={quote(keyword)}",
                })
                if ttwid:
                    headers["Cookie"] = f"ttwid={ttwid}"

                resp = http_get(self.API_URL, params=params, headers=headers)
                data = resp.json()

                if not data or data.get("return_count", 0) == 0:
                    break

                items = data.get("data") or []
                if not items:
                    break

                for item in items:
                    if not item:
                        continue
                    art_url = item.get("url", "") or item.get("article_url", "")
                    if art_url in seen_urls or "video" in art_url:
                        continue
                    seen_urls.add(art_url)

                    article = ArticleData(
                        source=self.platform,
                        title=item.get("title", ""),
                        content=item.get("abstract", "") or item.get("digest", ""),
                        url=art_url,
                        author=item.get("source", "") or item.get("author", ""),
                        publish_time=item.get("publish_time", ""),
                    )
                    articles.append(article)
                    if len(articles) >= max_count:
                        break

                offset += 20
                self.delay()

            except Exception as e:
                logger.warning(f"[头条] API 搜索失败(offset={offset}): {e}")
                break

        if articles:
            logger.info(f"[头条] API 搜索 '{keyword}' 完成，获取 {len(articles)} 篇")
        return articles

    def _search_via_playwright(self, keyword: str, max_count: int) -> list[ArticleData]:
        """通过 Playwright 渲染头条搜索页获取文章列表"""
        articles = []
        seen_urls = set()

        try:
            with get_browser_page(headless=True) as page:
                search_url = f"{self.SEARCH_URL}?keyword={quote(keyword)}"
                page.goto(search_url, timeout=45000, wait_until="domcontentloaded")
                page.wait_for_load_state("networkidle", timeout=30000)

                # 滚动加载更多
                for _ in range(3):
                    if len(articles) >= max_count:
                        break
                    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    page.wait_for_timeout(2000)

                # 提取搜索结果 - 只取跳转到 toutiao.com 的文章链接
                all_links = page.query_selector_all("a[href*='sou.toutiao.com/search/jump']")
                for link_el in all_links:
                    try:
                        raw_url = link_el.get_attribute("href") or ""
                        title = link_el.inner_text().strip()

                        if not title or len(title) < 8:
                            continue

                        # 解码 jump URL，只保留目标是 toutiao.com 的
                        from urllib.parse import parse_qs, unquote, urlparse
                        parsed = urlparse(raw_url)
                        target = parse_qs(parsed.query).get("url", [""])[0]
                        if not target:
                            continue
                        # 只要 toutiao.com 的文章或热点新闻
                        if "toutiao.com/article/" not in target and "toutiao.com/trending/" not in target:
                            continue

                        if target in seen_urls:
                            continue
                        seen_urls.add(target)

                        # 摘要：尝试找父容器内的文本
                        summary = ""
                        try:
                            parent = link_el.evaluate_handle("el => el.closest('div.cs-view') || el.parentElement")
                            s_el = parent.query_selector("div[class*='text'], span[class*='desc']")
                            if s_el:
                                summary = s_el.inner_text().strip()
                        except Exception:
                            pass

                        article = ArticleData(
                            source=self.platform,
                            title=title,
                            content=summary[:200],
                            url=target,
                        )
                        articles.append(article)

                        if len(articles) >= max_count:
                            break

                    except Exception:
                        continue

        except Exception as e:
            logger.warning(f"[头条] Playwright 搜索失败: {e}")

        if articles:
            logger.info(f"[头条] Playwright 搜索 '{keyword}' 完成，获取 {len(articles)} 篇")
        return articles[:max_count]
