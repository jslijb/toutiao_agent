"""日志配置，基于 loguru"""
from __future__ import annotations

import sys
from pathlib import Path

from loguru import logger

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = PROJECT_ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)


def setup_logger():
    """配置 loguru 日志"""
    logger.remove()  # 移除默认 handler

    # 控制台输出（INFO 及以上）
    logger.add(
        sys.stderr,
        level="INFO",
        format="<green>{time:HH:mm:ss}</green> | <level>{level:<8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan> - <level>{message}</level>",
        colorize=True,
    )

    # 全量日志文件（DEBUG 及以上）
    logger.add(
        LOG_DIR / "app_{time:YYYY-MM-DD}.log",
        level="DEBUG",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level:<8} | {name}:{function}:{line} - {message}",
        rotation="1 day",
        retention="30 days",
        encoding="utf-8",
    )

    # 错误日志文件
    logger.add(
        LOG_DIR / "error_{time:YYYY-MM-DD}.log",
        level="ERROR",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level:<8} | {name}:{function}:{line} - {message}",
        rotation="1 day",
        retention="90 days",
        encoding="utf-8",
    )

    return logger


# 模块加载时自动初始化
setup_logger()
