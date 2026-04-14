"""微信公众号爬虫（搜狗搜索 + 同一会话内跳转 + HTTP优先 + 合并详情页访问）

方案说明：
1. 搜索 + 详情页在同一 Playwright 非 headless 会话中完成
2. 使用 page.evaluate 一次性提取搜索结果，避免元素句柄失效
3. 在同一浏览器中导航到搜狗中间链接，可正常跳转到 mp.weixin.qq.com
4. HTTP 优先抓取微信文章全文（MicroMessenger UA），失败才用 Playwright
5. 新流程：fetch_content_http 尝试 MicroMessenger UA，fetch_detail_from_page 合并提取
"""
from __future__ import annotations

import re
import json
import time
from typing import Optional
from urllib.parse import quote, urlparse, parse_qs

from bs4 import BeautifulSoup
from loguru import logger

from crawlers.base import BaseCrawler, get_browser_page
from crawlers.quality_scorer import score_article, normalize_scores
from config.settings import settings
from models.article import ArticleData, ArticleMetrics
from utils.cookie_manager import cookie_manager
from utils.http_client import http_get, get_headers

# 微信内置浏览器 UA —— 伪装成微信客户端可绕过文章页滑块验证码
WECHAT_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 "
    "MicroMessenger/8.0.34(0x16082222) NetType/WIFI Language/zh_CN"
)


def _fetch_wechat_article_via_http(url: str) -> str:
    """使用 MicroMessenger UA 通过 HTTP 直接抓取微信文章全文。"""
    from rag.cleaner import TextCleaner

    headers = {
        "User-Agent": WECHAT_UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Referer": "https://mp.weixin.qq.com/",
    }
    try:
        resp = http_get(url, headers=headers, timeout=20)
        if resp.status_code == 200 and len(resp.text) > 500:
            soup = BeautifulSoup(resp.text, "lxml")
            js_content = soup.select_one("div#js_content, div.rich_media_content")
            if js_content:
                text = js_content.get_text(strip=True)
                if len(text) > 100:
                    return TextCleaner.clean(text)

            content = TextCleaner.extract_main_content(resp.text)
            if len(content) > 100:
                return content
    except Exception as e:
        logger.debug(f"[微信] MicroMessenger HTTP 抓取失败: {e}")
    return ""


class WechatCrawler(BaseCrawler):
    platform = "wechat"

    SOGOU_SEARCH_URL = "https://weixin.sogou.com/weixin"

    # ── 页面初始化（注入搜狗 Cookie） ─────────────────────────

    def setup_page(self, page):
        """注入搜狗 Cookie"""
        cookies = cookie_manager.load_cookies(self.platform)
        if cookies:
            try:
                cookie_list = [
                    {
                        "name": c.get("name", ""),
                        "value": c.get("value", ""),
                        "domain": c.get("domain", ".sogou.com"),
                        "path": c.get("path", "/"),
                    }
                    for c in cookies
                ]
                page.context.add_cookies(cookie_list)
            except Exception:
                pass

    # ── HTTP 优先抓取详情页 ──────────────────────────────────

    def fetch_content_http(self, url: str) -> str:
        """HTTP 方式抓取微信文章全文（MicroMessenger UA）"""
        if not url:
            return ""

        # mp.weixin.qq.com 直达链接 → HTTP 抓取
        if "mp.weixin.qq.com" in url:
            return _fetch_wechat_article_via_http(url)

        # 搜狗中间链接无法通过 HTTP 解析，需要浏览器跳转
        return ""

    # ── 合并详情页访问 ────────────────────────────────────────

    def fetch_detail_from_page(self, page, url: str) -> tuple[str, ArticleMetrics | None]:
        """从已加载的微信文章页提取正文+互动数据"""
        content = ""
        metrics = ArticleMetrics()

        try:
            # 如果当前在搜狗中间页，需要先等待跳转
            current = page.url
            if "sogou.com" in current and "mp.weixin.qq.com" not in current:
                # 等待跳转
                page.wait_for_timeout(5000)
                current = page.url

            if "mp.weixin.qq.com" in current:
                self._save_cookies(page)
                content = self._extract_from_wechat_page_inline(page)

            # 微信文章页通常没有公开的互动数据按钮
            # metrics 保持空

        except Exception as e:
            logger.debug(f"[微信] 详情页合并提取失败: {e}")

        return content, None

    def _extract_from_wechat_page_inline(self, page) -> str:
        """从微信文章页面提取全文内容（无 context 管理，页面已存在）"""
        from rag.cleaner import TextCleaner

        try:
            page.wait_for_selector(
                "div#js_content, div.rich_media_content",
                timeout=15000,
            )
        except Exception:
            pass

        for sel in ["div#js_content", "div.rich_media_content"]:
            el = page.query_selector(sel)
            if el:
                text = el.inner_text()
                if len(text) > 100:
                    return TextCleaner.clean(text)

        return ""

    # ── 爬取入口 ─────────────────────────────────────────────

    def crawl(self, keywords: list[str] | None = None, max_count: int | None = None) -> list[ArticleData]:
        """微信爬虫使用一站式搜索+抓取，重写父类 crawl 方法"""
        from crawlers.base import _is_running_in_async_loop, _executor
        keywords = keywords or self.get_keywords()
        max_count = max_count or settings.crawler.max_articles_per_platform

        if _is_running_in_async_loop():
            future = _executor.submit(self._crawl_sync, keywords, max_count)
            return future.result(timeout=600)
        else:
            return self._crawl_sync(keywords, max_count)

    def _crawl_sync(self, keywords: list[str], max_count: int) -> list[ArticleData]:
        """微信专用爬取实现：使用 search_with_content 一站式完成搜索+详情页抓取"""
        from models.article_store import get_article_store

        store = get_article_store()
        all_articles: list[ArticleData] = []
        seen_urls = set()

        for keyword in keywords:
            logger.info(f"[{self.platform}] 搜索关键词: {keyword}")
            try:
                articles = self.search_with_content(keyword, max_count=max_count)
                new_count = 0
                for art in articles:
                    if art.url and art.url not in seen_urls:
                        seen_urls.add(art.url)
                        if store.is_duplicate(art):
                            logger.debug(f"[{self.platform}] 跳过重复文章: {art.title[:30]}")
                            continue
                        all_articles.append(art)
                        new_count += 1
                logger.info(f"[{self.platform}] 关键词 '{keyword}': 搜索 {len(articles)} 篇, 新增 {new_count} 篇")
                self.delay()
            except Exception as e:
                logger.error(f"[{self.platform}] 搜索 '{keyword}' 失败: {e}")

        # 设置 TTL
        ttl_days = getattr(settings.crawler, 'article_ttl_days', 30)
        for art in all_articles:
            art.ttl_days = ttl_days

        # 持久化到 ArticleStore
        added = store.add_many(all_articles)
        logger.info(f"[{self.platform}] 共爬取 {len(all_articles)} 篇, 新增 {added} 篇（去重后）")

        all_articles.sort(key=lambda x: x.quality_score, reverse=True)
        return all_articles

    # ── 旧接口保留 ────────────────────────────────────────────

    def fetch_content(self, url: str) -> str:
        """微信文章详情页抓取（单独调用时使用，推荐用 search_with_content 代替）"""
        if not url:
            return ""

        # mp.weixin.qq.com 直达链接
        if "mp.weixin.qq.com" in url:
            return _fetch_wechat_article_via_http(url)

        # 搜狗中间链接 → 非 headless Playwright 跳转 + 提取
        if "sogou.com" in url:
            return self._resolve_and_fetch(url)

        # 其他链接
        return _fetch_wechat_article_via_http(url)

    def _resolve_and_fetch(self, sogou_url: str) -> str:
        """单独解析搜狗中间链接并抓取文章（非 headless Playwright）"""
        from rag.cleaner import TextCleaner

        try:
            with get_browser_page(headless=False) as page:
                self._inject_cookies(page)

                captured = [sogou_url]
                def on_response(response):
                    if "mp.weixin.qq.com" in response.url:
                        captured[0] = response.url

                page.on("response", on_response)
                page.goto(sogou_url, timeout=20000, wait_until="domcontentloaded")
                page.wait_for_timeout(5000)

                current = page.url

                # 成功跳转到微信文章页
                if "mp.weixin.qq.com" in current:
                    self._save_cookies(page)
                    return self._extract_from_wechat_page(page)

                if "mp.weixin.qq.com" in captured[0]:
                    self._save_cookies(page)
                    return self._extract_from_wechat_page(page)

                # 搜狗验证码
                if "antispider" in current:
                    logger.info("[微信] 搜狗验证码页面，等待用户处理（30秒）...")
                    page.wait_for_timeout(30000)
                    current = page.url
                    if "mp.weixin.qq.com" in current:
                        self._save_cookies(page)
                        return self._extract_from_wechat_page(page)

                return ""
        except Exception as e:
            logger.debug(f"[微信] 解析搜狗链接失败: {e}")
            return ""

    def _extract_from_wechat_page(self, page) -> str:
        """从微信文章页面提取全文内容（Playwright）"""
        from rag.cleaner import TextCleaner

        try:
            page.wait_for_selector(
                "div#js_content, div.rich_media_content",
                timeout=15000,
            )
        except Exception:
            pass

        for sel in ["div#js_content", "div.rich_media_content"]:
            el = page.query_selector(sel)
            if el:
                text = el.inner_text()
                if len(text) > 100:
                    return TextCleaner.clean(text)

        return ""

    def _inject_cookies(self, page):
        """注入搜狗 cookie"""
        cookies = cookie_manager.load_cookies(self.platform)
        if cookies:
            try:
                cookie_list = [
                    {
                        "name": c.get("name", ""),
                        "value": c.get("value", ""),
                        "domain": c.get("domain", ".sogou.com"),
                        "path": c.get("path", "/"),
                    }
                    for c in cookies
                ]
                page.context.add_cookies(cookie_list)
            except Exception:
                pass

    def _save_cookies(self, page):
        """保存当前浏览器 cookie"""
        try:
            browser_cookies = page.context.cookies()
            cookie_manager.save_cookies(self.platform, browser_cookies)
        except Exception:
            pass

    # ── 搜索接口 ──────────────────────────────────────────────

    def search(self, keyword: str, max_count: int = 50) -> list[ArticleData]:
        """搜索微信公众号文章（仅获取列表，不抓全文）"""
        articles = self._search_via_playwright(keyword, max_count)

        if not articles:
            logger.info("[微信] Playwright 无结果，回退到 HTTP 模式...")
            articles = self._search_via_http(keyword, max_count)

        articles = normalize_scores(articles, self.platform)
        logger.info(f"[微信] 搜索 '{keyword}' 完成，获取 {len(articles)} 篇")
        return articles[:max_count]

    def search_with_content(self, keyword: str, max_count: int = 10) -> list[ArticleData]:
        """一站式搜索 + 抓取全文：在同一浏览器会话中完成搜索和详情页抓取。

        优化：先尝试 MicroMessenger HTTP 抓取全文，失败才在同一浏览器中跳转。
        """
        articles = []
        seen_urls = set()

        try:
            with get_browser_page(headless=False) as page:
                # 注入 cookie
                self._inject_cookies(page)

                # 1. 搜索
                search_url = f"{self.SOGOU_SEARCH_URL}?type=2&query={quote(keyword)}"
                page.goto(search_url, timeout=30000)
                page.wait_for_load_state("networkidle", timeout=15000)

                # 检查搜索页是否被搜狗拦截
                if "antispider" in page.url:
                    logger.info("[微信] 搜索页被搜狗拦截，等待用户处理（30秒）...")
                    page.wait_for_timeout(30000)
                    page.goto(search_url, timeout=30000)
                    page.wait_for_load_state("networkidle", timeout=15000)

                # 保存搜索页 cookie
                self._save_cookies(page)

                # 2. 用 page.evaluate 一次性提取搜索结果
                article_infos = page.evaluate("""(maxCount) => {
                    const items = document.querySelectorAll('div.txt-box');
                    if (!items.length) {
                        const altItems = document.querySelectorAll('ul.news-list li');
                        return Array.from(altItems).slice(0, maxCount).map(item => {
                            const titleEl = item.querySelector('h3 a');
                            const summaryEl = item.querySelector('p.txt-info, p.s-p');
                            const sourceEl = item.querySelector('div.s-p a.account');
                            return {
                                title: titleEl ? titleEl.innerText.trim() : '',
                                href: titleEl ? titleEl.getAttribute('href') : '',
                                summary: summaryEl ? summaryEl.innerText.trim() : '',
                                author: sourceEl ? sourceEl.innerText.trim() : ''
                            };
                        });
                    }
                    return Array.from(items).slice(0, maxCount).map(item => {
                        const titleEl = item.querySelector('h3 a');
                        const summaryEl = item.querySelector('p.txt-info, p.s-p');
                        const sourceEl = item.querySelector('div.s-p a.account');
                        return {
                            title: titleEl ? titleEl.innerText.trim() : '',
                            href: titleEl ? titleEl.getAttribute('href') : '',
                            summary: summaryEl ? summaryEl.innerText.trim() : '',
                            author: sourceEl ? sourceEl.innerText.trim() : ''
                        };
                    });
                }""", max_count)

                # 补全相对路径
                for info in article_infos:
                    if info['href'] and info['href'].startswith('/'):
                        info['href'] = f"https://weixin.sogou.com{info['href']}"

                logger.info(f"[微信] 搜狗搜索 '{keyword}' 获取 {len(article_infos)} 篇")

                # 3. 逐一访问每篇文章，获取全文
                for info in article_infos:
                    title = info['title']
                    href = info['href']
                    summary = info['summary']
                    author = info['author']

                    if not title or href in seen_urls:
                        continue
                    seen_urls.add(href)

                    article = ArticleData(
                        source=self.platform,
                        title=title,
                        content=summary,
                        url=href,
                        author=author,
                    )

                    # 优化：先尝试 MicroMessenger HTTP 抓取
                    # 从搜狗中间链接中提取 mp.weixin.qq.com 真实 URL（如果有）
                    http_content = ""
                    if "mp.weixin.qq.com" in href:
                        http_content = _fetch_wechat_article_via_http(href)

                    if http_content and len(http_content) > len(summary):
                        article.content = http_content
                        logger.debug(f"[微信] HTTP全文提取成功: {title[:30]} ({len(http_content)} 字)")
                    else:
                        # 降级：在同一浏览器中获取全文
                        full_content = self._fetch_in_session(page, href)
                        if full_content and len(full_content) > len(summary):
                            article.content = full_content
                            logger.debug(f"[微信] Playwright全文提取成功: {title[:30]} ({len(full_content)} 字)")

                    articles.append(article)
                    if len(articles) >= max_count:
                        break

                    self.detail_delay()  # 用更短的详情页延迟

        except Exception as e:
            logger.warning(f"[微信] 一站式搜索失败: {e}")

        articles = normalize_scores(articles, self.platform)
        content_count = sum(1 for a in articles if len(a.content) > 200)
        logger.info(f"[微信] 一站式搜索 '{keyword}' 完成: {len(articles)} 篇, {content_count} 篇有全文")
        return articles[:max_count]

    def _fetch_in_session(self, page, sogou_url: str) -> str:
        """在同一浏览器会话中导航到搜狗中间链接，获取微信文章全文。"""
        from rag.cleaner import TextCleaner

        try:
            # 拦截 mp.weixin.qq.com 请求
            captured = [sogou_url]
            def on_response(response):
                if "mp.weixin.qq.com" in response.url:
                    captured[0] = response.url

            page.on("response", on_response)

            page.goto(sogou_url, timeout=20000, wait_until="domcontentloaded")
            page.wait_for_timeout(5000)

            current = page.url

            # 成功跳转到微信文章页
            if "mp.weixin.qq.com" in current:
                content = self._extract_from_wechat_page(page)
                if content:
                    return content
                # Playwright 提取失败，尝试 MicroMessenger UA HTTP
                wechat_url = captured[0] if "mp.weixin.qq.com" in captured[0] else current
                return _fetch_wechat_article_via_http(wechat_url)

            if "mp.weixin.qq.com" in captured[0]:
                content = self._extract_from_wechat_page(page)
                if content:
                    return content
                return _fetch_wechat_article_via_http(captured[0])

            # 搜狗验证码
            if "antispider" in current:
                logger.debug("[微信] 同一会话内遇到搜狗验证码，跳过")
                return ""

            return ""

        except Exception as e:
            logger.debug(f"[微信] 同会话详情页抓取失败: {e}")
            return ""

    def _search_via_playwright(self, keyword: str, max_count: int) -> list[ArticleData]:
        """通过 Playwright 渲染搜狗微信搜索获取文章列表（仅标题+摘要）"""
        articles = []
        seen_urls = set()

        try:
            with get_browser_page(headless=True) as page:
                self._inject_cookies(page)

                url = f"{self.SOGOU_SEARCH_URL}?type=2&query={quote(keyword)}"
                page.goto(url, timeout=30000)
                page.wait_for_load_state("networkidle", timeout=15000)

                self._save_cookies(page)

                # 滚动加载更多
                for _ in range(2):
                    if len(articles) >= max_count:
                        break
                    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    page.wait_for_timeout(2000)

                # 用 evaluate 提取，避免元素句柄问题
                article_infos = page.evaluate("""(maxCount) => {
                    const items = document.querySelectorAll('div.txt-box');
                    if (!items.length) {
                        const altItems = document.querySelectorAll('ul.news-list li');
                        return Array.from(altItems).slice(0, maxCount).map(item => {
                            const titleEl = item.querySelector('h3 a');
                            const summaryEl = item.querySelector('p.txt-info, p.s-p');
                            const sourceEl = item.querySelector('div.s-p a.account');
                            return {
                                title: titleEl ? titleEl.innerText.trim() : '',
                                href: titleEl ? titleEl.getAttribute('href') : '',
                                summary: summaryEl ? summaryEl.innerText.trim() : '',
                                author: sourceEl ? sourceEl.innerText.trim() : ''
                            };
                        });
                    }
                    return Array.from(items).slice(0, maxCount).map(item => {
                        const titleEl = item.querySelector('h3 a');
                        const summaryEl = item.querySelector('p.txt-info, p.s-p');
                        const sourceEl = item.querySelector('div.s-p a.account');
                        return {
                            title: titleEl ? titleEl.innerText.trim() : '',
                            href: titleEl ? titleEl.getAttribute('href') : '',
                            summary: summaryEl ? summaryEl.innerText.trim() : '',
                            author: sourceEl ? sourceEl.innerText.trim() : ''
                        };
                    });
                }""", max_count)

                for info in article_infos:
                    if not info['title'] or info['href'] in seen_urls:
                        continue
                    href = info['href']
                    if href.startswith('/'):
                        href = f"https://weixin.sogou.com{href}"
                    seen_urls.add(href)

                    article = ArticleData(
                        source=self.platform,
                        title=info['title'],
                        content=info['summary'],
                        url=href,
                        author=info['author'],
                    )
                    articles.append(article)

        except Exception as e:
            logger.warning(f"[微信] Playwright 搜索失败: {e}")

        return articles

    def _search_via_http(self, keyword: str, max_count: int) -> list[ArticleData]:
        """通过搜狗 HTTP 搜索获取文章列表（备用）"""
        articles = []
        page_num = 1

        headers = get_headers({
            "Referer": "https://weixin.sogou.com/",
            "Host": "weixin.sogou.com",
        })

        cookie_str = cookie_manager.to_cookie_header(self.platform)
        if cookie_str:
            headers["Cookie"] = cookie_str

        while len(articles) < max_count:
            try:
                params = {"type": "2", "query": keyword, "page": page_num}
                resp = http_get(self.SOGOU_SEARCH_URL, params=params, headers=headers)
                soup = BeautifulSoup(resp.text, "lxml")

                items = soup.select("div.txt-box")
                if not items:
                    break

                for item in items:
                    try:
                        title_el = item.select_one("h3 a")
                        if not title_el:
                            continue
                        title = title_el.get_text(strip=True)
                        url = title_el.get("href", "")
                        if url.startswith("/"):
                            url = f"https://weixin.sogou.com{url}"

                        summary_el = item.select_one("p.txt-info")
                        summary = summary_el.get_text(strip=True) if summary_el else ""

                        source_el = item.select_one("div.s-p a.account")
                        author = source_el.get_text(strip=True) if source_el else ""

                        if url in [a.url for a in articles]:
                            continue

                        article = ArticleData(
                            source=self.platform,
                            title=title,
                            content=summary,
                            url=url,
                            author=author,
                        )
                        articles.append(article)
                    except Exception:
                        continue

                    if len(articles) >= max_count:
                        break

                page_num += 1
                self.delay()

            except Exception as e:
                logger.warning(f"[微信] HTTP 搜索页 {page_num} 请求失败: {e}")
                break

        return articles
