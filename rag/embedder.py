"""Embedding 调用封装 - 支持 DashScope 云端和本地模型"""
from __future__ import annotations

import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional

import numpy as np
from loguru import logger

from config.settings import PROJECT_ROOT, settings


class BaseEmbedder(ABC):
    """Embedder 抽象基类，定义统一接口"""

    @property
    @abstractmethod
    def dimension(self) -> int:
        """当前 Embedder 输出的向量维度"""

    @abstractmethod
    def embed_texts(self, texts: list[str]) -> np.ndarray:
        """批量文本向量化，返回 (n, dimension) numpy 数组"""

    @abstractmethod
    def embed_query(self, text: str) -> np.ndarray:
        """单条文本向量化，返回 (dimension,) numpy 数组"""


class DashScopeEmbedder(BaseEmbedder):
    """DashScope 文本嵌入封装"""

    def __init__(
        self,
        model_name: Optional[str] = None,
        dimension: Optional[int] = None,
        batch_size: Optional[int] = None,
        batch_interval: Optional[float] = None,
    ):
        from dashscope import TextEmbedding

        cfg = settings.models.embedding
        self._model_name = model_name or cfg.name
        self._dimension = dimension or cfg.dimension
        self._batch_size = batch_size or cfg.batch_size
        self._batch_interval = batch_interval or cfg.batch_interval

    @property
    def dimension(self) -> int:
        return self._dimension

    def embed_texts(self, texts: list[str]) -> np.ndarray:
        """批量文本嵌入，返回 numpy 数组 (n, dimension)"""
        from dashscope import TextEmbedding

        if not texts:
            return np.array([])

        all_embeddings = []
        total = len(texts)

        for i in range(0, total, self._batch_size):
            batch = texts[i : i + self._batch_size]
            logger.debug(f"Embedding 批次 {i // self._batch_size + 1}: {len(batch)} 条")

            try:
                kwargs = {
                    "model": self._model_name,
                    "input": batch,
                }
                if not self._model_name.endswith(("-v1", "-v2")):
                    kwargs["dimension"] = self._dimension

                resp = TextEmbedding.call(**kwargs)

                if resp.status_code == 200:
                    batch_embeddings = [item["embedding"] for item in resp.output["embeddings"]]
                    all_embeddings.extend(batch_embeddings)
                    if not hasattr(self, '_actual_dim') and batch_embeddings:
                        self._actual_dim = len(batch_embeddings[0])
                else:
                    error_msg = resp.message or ""
                    if resp.status_code == 403 or "exhausted" in error_msg.lower():
                        raise RuntimeError(
                            f"Embedding 模型 {self._model_name} 免费额度已用完！"
                            f"请在 models.yaml 中切换到有额度的模型（如 text-embedding-v3/v4），"
                            f"或在阿里云百炼控制台关闭「仅使用免费额度」模式。"
                        )
                    logger.error(f"Embedding API 错误: {resp.status_code} - {error_msg}")
                    all_embeddings.extend([[0.0] * self._dimension] * len(batch))

            except Exception as e:
                logger.error(f"Embedding 请求异常: {e}")
                all_embeddings.extend([[0.0] * self._dimension] * len(batch))

            if i + self._batch_size < total:
                time.sleep(self._batch_interval)

        if hasattr(self, '_actual_dim') and self._actual_dim != self._dimension:
            logger.info(f"Embedding 实际维度: {self._actual_dim} (配置: {self._dimension})")
            self._dimension = self._actual_dim

        return np.array(all_embeddings, dtype=np.float32)

    def embed_query(self, text: str) -> np.ndarray:
        """单条文本嵌入（用于查询）"""
        result = self.embed_texts([text])
        return result[0] if len(result) > 0 else np.zeros(self._dimension, dtype=np.float32)


class LocalEmbedder(BaseEmbedder):
    """本地 Embedding 模型封装（sentence-transformers + ModelScope）"""

    def __init__(
        self,
        model_name: str,
        dimension: int = 1024,
        model_path: Optional[str] = None,
        device: Optional[str] = None,
        batch_size: int = 25,
    ):
        self._model = None
        self._model_name = model_name
        self._dimension = dimension
        self._model_path = model_path
        self._device = device
        self._batch_size = batch_size

    @property
    def dimension(self) -> int:
        return self._dimension

    def _load_model(self):
        """延迟加载模型（首次调用时触发）"""
        if self._model is not None:
            return self._model

        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as e:
            raise RuntimeError(
                f"sentence-transformers 导入失败: {e}，请运行: pip install sentence-transformers torch"
            )
        except Exception as e:
            raise RuntimeError(
                f"sentence-transformers 加载异常: {e}，请检查依赖是否完整"
            )

        model_dir = self._resolve_model_path()

        if not model_dir.exists() or not any(model_dir.iterdir()):
            logger.info(f"本地模型缓存未命中，从 ModelScope 下载: {self._model_name}")
            model_dir = self._download_from_modelscope()

        device = self._resolve_device()
        logger.info(f"加载本地 Embedding 模型: {model_dir} (device={device})")

        try:
            self._model = SentenceTransformer(str(model_dir), device=device)
        except RuntimeError as e:
            if "interpreter shutdown" in str(e).lower() or "cannot schedule new futures" in str(e).lower():
                logger.warning("Embedding 模型加载跳过: Python 解释器正在关闭")
                return None
            raise RuntimeError(f"本地 Embedding 模型加载失败: {e}，请检查模型文件是否完整")
        except Exception as e:
            raise RuntimeError(f"本地 Embedding 模型加载失败: {e}，请检查模型文件是否完整")

        logger.info(f"本地 Embedding 模型加载完成: {self._model_name}, device={device}")
        return self._model

    def _resolve_model_path(self) -> Path:
        """确定模型本地存储路径"""
        if self._model_path:
            return Path(self._model_path)
        return PROJECT_ROOT / "data" / "models" / self._model_name

    def _download_from_modelscope(self) -> Path:
        """从 ModelScope 下载模型"""
        try:
            from modelscope.hub.snapshot_download import snapshot_download
        except ImportError:
            try:
                from modelscope import snapshot_download
            except ImportError:
                raise RuntimeError(
                    "modelscope 未安装，请运行: pip install modelscope"
                )

        cache_dir = str(PROJECT_ROOT / "data" / "models")
        logger.info(f"从 ModelScope 下载模型: {self._model_name} -> {cache_dir}")

        try:
            model_dir = snapshot_download(
                self._model_name,
                cache_dir=cache_dir,
            )
            return Path(model_dir)
        except Exception as e:
            raise RuntimeError(
                f"从 ModelScope 下载模型失败: {e}，"
                f"请检查网络连接或模型名称 {self._model_name} 是否正确"
            )

    def _resolve_device(self) -> str:
        """自动检测推理设备"""
        if self._device:
            return self._device
        try:
            import torch
            if torch.cuda.is_available():
                return "cuda"
        except ImportError:
            pass
        return "cpu"

    def embed_texts(self, texts: list[str]) -> np.ndarray:
        """批量文本向量化，返回 (n, dimension) numpy 数组"""
        if not texts:
            return np.array([], dtype=np.float32)

        model = self._load_model()

        all_vectors = []
        for i in range(0, len(texts), self._batch_size):
            batch = texts[i : i + self._batch_size]
            try:
                vectors = model.encode(batch, normalize_embeddings=True)
                all_vectors.append(vectors)
            except Exception as e:
                logger.error(f"本地 Embedding 向量化失败 (批次 {i // self._batch_size + 1}): {e}")
                raise RuntimeError(f"本地 Embedding 向量化失败: {e}")

        result = np.concatenate(all_vectors, axis=0).astype(np.float32)

        if self._dimension < result.shape[1]:
            result = result[:, : self._dimension]
            norms = np.linalg.norm(result, axis=1, keepdims=True)
            norms = np.where(norms == 0, 1, norms)
            result = result / norms

        return result

    def embed_query(self, text: str) -> np.ndarray:
        """单条文本向量化（用于查询）"""
        result = self.embed_texts([text])
        return result[0]


DASHSCOPE_MODEL_PREFIXES = ("text-embedding-",)


def create_embedder() -> BaseEmbedder:
    """根据配置创建 Embedder 实例

    判断逻辑：
    - 若 embedding.name 以 "text-embedding-" 开头 -> DashScopeEmbedder
    - 否则 -> LocalEmbedder（作为本地模型处理）
    """
    cfg = settings.models.embedding
    name = cfg.name

    if any(name.startswith(prefix) for prefix in DASHSCOPE_MODEL_PREFIXES):
        logger.info(f"使用 DashScope 云端 Embedding: {name}")
        return DashScopeEmbedder()
    else:
        logger.info(f"使用本地 Embedding 模型: {name}")
        return LocalEmbedder(
            model_name=name,
            dimension=cfg.dimension,
            model_path=cfg.model_path or None,
            device=cfg.device or None,
            batch_size=cfg.batch_size,
        )
