"""
头条内容 Agent - 主入口

使用方式：
1. conda activate coze
2. python main.py                    # 使用默认端口 7860
3. python main.py --port 8080        # 指定端口
4. python main.py -p 8080            # 指定端口（简写）

启动后访问 http://127.0.0.1:<端口号>
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# 确保项目根目录在 Python 路径中
PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from webui.app import main

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="头条内容 Agent - 自动化内容创作平台")
    parser.add_argument(
        "-p", "--port",
        type=int,
        default=None,
        help="Web UI 端口号（默认: 7860，可在 models.yaml 中修改）",
    )
    parser.add_argument(
        "--host",
        type=str,
        default=None,
        help="Web UI 监听地址（默认: 127.0.0.1，可在 models.yaml 中修改）",
    )
    parser.add_argument(
        "--share",
        action="store_true",
        default=None,
        help="创建公开分享链接",
    )
    args = parser.parse_args()

    # 命令行参数覆盖配置
    overrides = {}
    if args.port is not None:
        overrides["port"] = args.port
    if args.host is not None:
        overrides["host"] = args.host
    if args.share is not None:
        overrides["share"] = args.share

    main(**overrides)
