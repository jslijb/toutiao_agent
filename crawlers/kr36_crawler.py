"""36氪爬虫（HTTP优先 + Playwright搜索 + 合并详情页访问）"""
from __future__ import annotations

import re
from urllib.parse import quote

from loguru import logger

from crawlers.base import BaseCrawler, get_browser_page
from crawlers.quality_scorer import score_article, normalize_scores
from models.article import ArticleData, ArticleMetrics
from utils.http_client import http_get, get_headers
from utils.text_utils import clean_html


class Kr36Crawler(BaseCrawler):
    platform = "kr36"

    # ── HTTP 优先抓取详情页 ──────────────────────────────────

    def fetch_content_http(self, url: str) -> str:
        """HTTP 方式抓取36氪文章全文（36氪静态内容多，HTTP 成功率高）"""
        if not url:
            return ""
        try:
            from rag.cleaner import TextCleaner
            headers = get_headers({
                "Referer": "https://36kr.com/",
                "Accept": "text/html,application/xhtml+xml",
            })
            resp = http_get(url, headers=headers, timeout=15)
            if resp.status_code == 200:
                return TextCleaner.extract_main_content(resp.text)
        except Exception as e:
            logger.debug(f"[36氪] HTTP 抓取失败: {e}")
        return ""

    # ── 合并详情页访问 ────────────────────────────────────────

    def fetch_detail_from_page(self, page, url: str) -> tuple[str, ArticleMetrics | None]:
        """从已加载的36氪详情页同时提取正文+互动数据"""
        content = ""
        metrics = ArticleMetrics()

        try:
            # 等待内容加载
            try:
                page.wait_for_selector(
                    "article, div.article-content, div.content-detail",
                    timeout=10000,
                )
            except Exception:
                try:
                    page.wait_for_load_state("networkidle", timeout=8000)
                except Exception:
                    pass

            # 提取正文
            from rag.cleaner import TextCleaner
            for sel in ["article", "div.article-content", "div.content-detail"]:
                el = page.query_selector(sel)
                if el:
                    text = el.inner_text()
                    if len(text) > 100:
                        content = TextCleaner.clean(text)
                        break

            if not content:
                html = page.content()
                content = TextCleaner.extract_main_content(html)

            # 提取互动数据（点赞）
            try:
                like_el = page.query_selector("span[class*='like'], span[class*='favor'], button[class*='like']")
                if like_el:
                    text = like_el.inner_text()
                    nums = re.findall(r'\d+', text.replace(',', ''))
                    if nums:
                        metrics.likes = int(nums[0])
            except Exception:
                pass

            if metrics.likes:
                logger.debug(f"[36氪] 互动数据: likes={metrics.likes}")

        except Exception as e:
            logger.debug(f"[36氪] 详情页合并提取失败: {e}")

        return content, metrics if metrics.likes else None

    # ── 旧接口保留 ────────────────────────────────────────────

    def fetch_content(self, url: str) -> str:
        """36氪详情页抓取 — 优先 HTTP，降级 Playwright"""
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
                        "article, div.article-content, div.content-detail",
                        timeout=10000,
                    )
                except Exception:
                    try:
                        page.wait_for_load_state("networkidle", timeout=8000)
                    except Exception:
                        pass

                for sel in ["article", "div.article-content", "div.content-detail"]:
                    el = page.query_selector(sel)
                    if el:
                        text = el.inner_text()
                        if len(text) > 100:
                            return TextCleaner.clean(text)

                html = page.content()
                return TextCleaner.extract_main_content(html)

        except Exception as e:
            logger.debug(f"[36氪] 详情页抓取失败: {e}")
            return ""

    # ── 搜索接口 ──────────────────────────────────────────────

    def search(self, keyword: str, max_count: int = 50) -> list[ArticleData]:
        """爬取36氪搜索结果页（Playwright 渲染）"""
        articles = []
        seen_urls = set()

        try:
            with get_browser_page(headless=True) as page:
                url = f"https://36kr.com/search/articles/{quote(keyword)}"
                page.goto(url, timeout=30000)
                page.wait_for_load_state("networkidle", timeout=15000)

                # 滚动加载更多
                for _ in range(3):
                    if len(articles) >= max_count:
                        break
                    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    page.wait_for_timeout(2000)

                # 36氪实际结构: a.article-item-title, href="/p/xxxxx"
                title_links = page.query_selector_all("a.article-item-title")
                if not title_links:
                    title_links = page.query_selector_all("a[href*='/p/']")

                for link in title_links:
                    try:
                        href = link.get_attribute("href") or ""
                        if "/p/" not in href:
                            continue
                        if not href.startswith("http"):
                            href = f"https://36kr.com{href}"
                        if href in seen_urls:
                            continue
                        seen_urls.add(href)

                        title = link.inner_text().strip()
                        if not title:
                            continue

                        # 从父级获取摘要
                        summary = ""
                        metrics = ArticleMetrics()
                        try:
                            parent = link.evaluate_handle(
                                "el => el.closest('.kr-flow-article-item') || el.closest('div[class*=\"article-item\"]') || el.parentElement"
                            )
                            s_el = parent.query_selector("div.article-item-summary, p.summary")
                            if s_el:
                                summary = s_el.inner_text().strip()
                            l_el = parent.query_selector("span[class*='like'], span[class*='favor']")
                            if l_el:
                                nums = re.findall(r"\d+", l_el.inner_text())
                                if nums:
                                    metrics.likes = int(nums[0])
                        except Exception:
                            pass

                        article = ArticleData(
                            source=self.platform,
                            title=title,
                            content=clean_html(summary),
                            url=href,
                            metrics=metrics,
                            quality_score=score_article(ArticleData(source=self.platform, metrics=metrics)),
                        )
                        articles.append(article)
                        if len(articles) >= max_count:
                            break
                    except Exception:
                        continue

        except Exception as e:
            logger.warning(f"[36氪] Playwright 搜索失败: {e}")

        articles = normalize_scores(articles, self.platform)
        logger.info(f"[36氪] 搜索 '{keyword}' 完成，获取 {len(articles)} 篇")
        return articles[:max_count]
