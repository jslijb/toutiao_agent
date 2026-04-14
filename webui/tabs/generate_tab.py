"""Tab 3: 文章生成 - 选择热点/输入主题、一键生成（含配图）、预览"""
from __future__ import annotations

import gradio as gr
from loguru import logger

from agent.content_agent import ContentAgent
from agent.tools import HotTopicTool
from config.settings import settings
from image_gen.cartoon_gen import WanxImageGenerator
from image_gen.prompt_builder import CartoonPromptBuilder
from image_gen.scene_extractor import SceneExtractor
from models.generated_store import get_generated_store
from utils.image_cache import image_cache


def create_generate_tab() -> gr.Blocks:
    """创建文章生成 Tab"""
    with gr.Blocks() as tab:
        gr.Markdown("## 文章生成")

        with gr.Row():
            with gr.Column(scale=1):
                gr.Markdown("### 热点话题")
                refresh_btn = gr.Button("获取最新热点", variant="secondary")
                hot_topics_radio = gr.Radio(
                    choices=["（点击获取热点）"],
                    value="（点击获取热点）",
                    label="选择热点",
                )
                gr.Markdown("### 或自定义主题")
                custom_topic = gr.Textbox(
                    label="自定义主题",
                    placeholder="例如：35岁后如何通过副业实现收入翻倍",
                )
                generate_btn = gr.Button("生成文章+配图", variant="primary", size="lg")

            with gr.Column(scale=2):
                gr.Markdown("### 生成结果")
                title_output = gr.Textbox(
                    label="爆款标题",
                    interactive=False,
                    lines=2,
                )
                word_count = gr.Textbox(
                    label="字数统计",
                    interactive=False,
                )
                content_output = gr.Textbox(
                    label="文章正文",
                    interactive=False,
                    lines=20,
                )

                gr.Markdown("### 卡通配图")
                with gr.Row():
                    img1 = gr.Image(label="配图1", height=250)
                    img2 = gr.Image(label="配图2", height=250)
                with gr.Row():
                    img3 = gr.Image(label="配图3", height=250)
                    img4 = gr.Image(label="配图4", height=250)

                gen_status = gr.Textbox(label="生成状态", interactive=False)

        refresh_btn.click(
            fn=refresh_hot_topics,
            outputs=[hot_topics_radio],
        )

        generate_btn.click(
            fn=generate_article,
            inputs=[hot_topics_radio, custom_topic],
            outputs=[title_output, word_count, content_output,
                     img1, img2, img3, img4, gen_status],
        )

    return tab


# 缓存最新文章和话题（供图片Tab和发布Tab使用）
_latest_article = None
_latest_topics = None


def refresh_hot_topics():
    global _latest_topics
    try:
        tool = HotTopicTool()
        topics = tool.fetch_hot_topics(max_topics=15)
        _latest_topics = topics
        choices = [f"{t['title']} (热度:{t.get('heat', '?')})" for t in topics]
        if not choices:
            return ["无可用热点"]
        return gr.update(choices=choices, value=choices[0])
    except Exception as e:
        logger.error(f"获取热点失败: {e}")
        return [f"获取失败: {e}"]


def generate_article(hot_topic: str, custom_topic: str):
    """生成文章 + 自动生成配图（优先使用缓存）"""
    global _latest_article

    topic = custom_topic.strip() if custom_topic.strip() else ""
    if not topic and hot_topic:
        # 兼容英文括号和中文括号: "标题 (热度:xxx)" 或 "标题（热度:xxx）"
        for sep in [" (", "（"]:
            if sep in hot_topic:
                topic = hot_topic.split(sep)[0].strip()
                break
        else:
            topic = hot_topic.strip()

    # 过滤无效默认值
    if topic in ("（点击获取热点）", "无可用热点", ""):
        topic = ""

    logger.info(f"[生成] hot_topic={hot_topic!r}, custom_topic={custom_topic!r}, topic={topic!r}")

    if not topic:
        yield "请选择热点或输入自定义主题", "", "", None, None, None, None, ""
        return

    # Phase 1: 生成文章
    yield "正在生成文章...", "", "", None, None, None, None, "Step 1/2: 生成文章中..."

    try:
        agent = ContentAgent()
        article = agent.generate_article(
            hot_topic=topic if not custom_topic.strip() else "",
            custom_topic=custom_topic.strip(),
        )
        _latest_article = article
    except Exception as e:
        logger.error(f"文章生成失败: {e}")
        yield f"生成失败: {e}", "", "", None, None, None, None, f"文章生成失败: {e}"
        return

    # 保存到持久化存储
    try:
        store = get_generated_store()
        store.add(article)
        logger.info(f"[生成] 文章已缓存: {article.id}")
    except Exception as e:
        logger.warning(f"[生成] 文章缓存保存失败: {e}")

    # 先输出文章内容
    yield (
        article.title,
        f"字数: {article.word_count} | 热点: {article.hot_topic}",
        article.content,
        None, None, None, None,
        "Step 2/2: 生成配图中...",
    )

    # Phase 2: 生成配图（优先使用缓存）
    try:
        # 检查缓存
        cached = image_cache.get(article.id)
        if cached and len(cached.get("image_paths", [])) >= 4:
            logger.info(f"[生成] 使用已缓存的配图: {article.id}")
            article.scenes = cached["scenes"]
            article.image_paths = cached["image_paths"]
            yield (
                article.title,
                f"字数: {article.word_count} | 热点: {article.hot_topic}",
                article.content,
                *article.image_paths[:4],
                "配图已从缓存加载",
            )
            return

        # 需要生成配图
        scenes = article.scenes
        if not scenes or len(scenes) < 4:
            extractor = SceneExtractor()
            scenes = extractor.extract(article.content, n=4)

        while len(scenes) < 4:
            scenes.append("职场成长主题，积极向上的卡通插画")

        article.scenes = scenes

        # 按文章 ID 创建输出目录
        from pathlib import Path
        from config.settings import PROJECT_ROOT
        article_img_dir = PROJECT_ROOT / "output" / "images" / article.id
        article_img_dir.mkdir(parents=True, exist_ok=True)

        generator = WanxImageGenerator(output_dir=article_img_dir)
        prompt_builder = CartoonPromptBuilder()

        all_paths = []
        for i, scene in enumerate(scenes):
            prompt = prompt_builder.build(scene)
            logger.info(f"[生成] 配图 {i+1}/4: {scene[:30]}...")

            paths = generator.generate(prompt=prompt, n=1)
            if paths:
                all_paths.append(paths[0])
                logger.info(f"[生成] 配图 {i+1} 完成: {paths[0]}")
            else:
                all_paths.append(None)
                logger.warning(f"[生成] 配图 {i+1} 失败")

            # 逐张更新 UI
            current_paths = all_paths + [None] * (4 - len(all_paths))
            yield (
                article.title,
                f"字数: {article.word_count} | 热点: {article.hot_topic}",
                article.content,
                *current_paths[:4],
                f"配图生成中 {i+1}/4...",
            )

        # 补齐到 4 张
        while len(all_paths) < 4:
            all_paths.append(None)

        article.image_paths = [p for p in all_paths if p]

        # 保存缓存
        image_cache.save(article.id, article.scenes, article.image_paths)

        n_success = sum(1 for p in all_paths if p)
        status_msg = f"生成完成: 文章 + {n_success}/4 张配图"

        # 自动发布
        if settings.publisher.auto_publish:
            try:
                from publisher.toutiao_publisher import get_toutiao_publisher
                publisher = get_toutiao_publisher()
                img_paths = [p for p in all_paths[:4] if p]

                # 从 hot_topic 生成动态话题
                dynamic_topics = []
                if article.hot_topic:
                    for t in article.hot_topic.replace("，", ",").split(","):
                        t = t.strip()
                        if t and len(dynamic_topics) < 2:
                            dynamic_topics.append(t)

                if settings.publisher.publish_type == "micro_toutiao":
                    result = publisher.publish_micro_toutiao(
                        content=article.content,
                        image_paths=img_paths,
                        topics=dynamic_topics,
                        location=settings.publisher.default_location,
                    )
                else:
                    result = publisher.publish_article(
                        title=article.title,
                        content=article.content,
                        image_paths=img_paths,
                        category=settings.publisher.default_category,
                    )
                if result.success:
                    get_generated_store().update(article.id, status="published", published_at=result.published_at)
                    status_msg += " | ✅ 已自动发布"
                else:
                    status_msg += f" | ❌ 自动发布失败: {result.error}"
            except Exception as e:
                status_msg += f" | ⚠️ 自动发布异常: {e}"

        yield (
            article.title,
            f"字数: {article.word_count} | 热点: {article.hot_topic}",
            article.content,
            *all_paths[:4],
            status_msg,
        )

    except Exception as e:
        logger.error(f"配图生成失败: {e}")
        yield (
            article.title,
            f"字数: {article.word_count} | 热点: {article.hot_topic}",
            article.content,
            None, None, None, None,
            f"配图生成失败: {e}（文章已生成）",
        )


def get_latest_article():
    """供其他 Tab 获取最新生成的文章（优先从内存缓存，其次从持久化存储）"""
    if _latest_article:
        return _latest_article
    # 从持久化存储加载最新一篇
    try:
        store = get_generated_store()
        articles = store.get_all()
        if articles:
            return articles[0]
    except Exception:
        pass
    return None
