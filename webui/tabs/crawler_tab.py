"""Tab 2: 爬虫控制 - 选择平台、关键词、启动爬虫、平台登录

支持后台爬虫：爬虫在后台线程中运行，UI 不阻塞，
用户可以在爬虫运行期间切换到文章生成 Tab 生成文章。
"""
from __future__ import annotations

import threading
from datetime import datetime

import gradio as gr
from loguru import logger

from agent.pipeline import CrawlPipeline, RAGPipeline
from utils.cookie_manager import cookie_manager

# ── 后台爬虫状态管理 ──────────────────────────────────────
_crawl_state = {
    "running": False,
    "start_time": None,
    "progress": "",
    "stats": "",
    "done": False,
    "error": None,
}
_crawl_lock = threading.Lock()


def create_crawler_tab() -> gr.Blocks:
    """创建爬虫控制 Tab"""
    with gr.Blocks() as tab:
        gr.Markdown("## 爬虫控制")

        with gr.Row():
            with gr.Column():
                platform_check = gr.CheckboxGroup(
                    choices=["toutiao", "zhihu", "wechat", "baijiahao", "kr36"],
                    value=["toutiao", "zhihu", "kr36"],
                    label="选择平台",
                )
                keywords_input = gr.Textbox(
                    value="职场, 副业, 个人成长, 赚钱, AI, 创业, 裁员, 晋升, 自由职业, 斜杠, 收入, 技能, 学习, 管理, 领导力, 时间管理, 效率, 认知, 思维, 转型, 跳槽, 面试",
                    label="搜索关键词（逗号分隔）",
                    lines=3,
                )
                max_count = gr.Slider(
                    minimum=5, maximum=100, value=30, step=5,
                    label="每平台最大文章数",
                )
                crawl_btn = gr.Button("开始爬取", variant="primary", size="lg")

            with gr.Column():
                progress_output = gr.Textbox(
                    label="爬取进度",
                    interactive=False,
                    lines=12,
                    max_lines=30,
                )
                result_stats = gr.Textbox(
                    label="结果统计",
                    interactive=False,
                    lines=10,
                    max_lines=25,
                )

        gr.Markdown("### RAG 向量库管理")
        gr.Markdown(
            "正常情况下 RAG 会**自动增量索引**新爬取的文章，无需手动操作。\n\n"
            "- **增量重建**：清理失败的零向量 + 补齐未索引的文章（推荐，速度快）\n"
            "- **全量重建**：清空现有向量库，从头重新构建（维度变更或数据异常时使用）\n"
            "- **查重检查**：检查知识库中是否存在重复文章（标题/内容相似度）"
        )
        with gr.Row():
            incr_rebuild_btn = gr.Button("增量重建", variant="primary")
            rebuild_btn = gr.Button("全量重建", variant="stop")
            dedup_check_btn = gr.Button("查重检查", variant="secondary")
        rebuild_output = gr.Textbox(label="重建进度", interactive=False, lines=5)

        gr.Markdown("### 平台登录")
        gr.Markdown(
            "**知乎**搜索需要登录才能获取结果。点击按钮会弹出浏览器窗口，请扫码/密码登录，"
            "登录成功后自动保存 Cookie（持久化，过期前不用重复登录）。\n\n"
            "**微信**通过搜狗微信搜索公开文章，无需微信登录。"
        )
        zhihu_login_btn = gr.Button("知乎 - 登录获取 Cookie", variant="secondary")
        login_output = gr.Textbox(label="登录状态", interactive=False, lines=3)

        zhihu_login_btn.click(fn=login_zhihu, outputs=[login_output])

        rebuild_btn.click(
            fn=rebuild_vectorstore,
            outputs=[rebuild_output],
        )

        incr_rebuild_btn.click(
            fn=incremental_rebuild_vectorstore,
            outputs=[rebuild_output],
        )

        dedup_check_btn.click(
            fn=check_rag_duplicates,
            outputs=[rebuild_output],
        )

        crawl_btn.click(
            fn=run_crawler,
            inputs=[platform_check, keywords_input, max_count],
            outputs=[progress_output, result_stats],
        )

    return tab


def _do_login_platform(platform: str, url: str, cookie_domain: str) -> str:
    """通用平台登录（同步，需在线程中调用）"""
    from playwright.sync_api import sync_playwright
    from utils.http_client import get_headers

    pw = sync_playwright().start()
    browser = pw.chromium.launch(headless=False)
    context = browser.new_context(
        viewport={"width": 1280, "height": 800},
        user_agent=get_headers()["User-Agent"],
        locale="zh-CN",
    )
    page = context.new_page()

    cookies = cookie_manager.load_cookies(platform)
    if cookies:
        try:
            cookie_list = [
                {"name": c.get("name", ""), "value": c.get("value", ""),
                 "domain": c.get("domain", cookie_domain), "path": c.get("path", "/")}
                for c in cookies
            ]
            context.add_cookies(cookie_list)
        except Exception:
            pass

    page.goto(url, timeout=30000)
    page.wait_for_load_state("networkidle", timeout=15000)

    has_avatar = bool(page.query_selector("img.Avatar, [class*='Avatar'], [class*='avatar']"))

    if has_avatar:
        browser_cookies = context.cookies()
        cookie_list = [{"name": c["name"], "value": c["value"],
                        "domain": c.get("domain", ""), "path": c.get("path", "/")}
                       for c in browser_cookies]
        cookie_manager.save_cookies(platform, cookie_list)
        browser.close()
        pw.stop()
        return f"[{platform}] 检测到已登录，Cookie 已保存（{len(cookie_list)} 个）"

    for _ in range(60):
        page.wait_for_timeout(2000)
        has_avatar = bool(
            page.query_selector("img.Avatar, [class*='Avatar'], [class*='avatar']")
            or page.query_selector("[class*='GlobalSideBar'] [class*='avatar']")
        )
        if has_avatar:
            break

    browser_cookies = context.cookies()
    cookie_list = [{"name": c["name"], "value": c["value"],
                    "domain": c.get("domain", ""), "path": c.get("path", "/")}
                   for c in browser_cookies]
    cookie_manager.save_cookies(platform, cookie_list)
    browser.close()
    pw.stop()

    if has_avatar:
        return f"[{platform}] 登录成功！Cookie 已保存（{len(cookie_list)} 个），可以开始爬取了。"
    else:
        return f"[{platform}] 超时未检测到登录，Cookie 已保存（{len(cookie_list)} 个），可能需要重试。"


def login_zhihu():
    """知乎登录"""
    from crawlers.base import run_sync_in_thread
    try:
        result = run_sync_in_thread(
            _do_login_platform,
            platform="zhihu",
            url="https://www.zhihu.com/signin",
            cookie_domain=".zhihu.com",
        )
        yield result
    except Exception as e:
        yield f"[知乎] 登录失败: {e}"


def _background_crawl(platforms: list[str], keywords: list[str], max_count: int):
    """后台线程执行爬虫 + RAG 构建"""
    global _crawl_state

    try:
        with _crawl_lock:
            _crawl_state["running"] = True
            _crawl_state["done"] = False
            _crawl_state["error"] = None
            _crawl_state["start_time"] = datetime.now()
            _crawl_state["progress"] = f"正在爬取...\n平台: {', '.join(platforms)}\n关键词: {', '.join(keywords)}"

        # 执行爬虫
        pipeline = CrawlPipeline()
        context = pipeline.run(
            platforms=platforms,
            keywords=keywords,
            max_per_platform=max_count,
            parallel=True,
        )

        # 统计
        stats = {}
        content_stats = {}
        for art in context.articles:
            stats[art.source] = stats.get(art.source, 0) + 1
            if len(art.content) >= 200:
                content_stats[art.source] = content_stats.get(art.source, 0) + 1

        stat_lines = [f"本次爬取: {len(context.articles)} 篇"]
        for src, cnt in sorted(stats.items()):
            has_content = content_stats.get(src, 0)
            stat_lines.append(f"  {src}: {cnt} 篇（有正文: {has_content}）")

        from models.article_store import get_article_store
        store = get_article_store()
        total = store.count()
        with_content_total = len(store.get_with_content())
        store_stats = store.count_by_platform()
        stat_lines.append(f"\n知识库总计: {total} 篇（有正文: {with_content_total}）")
        for src, cnt in sorted(store_stats.items()):
            stat_lines.append(f"  {src}: {cnt} 篇")

        # 构建 RAG
        rag_lines = []
        try:
            rag_pipeline = RAGPipeline()
            rag_context = rag_pipeline.rebuild_incremental()
            rag_result = rag_context.get_last_result()
            rag_lines.append(f"\nRAG 构建: {rag_result.status.value} - {rag_result.message}")
        except Exception as e:
            rag_lines.append(f"\nRAG 构建失败: {e}")

        result = context.get_last_result()
        elapsed = (datetime.now() - _crawl_state["start_time"]).total_seconds()
        status_msg = f"状态: {result.status.value}\n{result.message}\n耗时: {elapsed:.0f}秒"

        with _crawl_lock:
            _crawl_state["progress"] = f"{status_msg}\n\n" + "\n".join(stat_lines) + "\n".join(rag_lines)
            _crawl_state["stats"] = "\n".join(stat_lines)
            _crawl_state["done"] = True
            _crawl_state["running"] = False

    except Exception as e:
        logger.error(f"后台爬虫异常: {e}")
        with _crawl_lock:
            _crawl_state["progress"] = f"爬取失败: {e}"
            _crawl_state["error"] = str(e)
            _crawl_state["done"] = True
            _crawl_state["running"] = False


def rebuild_vectorstore():
    """全量重建 RAG 向量库"""
    try:
        yield "正在全量重建向量库，请稍候...\n（清空现有索引 → 重新向量化所有文章）"
        pipeline = RAGPipeline()
        context = pipeline.run(force_full=True)
        result = context.get_last_result()
        elapsed_msg = result.message
        if result.status.value == "success":
            yield f"全量重建完成!\n{elapsed_msg}"
        else:
            yield f"全量重建失败: {elapsed_msg}"
    except Exception as e:
        logger.error(f"全量重建失败: {e}")
        yield f"全量重建失败: {e}"


def incremental_rebuild_vectorstore():
    """增量重建 RAG 向量库（清理零向量 + 补齐未索引文章 + 自动检测维度变更）"""
    try:
        yield "正在增量重建向量库...\n（清理零向量 → 检测维度变更 → 补齐未索引文章）"
        pipeline = RAGPipeline()
        context = pipeline.rebuild_incremental()
        result = context.get_last_result()
        if result.status.value == "success":
            yield f"增量重建完成!\n{result.message}"
        else:
            yield f"增量重建失败: {result.message}"
    except Exception as e:
        logger.error(f"增量重建失败: {e}")
        yield f"增量重建失败: {e}"


def check_rag_duplicates():
    """检查 RAG 知识库中是否存在重复文章"""
    try:
        yield "正在检查知识库中的重复文章...\n（标题 Jaccard 相似度 + 正文 SimHash）"
        pipeline = RAGPipeline()
        result = pipeline.check_rag_duplicates()
        yield result
    except Exception as e:
        logger.error(f"查重检查失败: {e}")
        yield f"查重检查失败: {e}"


def run_crawler(platforms: list[str], keywords_str: str, max_count: int):
    """启动后台爬虫（非阻塞，立即返回）"""
    global _crawl_state

    if not platforms:
        yield "请选择至少一个平台", ""
        return

    keywords = [k.strip() for k in keywords_str.split(",") if k.strip()]
    if not keywords:
        yield "请输入搜索关键词", ""
        return

    with _crawl_lock:
        if _crawl_state["running"]:
            yield "爬虫正在运行中，请等待完成后再启动", _crawl_state.get("stats", "")
            return

    # 启动后台线程
    thread = threading.Thread(
        target=_background_crawl,
        args=(platforms, keywords, int(max_count)),
        daemon=True,
    )
    thread.start()

    yield f"爬虫已启动（后台运行中）...\n平台: {', '.join(platforms)}\n关键词: {', '.join(keywords)}\n\n你可以切换到「文章生成」Tab 先生成文章，爬虫完成后 RAG 会自动更新。", ""

    # 等待完成，每2秒刷新一次状态
    import time
    while True:
        time.sleep(2)
        with _crawl_lock:
            if _crawl_state["done"]:
                yield _crawl_state["progress"], _crawl_state.get("stats", "")
                return
            yield f"（爬虫运行中...）\n\n{_crawl_state.get('progress', '')}", _crawl_state.get("stats", "")
