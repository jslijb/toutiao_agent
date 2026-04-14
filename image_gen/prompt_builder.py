"""卡通图片 Prompt 构建器"""
from __future__ import annotations

from config.settings import settings


class CartoonPromptBuilder:
    """统一的 3D 卡通风格图片 Prompt 构建"""

    STYLE_PREFIX = "3D卡通风格, 明亮色彩, 扁平插画, 简洁背景, 高质量, 精美细节"

    THEME_KEYWORDS = "职场/副业/个人成长主题"

    def __init__(self, style: str | None = None):
        self.style = style or settings.models.image_gen.style

    def build(self, scene_description: str) -> str:
        """构建图片生成 Prompt"""
        prompt = f"{self.STYLE_PREFIX}, {scene_description}, {self.THEME_KEYWORDS}"
        return prompt

    def build_negative_prompt(self) -> str:
        """构建反向 Prompt（排除不想要的内容）"""
        return "模糊, 低质量, 变形, 丑陋, 暴力, 色情, 文字水印, 复杂背景"
