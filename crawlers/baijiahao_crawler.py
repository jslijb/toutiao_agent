"""百家号爬虫（百度资讯搜索 + HTTP优先 + 合并详情页访问）"""
from __future__ import annotations

import re
from urllib.parse import quote

from loguru import logger

from crawlers.base import BaseCrawler, get_browser_page
from crawlers.quality_scorer import score_article, normalize_scores
from models.article import ArticleData, ArticleMetrics
from utils.http_client import http_get, get_headers
from utils.text_utils import clean_html


class BaijiahaoCrawler(BaseCrawler):
    platform = "baijiahao"

    # ── HTTP 优先抓取详情页 ──────────────────────────────────

    def fetch_content_http(self, url: str) -> str:
        """HTTP 方式抓取百家号文章全文（百家号静态内容多，HTTP 成功率高）"""
        if not url:
            return ""
        try:
            from rag.cleaner import TextCleaner
            headers = get_headers({
                "Referer": "https://www.baidu.com/",
                "Accept": "text/html,application/xhtml+xml",
            })
            resp = http_get(url, headers=headers, timeout=15)
            if resp.status_code == 200:
                return TextCleaner.extract_main_content(resp.text)
        except Exception as e:
            logger.debug(f"[百家号] HTTP 抓取失败: {e}")
        return ""

    # ── 合并详情页访问 ────────────────────────────────────────

    def fetch_detail_from_page(self, page, url: str) -> tuple[str, ArticleMetrics | None]:
        """从已加载的百家号详情页同时提取正文+互动数据"""
        content = ""
        metrics = ArticleMetrics()

        try:
            # 等待内容加载
            try:
                page.wait_for_selector(
                    "div.article-content, div#indexContent, article",
                    timeout=10000,
                )
            except Exception:
                try:
                    page.wait_for_load_state("networkidle", timeout=8000)
                except Exception:
                    pass

            # 提取正文
            from rag.cleaner import TextCleaner
            for sel in [
                "div.article-content",
                "div#indexContent",
                "div.mainContent",
                "article",
            ]:
                el = page.query_selector(sel)
                if el:
                    text = el.inner_text()
                    if len(text) > 100:
                        content = TextCleaner.clean(text)
                        break

            if not content:
                html = page.content()
                content = TextCleaner.extract_main_content(html)

            # 提取互动数据（百家号页面可能包含点赞、评论）
            try:
                like_el = page.query_selector("span[class*='like'], div[class*='like'], button[class*='like']")
                if like_el:
                    text = like_el.inner_text()
                    nums = re.findall(r'\d+', text.replace(',', ''))
                    if nums:
                        metrics.likes = int(nums[0])
            except Exception:
                pass

            try:
                comment_el = page.query_selector("span[class*='comment'], div[class*='comment']")
                if comment_el:
                    text = comment_el.inner_text()
                    nums = re.findall(r'\d+', text.replace(',', ''))
                    if nums:
                        metrics.comments = int(nums[0])
            except Exception:
                pass

        except Exception as e:
            logger.debug(f"[百家号] 详情页合并提取失败: {e}")

        return content, metrics if (metrics.likes or metrics.comments) else None

    # ── 旧接口保留 ────────────────────────────────────────────

    def fetch_content(self, url: str) -> str:
        """百家号/新闻详情页抓取 — 优先 HTTP，降级 Playwright"""
        if not url:
            return ""

        from rag.cleaner import TextCleaner

        # 优先用 HTTP 请求（避免百度安全验证）
        if "baijiahao.baidu.com" in url:
            http_content = self.fetch_content_http(url)
            if http_content and len(http_content) > 100:
                return http_content

        # 降级到 Playwright
        try:
            with get_browser_page(headless=True) as page:
                page.goto(url, timeout=30000, wait_until="domcontentloaded")

                try:
                    page.wait_for_selector(
                        "div.article-content, div#indexContent, article",
                        timeout=10000,
                    )
                except Exception:
                    try:
                        page.wait_for_load_state("networkidle", timeout=8000)
                    except Exception:
                        pass

                for sel in [
                    "div.article-content",
                    "div#indexContent",
                    "div.mainContent",
                    "article",
                ]:
                    el = page.query_selector(sel)
                    if el:
                        text = el.inner_text()
                        if len(text) > 100:
                            return TextCleaner.clean(text)

                html = page.content()
                return TextCleaner.extract_main_content(html)

        except Exception as e:
            logger.debug(f"[百家号] 详情页抓取失败: {e}")
            return ""

    # ── 搜索接口 ──────────────────────────────────────────────

    def search(self, keyword: str, max_count: int = 50) -> list[ArticleData]:
        """搜索百家号文章（百度资讯 + 跟随跳转 + 来源过滤）"""
        articles = []
        seen_urls = set()
        seen_titles = set()

        # 尝试两种搜索策略（添加时间范围和按时间排序）
        # 百度资讯 gpc 参数: stf=起始时间戳,etf=结束时间戳（URL编码的时间戳）
        from datetime import datetime, timedelta
        one_year_ago = int((datetime.now() - timedelta(days=365)).timestamp())
        now_ts = int(datetime.now().timestamp())
        # gpc 值格式: stf{start},{end}etf（需要 URL 编码逗号）
        gpc_value = f"stf{one_year_ago},{now_ts}etf"

        search_urls = [
            (
                f"https://www.baidu.com/s?wd={quote(keyword)}&rtt=4&bsst=1&cl=2&tn=news&gpc={quote(gpc_value)}",
                "百度资讯(按时间排序+1年内)",
            ),
            (
                f"https://www.baidu.com/s?wd={quote(keyword)}+site%3Abaijiahao.baidu.com&rn=20&rtt=4&bsst=1",
                "百度搜索(site:百家号+按时间)",
            ),
        ]

        for search_url, strategy_name in search_urls:
            if len(articles) >= max_count:
                break
            logger.info(f"[百家号] 尝试策略: {strategy_name}")
            try:
                with get_browser_page(headless=True) as page:
                    page.goto(search_url, timeout=45000, wait_until="domcontentloaded")
                    page.wait_for_load_state("networkidle", timeout=20000)

                    # 滚动加载
                    for _ in range(3):
                        if len(articles) >= max_count:
                            break
                        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                        page.wait_for_timeout(2000)

                    results = page.query_selector_all("div.result, div.c-container")
                    logger.debug(f"[百家号] 搜索结果数: {len(results)}")

                    for result in results:
                        try:
                            h3 = result.query_selector("h3")
                            if not h3:
                                continue
                            link_el = h3.query_selector("a")
                            if not link_el:
                                continue

                            title = link_el.inner_text().strip()
                            raw_url = link_el.get_attribute("href") or ""

                            if not title or not raw_url or title in seen_titles:
                                continue
                            if len(title) < 8 or "百度" in title[:5]:
                                continue
                            # 过滤搜索页标题（包含 site: 或 最新相关）
                            if "site:" in title or "最新相关" in title:
                                continue

                            seen_titles.add(title)

                            # 跟随百度跳转获取真实 URL
                            real_url = self._follow_redirect(page, raw_url)
                            if not real_url:
                                real_url = raw_url

                            # 优先保留百家号，但也接受其他新闻来源
                            is_bjh = real_url and "baijiahao.baidu.com" in real_url
                            if real_url in seen_urls:
                                continue
                            seen_urls.add(real_url)

                            if not is_bjh:
                                logger.debug(f"[百家号] 接受非百家号结果: {real_url[:80]}")

                            # 摘要
                            summary_el = result.query_selector(
                                "div.c-abstract, span.content-right_8Zs40, div.c-span-last"
                            )
                            summary = summary_el.inner_text().strip() if summary_el else ""

                            # 来源
                            source_el = result.query_selector(
                                "span.c-color-gray, a.c-color-gray, p.c-color-gray"
                            )
                            source = source_el.inner_text().strip() if source_el else ""

                            # 时间
                            time_el = result.query_selector("span.c-color-gray2")
                            pub_time = time_el.inner_text().strip() if time_el else ""

                            article = ArticleData(
                                source=self.platform,
                                title=title,
                                content=clean_html(summary),
                                url=real_url,
                                author=source,
                                publish_time=pub_time,
                                quality_score=score_article(
                                    ArticleData(source=self.platform, title=title, content=summary)
                                ),
                            )
                            articles.append(article)

                            if len(articles) >= max_count:
                                break

                        except Exception:
                            continue

            except Exception as e:
                logger.warning(f"[百家号] 策略 '{strategy_name}' 失败: {e}")
                continue

        articles = normalize_scores(articles, self.platform)
        logger.info(f"[百家号] 搜索 '{keyword}' 完成，获取 {len(articles)} 篇")
        return articles[:max_count]

    def _follow_redirect(self, page, url: str) -> str | None:
        """用新标签页跟随重定向，获取最终 URL"""
        try:
            new_page = page.context.new_page()
            new_page.goto(url, timeout=15000, wait_until="commit")
            real_url = new_page.url
            new_page.close()
            if "baidu.com/link" in real_url or "baidu.com/s" in real_url:
                return None
            return real_url
        except Exception:
            return None
