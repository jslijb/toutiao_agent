"""wanx2.1-t2i-turbo 异步卡通图片生成封装

异步调用流程：
1. 提交任务（创建异步任务，获取 task_id）
2. 轮询任务状态（每 poll_interval 秒查询一次）
3. 获取结果 URL
4. 下载图片到本地
"""
from __future__ import annotations

import time
import uuid
from pathlib import Path
from typing import Optional

import httpx
from loguru import logger

from config.settings import settings, PROJECT_ROOT


class WanxImageGenerator:
    """通义万相文生图 V2 Turbo 异步调用封装"""

    SUBMIT_URL = "https://dashscope.aliyuncs.com/api/v1/services/aigc/text2image/image-synthesis"
    TASK_URL = "https://dashscope.aliyuncs.com/api/v1/tasks/{task_id}"

    def __init__(self, output_dir: str | Path | None = None):
        cfg = settings.models.image_gen
        self.model = cfg.name
        self.style = cfg.style
        self.size = cfg.size
        self.n = cfg.n
        self.async_mode = cfg.async_mode
        self.poll_interval = cfg.poll_interval
        self.max_poll_times = cfg.max_poll_times
        self.api_key = settings.dashscope_api_key

        self.output_dir = Path(output_dir) if output_dir else PROJECT_ROOT / "output" / "images"
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def generate(self, prompt: str, negative_prompt: str = "", n: int | None = None) -> list[str]:
        """
        生成卡通图片，返回本地文件路径列表

        Args:
            prompt: 图片描述 Prompt
            negative_prompt: 反向 Prompt
            n: 生成数量（默认使用配置值）

        Returns:
            本地图片文件路径列表
        """
        n = n or self.n
        logger.info(f"[图片] 开始生成 {n} 张图片, 模型: {self.model}")

        # Step 1: 提交任务
        task_ids = self._submit_tasks(prompt, negative_prompt, n)
        if not task_ids:
            logger.error("[图片] 任务提交失败")
            return []

        # Step 2: 并行轮询所有任务
        logger.info(f"[图片] 已提交 {len(task_ids)} 个任务，等待生成...")
        results = self._poll_all_tasks(task_ids)

        # Step 3: 下载图片
        paths = []
        for i, url in enumerate(results):
            try:
                path = self._download_image(url, index=i)
                if path:
                    paths.append(path)
            except Exception as e:
                logger.error(f"[图片] 第 {i + 1} 张下载失败: {e}")

        logger.info(f"[图片] 生成完成: {len(paths)}/{n} 张")
        return paths

    def generate_scenes(self, scenes: list[str], negative_prompt: str = "") -> list[str]:
        """为多个场景分别生成图片（每场景一张）"""
        all_paths = []
        for i, scene in enumerate(scenes):
            logger.info(f"[图片] 生成第 {i + 1}/{len(scenes)} 张: {scene[:20]}...")
            paths = self.generate(prompt=scene, negative_prompt=negative_prompt, n=1)
            if paths:
                all_paths.extend(paths)
        return all_paths

    def _submit_tasks(self, prompt: str, negative_prompt: str, n: int) -> list[str]:
        """提交异步任务，返回 task_id 列表"""
        task_ids = []

        for _ in range(n):
            payload = {
                "model": self.model,
                "input": {
                    "prompt": prompt,
                    "negative_prompt": negative_prompt or "模糊, 低质量, 变形, 文字水印",
                },
                "parameters": {
                    "size": self.size,
                    "n": 1,
                    "style": f"<{self.style}>",
                },
            }

            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "X-DashScope-Async": "enable",  # 异步模式
            }

            try:
                with httpx.Client(timeout=30) as client:
                    resp = client.post(self.SUBMIT_URL, json=payload, headers=headers)
                    data = resp.json()

                    if resp.status_code == 200 and data.get("output", {}).get("task_id"):
                        task_id = data["output"]["task_id"]
                        task_ids.append(task_id)
                        logger.debug(f"[图片] 任务已提交: {task_id}")
                    else:
                        error_msg = data.get("message", "未知错误")
                        logger.error(f"[图片] 任务提交失败: {error_msg}")
            except Exception as e:
                logger.error(f"[图片] 任务提交异常: {e}")

            time.sleep(0.5)  # 避免瞬间并发太多

        return task_ids

    def _poll_all_tasks(self, task_ids: list[str]) -> list[str]:
        """轮询所有任务直到完成，返回图片 URL 列表"""
        results = [None] * len(task_ids)
        pending = list(range(len(task_ids)))

        for poll in range(self.max_poll_times):
            if not pending:
                break

            new_pending = []
            for idx in pending:
                task_id = task_ids[idx]
                status, url = self._check_task(task_id)
                if status == "SUCCEEDED" and url:
                    results[idx] = url
                    logger.info(f"[图片] 任务 {task_id[:8]}... 完成")
                elif status == "FAILED":
                    logger.error(f"[图片] 任务 {task_id[:8]}... 失败")
                else:
                    new_pending.append(idx)

            pending = new_pending
            if pending:
                time.sleep(self.poll_interval)

        if pending:
            for idx in pending:
                logger.warning(f"[图片] 任务 {task_ids[idx][:8]}... 超时")

        return [r for r in results if r]

    def _check_task(self, task_id: str) -> tuple[str, Optional[str]]:
        """查询单个任务状态，返回 (status, image_url)"""
        url = self.TASK_URL.format(task_id=task_id)
        headers = {"Authorization": f"Bearer {self.api_key}"}

        try:
            with httpx.Client(timeout=10) as client:
                resp = client.get(url, headers=headers)
                data = resp.json()

                output = data.get("output", {})
                status = output.get("task_status", output.get("status", "UNKNOWN"))

                if status == "SUCCEEDED":
                    results = output.get("results", [])
                    if results:
                        image_url = results[0].get("url", "")
                        return status, image_url

                return status, None
        except Exception as e:
            logger.debug(f"[图片] 查询任务异常: {e}")
            return "UNKNOWN", None

    def _download_image(self, url: str, index: int = 0) -> Optional[str]:
        """下载图片到本地"""
        filename = f"{uuid.uuid4().hex[:8]}_{index}.png"
        filepath = self.output_dir / filename

        try:
            with httpx.Client(timeout=60, follow_redirects=True) as client:
                resp = client.get(url)
                resp.raise_for_status()
                with open(filepath, "wb") as f:
                    f.write(resp.content)
            logger.info(f"[图片] 已保存: {filepath}")
            return str(filepath)
        except Exception as e:
            logger.error(f"[图片] 下载失败: {e}")
            return None
