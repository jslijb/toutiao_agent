"""Tab 4: 图片预览 - 查看/重新生成卡通配图（自动加载缓存）"""
from __future__ import annotations

from pathlib import Path

import gradio as gr
from loguru import logger

from image_gen.cartoon_gen import WanxImageGenerator
from image_gen.prompt_builder import CartoonPromptBuilder
from image_gen.scene_extractor import SceneExtractor
from utils.image_cache import image_cache
from webui.tabs.generate_tab import get_latest_article


def create_image_tab() -> gr.Blocks:
    """创建图片预览 Tab"""
    with gr.Blocks() as tab:
        gr.Markdown("## 卡通配图")

        with gr.Row():
            load_btn = gr.Button("加载已缓存配图", variant="secondary")
            regen_btn = gr.Button("重新生成全部", variant="primary")

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
            outputs=[scene1, scene2, scene3, scene4, img1, img2, img3, img4, status_text],
        )
        regen_btn.click(
            fn=regenerate_images,
            outputs=[scene1, scene2, scene3, scene4, img1, img2, img3, img4, status_text],
        )

    return tab


def load_cached_images():
    """加载已缓存的配图"""
    article = get_latest_article()
    if not article:
        yield "无", "无", "无", "无", None, None, None, None, "请先在「文章生成」Tab 中生成文章"
        return

    cached = image_cache.get(article.id)
    if not cached or len(cached.get("image_paths", [])) == 0:
        yield "无", "无", "无", "无", None, None, None, None, "无缓存配图，请点击「重新生成全部」"
        return

    scenes = cached.get("scenes", [""] * 4)
    while len(scenes) < 4:
        scenes.append("")
    paths = cached.get("image_paths", [])
    while len(paths) < 4:
        paths.append(None)

    yield scenes[0], scenes[1], scenes[2], scenes[3], *paths[:4], f"已加载 {sum(1 for p in paths if p)} 张缓存配图"


def regenerate_images():
    """重新生成4张卡通配图（清除旧缓存）"""
    article = get_latest_article()
    if not article:
        yield "无", "无", "无", "无", None, None, None, None, "请先在「文章生成」Tab 中生成文章"
        return

    # 清除旧缓存
    image_cache.clear(article.id)

    scenes = article.scenes
    if not scenes or len(scenes) < 4:
        extractor = SceneExtractor()
        scenes = extractor.extract(article.content, n=4)

    while len(scenes) < 4:
        scenes.append("职场成长主题，积极向上的卡通插画")

    article.scenes = scenes

    yield scenes[0], scenes[1], scenes[2], scenes[3], None, None, None, None, "正在重新生成配图..."

    try:
        article_img_dir = Path("output") / "images" / article.id
        article_img_dir.mkdir(parents=True, exist_ok=True)

        generator = WanxImageGenerator(output_dir=article_img_dir)
        prompt_builder = CartoonPromptBuilder()

        all_paths = []
        for i, scene in enumerate(scenes):
            prompt = prompt_builder.build(scene)
            logger.info(f"[图片] 重新生成 {i+1}/4: {scene[:30]}...")

            paths = generator.generate(prompt=prompt, n=1)
            if paths:
                all_paths.append(paths[0])
            else:
                all_paths.append(None)

            current_paths = all_paths + [None] * (4 - len(all_paths))
            yield scenes[0], scenes[1], scenes[2], scenes[3], *current_paths[:4], f"重新生成中 {i+1}/4..."

        while len(all_paths) < 4:
            all_paths.append(None)

        article.image_paths = [p for p in all_paths if p]
        image_cache.save(article.id, article.scenes, article.image_paths)

        n_success = sum(1 for p in all_paths if p)
        yield scenes[0], scenes[1], scenes[2], scenes[3], *all_paths[:4], f"重新生成完成: {n_success}/4 张"

    except Exception as e:
        logger.error(f"图片重新生成失败: {e}")
        yield scenes[0], scenes[1], scenes[2], scenes[3], None, None, None, None, f"重新生成失败: {e}"
