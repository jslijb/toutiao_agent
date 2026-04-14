"""知乎爬虫（Playwright 渲染搜索 + Cookie 增强 + 合并详情页访问）"""
from __future__ import annotations

import json
import re
from typing import Optional
from urllib.parse import quote

from loguru import logger

from crawlers.base import BaseCrawler, get_browser_page
from crawlers.quality_scorer import score_article, normalize_scores
from models.article import ArticleData, ArticleMetrics
from utils.cookie_manager import cookie_manager
from utils.http_client import http_get, get_headers
from utils.text_utils import clean_html


class ZhihuCrawler(BaseCrawler):
    platform = "zhihu"

    SEARCH_URL = "https://www.zhihu.com/search"

    # ── 页面初始化（注入 Cookie） ─────────────────────────────

    def setup_page(self, page):
        """注入知乎 Cookie（搜索和详情页都需要）"""
        cookies = cookie_manager.load_cookies(self.platform)
        if cookies:
            try:
                cookie_list = []
                for c in cookies:
                    cookie_list.append({
                        "name": c.get("name", ""),
                        "value": c.get("value", ""),
                        "domain": c.get("domain", ".zhihu.com"),
                        "path": c.get("path", "/"),
                    })
                page.context.add_cookies(cookie_list)
            except Exception as e:
                logger.debug(f"[知乎] Cookie 注入失败: {e}")

    # ── 合并详情页访问 ────────────────────────────────────────

    def fetch_detail_from_page(self, page, url: str) -> tuple[str, ArticleMetrics | None]:
        """从已加载的知乎详情页同时提取正文+互动数据"""
        content = ""
        metrics = ArticleMetrics()

        try:
            # 等待内容加载
            try:
                page.wait_for_selector(
                    "div.RichContent-inner, div.RichText, div.Post-RichTextContainer, div.AnswerCard",
                    timeout=10000,
                )
            except Exception:
                try:
                    page.wait_for_load_state("networkidle", timeout=8000)
                except Exception:
                    pass

            # 提取正文
            from rag.cleaner import TextCleaner
            selectors = [
                "div.RichContent-inner",
                "div.RichText",
                "div.Post-RichTextContainer",
                "article",
            ]
            for sel in selectors:
                el = page.query_selector(sel)
                if el:
                    text = el.inner_text()
                    if len(text) > 100:
                        content = TextCleaner.clean(text)
                        break

            if not content:
                html = page.content()
                content = TextCleaner.extract_main_content(html)

            # 提取互动数据（点赞、评论）
            try:
                vote_btn = page.query_selector("button.VoteButton--up, button[class*='VoteButton--up']")
                if vote_btn:
                    text = vote_btn.inner_text()
                    nums = re.findall(r'\d+', text.replace(',', '').replace('万', '0000'))
                    if nums:
                        metrics.likes = int(nums[0])
                    elif "赞同" in text:
                        metrics.likes = 1
            except Exception:
                pass

            try:
                comment_btn = page.query_selector("button[class*='Comment'], a[class*='Comment']")
                if comment_btn:
                    text = comment_btn.inner_text()
                    nums = re.findall(r'\d+', text.replace(',', '').replace('万', '0000'))
                    if nums:
                        metrics.comments = int(nums[0])
                    elif "评论" in text:
                        metrics.comments = 1
            except Exception:
                pass

            if metrics.likes or metrics.comments:
                logger.debug(f"[知乎] 互动数据: likes={metrics.likes}, comments={metrics.comments}")

        except Exception as e:
            logger.debug(f"[知乎] 详情页合并提取失败: {e}")

        return content, metrics if (metrics.likes or metrics.comments) else None

    # ── HTTP 详情页（知乎需要登录，HTTP 基本无效，但保留尝试） ──

    def fetch_content_http(self, url: str) -> str:
        """HTTP 方式抓取知乎详情页（通常需要 Cookie，成功率低）"""
        if not url:
            return ""
        try:
            from rag.cleaner import TextCleaner
            headers = get_headers({
                "Referer": "https://www.zhihu.com/",
                "Accept": "text/html,application/xhtml+xml",
                "Cookie": cookie_manager.to_cookie_header(self.platform) or "",
            })
            resp = http_get(url, headers=headers, timeout=15)
            if resp.status_code == 200 and len(resp.text) > 500:
                return TextCleaner.extract_main_content(resp.text)
        except Exception:
            pass
        return ""

    # ── 旧接口保留 ────────────────────────────────────────────

    def fetch_content(self, url: str) -> str:
        """知乎详情页抓取 — 需要带 Cookie，且从回答中提取 RichContent"""
        if not url:
            return ""

        try:
            from rag.cleaner import TextCleaner

            with get_browser_page(headless=True) as page:
                # 注入 Cookie
                self.setup_page(page)

                page.goto(url, timeout=30000, wait_until="domcontentloaded")

                # 等待内容加载
                try:
                    page.wait_for_selector(
                        "div.RichContent-inner, div.RichText, div.Post-RichTextContainer, div.AnswerCard",
                        timeout=10000,
                    )
                except Exception:
                    try:
                        page.wait_for_load_state("networkidle", timeout=8000)
                    except Exception:
                        pass

                # 优先提取回答/文章正文
                selectors = [
                    "div.RichContent-inner",
                    "div.RichText",
                    "div.Post-RichTextContainer",
                    "article",
                ]
                for sel in selectors:
                    el = page.query_selector(sel)
                    if el:
                        text = el.inner_text()
                        if len(text) > 100:
                            return TextCleaner.clean(text)

                # 降级：通用提取
                html = page.content()
                return TextCleaner.extract_main_content(html)

        except Exception as e:
            logger.debug(f"[知乎] 详情页抓取失败: {e}")
            return ""

    # ── 搜索接口 ──────────────────────────────────────────────

    def search(self, keyword: str, max_count: int = 50) -> list[ArticleData]:
        """通过 Playwright 渲染知乎搜索页获取文章/回答列表"""
        articles = []
        seen_urls = set()

        try:
            with get_browser_page(headless=True) as page:
                # 注入已保存的 Cookie（如果有）
                self.setup_page(page)

                # 访问搜索页（按时间排序，优先最新内容）
                search_url = f"{self.SEARCH_URL}?type=content&q={quote(keyword)}&sort=created_time"
                page.goto(search_url, timeout=30000)
                page.wait_for_load_state("networkidle", timeout=15000)

                # 检测是否需要登录
                body_text = page.inner_text("body")[:500]
                if "登录" in body_text and ("注册" in body_text or "未搜索到" in body_text):
                    # 检测搜索结果区域
                    search_results = page.query_selector_all(
                        "div.SearchResult-Card, div.ContentItem, div[class*='SearchResult']"
                    )
                    if not search_results:
                        logger.warning(
                            "[知乎] 搜索页需要登录才能获取结果。"
                            "请在 WebUI 爬虫 Tab 中点击「知乎 - 登录获取 Cookie」按钮，"
                            "扫码登录后再重试。"
                        )
                        return []

                # 滚动加载更多结果
                for scroll_round in range(5):
                    if len(articles) >= max_count:
                        break
                    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    page.wait_for_timeout(2500)

                # 提取搜索结果
                results = page.query_selector_all(
                    "div.SearchResult-Card, div.ContentItem-Content, div[class*='SearchResult-Card']"
                )
                if not results:
                    # 尝试更宽泛的选择器
                    results = page.query_selector_all("div[class*='ContentItem'], div[class*='search-result']")

                for item in results:
                    try:
                        # 标题
                        title_el = item.query_selector("h2 a, h3 a, span.Highlight")
                        if not title_el:
                            continue
                        title = title_el.inner_text().strip()
                        href = title_el.get_attribute("href") or ""
                        if href and not href.startswith("http"):
                            href = f"https://www.zhihu.com{href}"

                        if not title or href in seen_urls:
                            continue
                        # 过滤话题页、专栏主页等非文章 URL
                        if "/topic/" in href and "/question/" not in href:
                            continue
                        if "/people/" in href:
                            continue
                        if href.endswith("/answers"):
                            continue
                        seen_urls.add(href)

                        # 摘要/内容
                        content_el = item.query_selector("div.RichContent-inner, div.content, span.RichText")
                        content = clean_html(content_el.inner_text()[:500]) if content_el else ""

                        # 作者
                        author_el = item.query_selector("a.AuthorInfo-name, span.AuthorInfo-name")
                        author = author_el.inner_text().strip() if author_el else ""

                        # 互动数据
                        metrics = ArticleMetrics()
                        for cls_name, attr in [
                            ("Button--like", "likes"),
                            ("Button--comment", "comments"),
                        ]:
                            try:
                                btn = item.query_selector(f"button.{cls_name}, div[class*='{cls_name}']")
                                if btn:
                                    text = btn.inner_text()
                                    nums = re.findall(r"\d+", text)
                                    if nums:
                                        setattr(metrics, attr, int(nums[0]))
                                    elif "赞同" in text or "评论" in text:
                                        setattr(metrics, attr, 1)
                            except Exception:
                                pass

                        article = ArticleData(
                            source=self.platform,
                            title=title,
                            content=content,
                            url=href,
                            author=author,
                            metrics=metrics,
                            quality_score=score_article(ArticleData(source=self.platform, metrics=metrics)),
                        )
                        articles.append(article)

                        if len(articles) >= max_count:
                            break

                    except Exception:
                        continue

                # 保存当前页面的 Cookie 供后续使用
                try:
                    browser_cookies = page.context.cookies()
                    if browser_cookies:
                        cookie_list = []
                        for c in browser_cookies:
                            cookie_list.append({
                                "name": c["name"],
                                "value": c["value"],
                                "domain": c.get("domain", ""),
                                "path": c.get("path", "/"),
                            })
                        cookie_manager.save_cookies(self.platform, cookie_list)
                except Exception:
                    pass

        except Exception as e:
            logger.warning(f"[知乎] 搜索失败: {e}")

        articles = normalize_scores(articles, self.platform)
        logger.info(f"[知乎] 搜索 '{keyword}' 完成，获取 {len(articles)} 篇")
        return articles[:max_count]
