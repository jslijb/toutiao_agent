"""Tab 4: 图片预览 - 查看/重新生成卡通配图（自动加载缓存）"""
from __future__ import annotations

import gradio as gr
from loguru import logger

from services.image_regen_service import regen_service
from utils.image_cache import image_cache
from webui.tabs.generate_tab import get_latest_article


def create_image_tab() -> gr.Blocks:
    """创建图片预览 Tab"""
    with gr.Blocks() as tab:
        gr.Markdown("## 卡通配图")

        with gr.Row():
            load_btn = gr.Button("加载已缓存配图", variant="secondary")
            regen_all_btn = gr.Button("一键重新生成配图", variant="primary")
            regen_failed_btn = gr.Button("重新生成失败配图", variant="primary", visible=False)

        with gr.Row():
            scene1 = gr.Textbox(label="场景1", interactive=False)
            scene2 = gr.Textbox(label="场景2", interactive=False)
            scene3 = gr.Textbox(label="场景3", interactive=False)
            scene4 = gr.Textbox(label="场景4", interactive=False)

        with gr.Row():
            img1 = gr.Image(label="配图1", height=300)
            img2 = gr.Image(label="配图2", height=300)
            img3 = gr.Image(label="配图3", height=300)
            img4 = gr.Image(label="配图4", height=300)

        status_text = gr.Textbox(label="状态", interactive=False)

        load_btn.click(
            fn=load_cached_images,
            outputs=[scene1, scene2, scene3, scene4, img1, img2, img3, img4, status_text, regen_failed_btn],
        )
        regen_all_btn.click(
            fn=on_regen_all,
            outputs=[scene1, scene2, scene3, scene4, img1, img2, img3, img4, status_text, regen_failed_btn],
        )
        regen_failed_btn.click(
            fn=on_regen_failed,
            outputs=[scene1, scene2, scene3, scene4, img1, img2, img3, img4, status_text, regen_failed_btn],
        )

    return tab


def load_cached_images():
    """加载已缓存的配图"""
    article = get_latest_article()
    if not article:
        yield "无", "无", "无", "无", None, None, None, None, "请先在「文章生成」Tab 中生成文章", gr.update(visible=False)
        return

    cached = image_cache.get(article.id)
    if not cached or len(cached.get("image_paths", [])) == 0:
        yield "无", "无", "无", "无", None, None, None, None, "无缓存配图，请点击「一键重新生成配图」", gr.update(visible=False)
        return

    scenes = cached.get("scenes", [""] * 4)
    while len(scenes) < 4:
        scenes.append("")
    paths = cached.get("image_paths", [])
    while len(paths) < 4:
        paths.append(None)

    padded_paths = list(paths[:4]) + [None] * (4 - len(paths[:4]))
    failure_status = regen_service.detect_failure_status(padded_paths)
    regen_visible = failure_status.failure_type != "none"

    yield scenes[0], scenes[1], scenes[2], scenes[3], *paths[:4], f"已加载 {sum(1 for p in paths if p)} 张缓存配图", gr.update(visible=regen_visible)


def _get_scenes_display(article) -> tuple[str, str, str, str]:
    scenes = regen_service.get_scenes_with_fallback(article, n=4)
    while len(scenes) < 4:
        scenes.append("")
    return scenes[0], scenes[1], scenes[2], scenes[3]


def on_regen_all():
    """一键重新生成全部配图"""
    article = get_latest_article()
    if not article:
        yield "无", "无", "无", "无", None, None, None, None, "请先在「文章生成」Tab 中生成文章", gr.update(visible=False)
        return

    s1, s2, s3, s4 = _get_scenes_display(article)

    for paths, status_msg in regen_service.regenerate_article_images(article, scope="all"):
        failure_status = regen_service.detect_failure_status(paths)
        regen_visible = failure_status.failure_type != "none"
        yield s1, s2, s3, s4, *paths[:4], status_msg, gr.update(visible=regen_visible)


def on_regen_failed():
    """重新生成失败配图"""
    article = get_latest_article()
    if not article:
        yield "无", "无", "无", "无", None, None, None, None, "请先在「文章生成」Tab 中生成文章", gr.update(visible=False)
        return

    s1, s2, s3, s4 = _get_scenes_display(article)

    for paths, status_msg in regen_service.regenerate_article_images(article, scope="failed_only"):
        failure_status = regen_service.detect_failure_status(paths)
        regen_visible = failure_status.failure_type != "none"
        yield s1, s2, s3, s4, *paths[:4], status_msg, gr.update(visible=regen_visible)
