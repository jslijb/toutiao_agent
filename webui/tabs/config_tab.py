"""Tab 1: 配置管理 - 查看/修改模型配置"""
from __future__ import annotations

import gradio as gr
import yaml
from loguru import logger

from config.settings import settings, PROJECT_ROOT


def create_config_tab() -> gr.Blocks:
    """创建配置管理 Tab"""
    yaml_path = PROJECT_ROOT / "config" / "models.yaml"

    with gr.Blocks() as tab:
        gr.Markdown("## 模型配置管理")

        with gr.Row():
            with gr.Column(scale=3):
                config_display = gr.Code(
                    value=_read_yaml(),
                    language="yaml",
                    label="config/models.yaml",
                    interactive=True,
                    lines=20,
                )
            with gr.Column(scale=1):
                gr.Markdown("### 当前生效配置")
                llm_info = gr.Textbox(
                    value=f"LLM: {settings.models.llm.name}",
                    label="大语言模型",
                    interactive=False,
                )
                embed_info = gr.Textbox(
                    value=f"Embedding: {settings.models.embedding.name} ({settings.models.embedding.dimension}d)",
                    label="嵌入模型",
                    interactive=False,
                )
                img_info = gr.Textbox(
                    value=f"图片: {settings.models.image_gen.name} ({settings.models.image_gen.style})",
                    label="图片模型",
                    interactive=False,
                )

        with gr.Row():
            save_btn = gr.Button("保存配置", variant="primary")
            reload_btn = gr.Button("重新加载", variant="secondary")
            status_text = gr.Textbox(label="状态", interactive=False)

        save_btn.click(
            fn=_save_yaml,
            inputs=[config_display],
            outputs=[status_text, llm_info, embed_info, img_info],
        )
        reload_btn.click(
            fn=_reload_config,
            outputs=[config_display, llm_info, embed_info, img_info, status_text],
        )

    return tab


def _read_yaml() -> str:
    yaml_path = PROJECT_ROOT / "config" / "models.yaml"
    if yaml_path.exists():
        return yaml_path.read_text(encoding="utf-8")
    return "# 配置文件不存在"


def _save_yaml(content: str):
    try:
        yaml.safe_load(content)  # 校验 YAML 格式
        yaml_path = PROJECT_ROOT / "config" / "models.yaml"
        yaml_path.write_text(content, encoding="utf-8")
        settings.reload()
        logger.info("配置已保存并重新加载")
        return "保存成功！", *_current_info()
    except Exception as e:
        return f"保存失败: {e}", *_current_info()


def _reload_config():
    settings.reload()
    return _read_yaml(), *_current_info(), "配置已重新加载"


def _current_info():
    return (
        f"LLM: {settings.models.llm.name}",
        f"Embedding: {settings.models.embedding.name} ({settings.models.embedding.dimension}d)",
        f"图片: {settings.models.image_gen.name} ({settings.models.image_gen.style})",
    )
