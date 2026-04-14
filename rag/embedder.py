"""DashScope Embedding 调用封装"""
from __future__ import annotations

import time
from typing import Optional

import numpy as np
from dashscope import TextEmbedding
from loguru import logger

from config.settings import settings


class DashScopeEmbedder:
    """DashScope 文本嵌入封装"""

    def __init__(
        self,
        model_name: Optional[str] = None,
        dimension: Optional[int] = None,
        batch_size: Optional[int] = None,
        batch_interval: Optional[float] = None,
    ):
        cfg = settings.models.embedding
        self.model_name = model_name or cfg.name
        self.dimension = dimension or cfg.dimension
        self.batch_size = batch_size or cfg.batch_size
        self.batch_interval = batch_interval or cfg.batch_interval

    def embed_texts(self, texts: list[str]) -> np.ndarray:
        """批量文本嵌入，返回 numpy 数组 (n, dimension)"""
        if not texts:
            return np.array([])

        all_embeddings = []
        total = len(texts)

        for i in range(0, total, self.batch_size):
            batch = texts[i : i + self.batch_size]
            logger.debug(f"Embedding 批次 {i // self.batch_size + 1}: {len(batch)} 条")

            try:
                # text-embedding-v1 不支持 dimension 参数，v3/v4 支持
                kwargs = {
                    "model": self.model_name,
                    "input": batch,
                }
                # v1/v2/async-v1/async-v2 不支持 dimension 参数，v3/v4 支持
                if not self.model_name.endswith(("-v1", "-v2")):
                    kwargs["dimension"] = self.dimension

                resp = TextEmbedding.call(**kwargs)

                if resp.status_code == 200:
                    batch_embeddings = [item["embedding"] for item in resp.output["embeddings"]]
                    all_embeddings.extend(batch_embeddings)
                    # 更新实际维度（v1/v2 可能返回不同维度）
                    if not hasattr(self, '_actual_dim') and batch_embeddings:
                        self._actual_dim = len(batch_embeddings[0])
                else:
                    error_msg = resp.message or ""
                    # 403 = 免费额度用完，直接抛异常而不是静默填零向量
                    if resp.status_code == 403 or "exhausted" in error_msg.lower():
                        raise RuntimeError(
                            f"Embedding 模型 {self.model_name} 免费额度已用完！"
                            f"请在 models.yaml 中切换到有额度的模型（如 text-embedding-v3/v4），"
                            f"或在阿里云百炼控制台关闭「仅使用免费额度」模式。"
                        )
                    logger.error(f"Embedding API 错误: {resp.status_code} - {error_msg}")
                    # 其他错误用零向量填充
                    all_embeddings.extend([[0.0] * self.dimension] * len(batch))

            except Exception as e:
                logger.error(f"Embedding 请求异常: {e}")
                all_embeddings.extend([[0.0] * self.dimension] * len(batch))

            # 批次间隔（限流）
            if i + self.batch_size < total:
                time.sleep(self.batch_interval)

        # 如果实际维度与配置不同，更新 dimension
        if hasattr(self, '_actual_dim') and self._actual_dim != self.dimension:
            logger.info(f"Embedding 实际维度: {self._actual_dim} (配置: {self.dimension})")
            self.dimension = self._actual_dim

        return np.array(all_embeddings, dtype=np.float32)

    def embed_query(self, text: str) -> np.ndarray:
        """单条文本嵌入（用于查询）"""
        result = self.embed_texts([text])
        return result[0] if len(result) > 0 else np.zeros(self.dimension, dtype=np.float32)
