"""Tab 5: 自动发布 - 登录管理 + 一键发布 + 发布历史"""
from __future__ import annotations

import gradio as gr
from loguru import logger

from config.settings import settings
from models.generated_store import get_generated_store
from publisher.toutiao_publisher import get_toutiao_publisher, close_publisher
from utils.image_cache import image_cache
from webui.tabs.generate_tab import get_latest_article

# JS 剪贴板复制脚本
_COPY_JS = """
(text) => {
    if (!text) return '暂无内容可复制';
    navigator.clipboard.writeText(text).then(() => {}).catch(() => {
        const ta = document.createElement('textarea');
        ta.value = text;
        ta.style.position = 'fixed';
        ta.style.opacity = '0';
        document.body.appendChild(ta);
        ta.select();
        document.execCommand('copy');
        document.body.removeChild(ta);
    });
    return '已复制到剪贴板！';
}
"""


def create_publish_tab() -> gr.Blocks:
    """创建发布 Tab"""
    with gr.Blocks() as tab:
        gr.Markdown("## 自动发布")

        # ── 登录管理 ─────────────────────────────
        with gr.Accordion("账号登录", open=True):
            with gr.Row():
                login_btn = gr.Button("登录头条号", variant="primary")
                check_login_btn = gr.Button("检查登录状态", variant="secondary")
                logout_btn = gr.Button("退出登录", variant="stop")
            login_status = gr.Textbox(label="登录状态", interactive=False)
            gr.Markdown(
                "点击「登录头条号」后会弹出浏览器窗口，"
                "系统会先加载已保存的 Cookie 尝试自动登录，如果 Cookie 有效则直接登录成功。"
                "仅当 Cookie 过期时才需要**扫码登录**。"
                "登录成功后 Cookie 会持久保存，下次自动登录。"
            )

        # ── 发布设置 ─────────────────────────────
        with gr.Accordion("发布设置", open=True):
            with gr.Row():
                publish_type = gr.Radio(
                    choices=["article", "micro_toutiao"],
                    value=settings.publisher.publish_type,
                    label="发布类型",
                )
                category = gr.Textbox(
                    label="文章分类",
                    value=settings.publisher.default_category,
                    placeholder="职场、科技、财经、教育、生活等",
                )
            auto_publish = gr.Checkbox(
                label="文章生成后自动发布",
                value=settings.publisher.auto_publish,
            )

        # ── 文章选择 & 预览 ────────────────────────
        with gr.Accordion("文章预览", open=True):
            with gr.Row():
                refresh_btn = gr.Button("刷新预览", variant="secondary")
                copy_title_btn = gr.Button("复制标题", variant="secondary")
                copy_content_btn = gr.Button("复制正文", variant="secondary")
                copy_all_btn = gr.Button("复制全部", variant="secondary")

            article_selector = gr.Dropdown(
                label="选择文章",
                choices=[],
                value=None,
                interactive=True,
            )

            with gr.Row():
                with gr.Column(scale=2):
                    title_display = gr.Textbox(
                        label="标题",
                        interactive=False,
                        lines=2,
                    )
                    final_content = gr.Textbox(
                        label="正文",
                        interactive=False,
                        lines=20,
                        max_lines=50,
                    )
                with gr.Column(scale=1):
                    info_display = gr.Textbox(
                        label="文章信息",
                        interactive=False,
                        lines=8,
                    )
                    image_gallery = gr.Gallery(
                        label="配图预览",
                        height=400,
                    )

        # ── 发布操作 ─────────────────────────────
        with gr.Row():
            publish_btn = gr.Button("一键发布到头条", variant="primary", size="lg")
            batch_publish_btn = gr.Button("批量发布草稿", variant="secondary")

        publish_result = gr.Textbox(label="发布结果", interactive=False, lines=3)

        # ── 发布历史 ─────────────────────────────
        with gr.Accordion("发布历史", open=False):
            history_refresh_btn = gr.Button("刷新历史", variant="secondary")
            history_display = gr.Dataframe(
                headers=["标题", "状态", "发布时间", "类型"],
                label="文章列表",
                interactive=False,
            )

        # 隐藏状态组件
        _copy_text = gr.Textbox(visible=False)
        copy_status = gr.Textbox(label="操作状态", interactive=False)
        _selected_article_id = gr.Textbox(visible=False)

        # ── 事件绑定 ─────────────────────────────

        # 登录
        login_btn.click(fn=do_login, outputs=[login_status])
        check_login_btn.click(fn=check_login, outputs=[login_status])
        logout_btn.click(fn=do_logout, outputs=[login_status])

        # 文章选择
        article_selector.change(
            fn=select_article,
            inputs=[article_selector],
            outputs=[title_display, final_content, info_display, image_gallery, _selected_article_id],
        )

        # 刷新
        refresh_btn.click(
            fn=refresh_article_list,
            outputs=[article_selector],
        ).then(
            fn=select_article,
            inputs=[article_selector],
            outputs=[title_display, final_content, info_display, image_gallery, _selected_article_id],
        )

        # 复制
        copy_title_btn.click(fn=_get_title_text, outputs=[_copy_text]).then(
            fn=None, inputs=[_copy_text], outputs=[copy_status], js=_COPY_JS,
        )
        copy_content_btn.click(fn=_get_content_text, outputs=[_copy_text]).then(
            fn=None, inputs=[_copy_text], outputs=[copy_status], js=_COPY_JS,
        )
        copy_all_btn.click(fn=_get_all_text, outputs=[_copy_text]).then(
            fn=None, inputs=[_copy_text], outputs=[copy_status], js=_COPY_JS,
        )

        # 发布
        publish_btn.click(
            fn=do_publish,
            inputs=[_selected_article_id, publish_type, category],
            outputs=[publish_result],
        )
        batch_publish_btn.click(
            fn=do_batch_publish,
            inputs=[category],
            outputs=[publish_result],
        )

        # 历史
        history_refresh_btn.click(fn=refresh_history, outputs=[history_display])

        # 自动发布设置
        auto_publish.change(fn=toggle_auto_publish, inputs=[auto_publish])

    return tab


# ── 全局状态 ──────────────────────────────────────
_current_article_id = None


def _get_article(article_id: str = None):
    """获取指定文章或最新文章"""
    if article_id:
        store = get_generated_store()
        return store.get(article_id)
    return get_latest_article()


# ── 登录管理 ──────────────────────────────────────

def do_login():
    """扫码登录"""
    try:
        # 强制非无头模式，显示浏览器窗口供用户扫码/输入
        from publisher.toutiao_publisher import ToutiaoPublisher
        publisher = ToutiaoPublisher(headless=False)
        success = publisher.login()
        if success:
            return "✅ 登录成功！Cookie 已保存，后续无需重复登录"
        else:
            return "❌ 登录超时或失败，请重试"
    except Exception as e:
        logger.error(f"登录失败：{e}")
        return f"❌ 登录失败：{e}"


def check_login():
    """检查登录状态"""
    try:
        publisher = get_toutiao_publisher()

        # 轻量检查（如果浏览器在同一线程且已启动）
        try:
            if publisher.is_logged_in():
                return "✅ 已登录，可以发布文章"
        except Exception:
            pass  # greenlet 线程错误，走深度验证

        # 深度验证（会重建浏览器 + 加载 Cookie + 访问创作者后台确认）
        if publisher.verify_login():
            return "✅ 已登录，可以发布文章"

        return "❌ 未登录或登录已过期，请点击登录"
    except Exception as e:
        return f"⚠️ 检查失败: {e}"


def do_logout():
    """退出登录（清除 Cookie）"""
    try:
        from publisher.toutiao_publisher import _COOKIE_FILE
        close_publisher()
        if _COOKIE_FILE.exists():
            _COOKIE_FILE.unlink()
        return "✅ 已退出登录，Cookie 已清除"
    except Exception as e:
        return f"⚠️ 退出失败: {e}"


# ── 文章预览 ──────────────────────────────────────

def refresh_article_list():
    """刷新文章下拉列表"""
    try:
        store = get_generated_store()
        articles = store.get_all()
        choices = [
            (f"{a.title[:40]}... [{a.status}]", a.id)
            for a in articles
        ]
        if not choices:
            return gr.update(choices=[], value=None)
        return gr.update(choices=choices, value=choices[0][1])
    except Exception as e:
        logger.error(f"刷新文章列表失败: {e}")
        return gr.update(choices=[], value=None)


def on_page_load():
    """页面加载时一次性初始化：刷新文章列表 + 选中首篇 + 加载发布历史

    将多个操作合并为一个函数，避免 .then 链式调用导致前端阻塞。
    """
    try:
        # 1. 刷新文章列表
        selector_update = refresh_article_list()

        # 2. 获取第一篇文章的 ID
        store = get_generated_store()
        articles = store.get_all()
        first_id = articles[0].id if articles else None

        # 3. 加载文章详情
        if first_id:
            title, content, info, images, aid = select_article(first_id)
        else:
            title, content, info, images, aid = "", "", "暂无文章", [], ""

        # 4. 加载发布历史
        history = refresh_history()

        return selector_update, title, content, info, images, aid, history
    except Exception as e:
        logger.error(f"页面初始化失败: {e}")
        return (
            gr.update(choices=[], value=None),
            "", "", f"初始化失败: {e}", [], "", [],
        )


def select_article(article_id):
    """选择文章"""
    global _current_article_id
    _current_article_id = article_id
    article = _get_article(article_id)
    if not article:
        return "未选择文章", "", "请选择一篇文章", [], ""

    # 从缓存加载图片
    image_paths = article.image_paths
    cached = image_cache.get(article.id)
    if cached and cached.get("image_paths"):
        image_paths = cached["image_paths"]

    content = f"{article.title}\n\n{article.content}"
    info_lines = [
        f"标题: {article.title}",
        f"字数: {article.word_count}",
        f"热点: {article.hot_topic}",
        f"状态: {article.status}",
        f"类型: {article.article_type}",
        f"配图数: {len(image_paths)}",
        f"文章ID: {article.id}",
        f"创建时间: {article.created_at}",
    ]

    images = []
    for path in image_paths:
        if path:
            from pathlib import Path
            if Path(path).exists():
                images.append(path)

    return article.title, content, "\n".join(info_lines), images, article.id


def refresh_publish():
    """刷新预览"""
    article = get_latest_article()
    if not article:
        return "暂无文章", "", "请先生成文章", [], ""

    image_paths = article.image_paths
    cached = image_cache.get(article.id)
    if cached and cached.get("image_paths"):
        image_paths = cached["image_paths"]

    content = f"{article.title}\n\n{article.content}"
    info_lines = [
        f"标题: {article.title}",
        f"字数: {article.word_count}",
        f"热点: {article.hot_topic}",
        f"状态: {article.status}",
        f"类型: {article.article_type}",
        f"配图数: {len(image_paths)}",
        f"文章ID: {article.id}",
        f"创建时间: {article.created_at}",
    ]

    images = []
    for path in image_paths:
        if path:
            from pathlib import Path
            if Path(path).exists():
                images.append(path)

    return article.title, content, "\n".join(info_lines), images, article.id


# ── 复制功能 ──────────────────────────────────────

def _get_title_text() -> str:
    article = _get_article(_current_article_id)
    return article.title if article and article.title else ""


def _get_content_text() -> str:
    article = _get_article(_current_article_id)
    return article.content if article and article.content else ""


def _get_all_text() -> str:
    article = _get_article(_current_article_id)
    if not article:
        return ""
    return f"{article.title}\n\n{article.content}"


# ── 发布操作 ──────────────────────────────────────

def do_publish(article_id: str, publish_type: str, category: str):
    """一键发布到头条"""
    if not article_id:
        return "❌ 请先选择文章"

    article = _get_article(article_id)
    if not article:
        return "❌ 文章不存在"

    try:
        publisher = get_toutiao_publisher()

        # 获取配图路径
        image_paths = article.image_paths
        cached = image_cache.get(article.id)
        if cached and cached.get("image_paths"):
            image_paths = cached["image_paths"]

        if publish_type == "micro_toutiao":
            # 从 hot_topic 生成动态话题（最多2个）
            dynamic_topics = []
            if article.hot_topic:
                # hot_topic 格式可能是 "主题1，主题2，主题3" 或 "主题1,主题2"
                for t in article.hot_topic.replace("，", ",").split(","):
                    t = t.strip()
                    if t and len(dynamic_topics) < 2:
                        dynamic_topics.append(t)

            result = publisher.publish_micro_toutiao(
                content=article.content,
                image_paths=image_paths,
                topics=dynamic_topics,
                location=getattr(settings.publisher, "default_location", ""),
            )
        else:
            result = publisher.publish_article(
                title=article.title,
                content=article.content,
                image_paths=image_paths,
                category=category or settings.publisher.default_category,
            )

        # 更新文章状态
        if result.success:
            store = get_generated_store()
            store.update(
                article_id,
                status="published",
                published_at=result.published_at,
                published_url=result.published_url,
            )
            return f"✅ 发布成功！{result.message}"
        else:
            return f"❌ 发布失败: {result.error}"

    except Exception as e:
        logger.error(f"发布失败: {e}")
        return f"❌ 发布异常: {e}"


def do_batch_publish(category: str):
    """批量发布草稿"""
    try:
        store = get_generated_store()
        drafts = store.get_drafts()
        if not drafts:
            return "没有待发布的草稿"

        publisher = get_toutiao_publisher()
        results = []
        for article in drafts[:5]:  # 每次最多5篇
            image_paths = article.image_paths
            cached = image_cache.get(article.id)
            if cached and cached.get("image_paths"):
                image_paths = cached["image_paths"]

            result = publisher.publish_article(
                title=article.title,
                content=article.content,
                image_paths=image_paths,
                category=category or settings.publisher.default_category,
            )

            if result.success:
                store.update(
                    article.id,
                    status="published",
                    published_at=result.published_at,
                    published_url=result.published_url,
                )
                results.append(f"✅ {article.title[:20]}: 发布成功")
            else:
                results.append(f"❌ {article.title[:20]}: {result.error}")

            # 发布间隔，避免触发风控
            import time
            time.sleep(5)

        return "\n".join(results)
    except Exception as e:
        return f"❌ 批量发布失败: {e}"


# ── 发布历史 ──────────────────────────────────────

def refresh_history():
    """刷新发布历史"""
    try:
        store = get_generated_store()
        articles = store.get_all()
        rows = []
        for a in articles[:20]:
            rows.append([
                a.title[:30],
                a.status,
                a.published_at or a.created_at,
                a.article_type,
            ])
        return rows
    except Exception:
        return []


# ── 设置 ──────────────────────────────────────────

def toggle_auto_publish(enabled: bool):
    """切换自动发布设置"""
    try:
        # 更新运行时配置
        settings.publisher.auto_publish = enabled
        status = "开启" if enabled else "关闭"
        logger.info(f"[Publisher] 自动发布已{status}")
    except Exception as e:
        logger.warning(f"切换自动发布设置失败: {e}")
