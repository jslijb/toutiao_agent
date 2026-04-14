"""Gradio Web 应用主文件"""
from __future__ import annotations

import gradio as gr
from loguru import logger

from config.settings import settings
from webui.tabs.config_tab import create_config_tab
from webui.tabs.crawler_tab import create_crawler_tab
from webui.tabs.generate_tab import create_generate_tab
from webui.tabs.image_tab import create_image_tab
from webui.tabs.publish_tab import create_publish_tab


def create_app() -> gr.Blocks:
    """创建 Gradio 应用"""
    import time
    start_time = time.time()
    
    logger.info("[应用创建] 开始创建 Gradio 应用...")
    
    # 减少页面加载时的计算开销
    llm_name = settings.models.llm.name
    image_gen_name = settings.models.image_gen.name
    embedding_name = settings.models.embedding.name
    
    logger.info(f"[应用创建] 配置读取耗时: {time.time() - start_time:.3f}秒")
    
    with gr.Blocks(title="头条内容 Agent") as app:
        gr.Markdown("# 头条内容 Agent - 自动化内容创作平台")
        gr.Markdown(
            "爬取高质量文章 → 构建RAG知识库 → AI生成爆款微头条 → 卡通配图 → 自动发布"
        )

        with gr.Tabs():
            with gr.Tab("配置管理"):
                create_config_tab()
            with gr.Tab("爬虫控制"):
                create_crawler_tab()
            with gr.Tab("文章生成"):
                create_generate_tab()
            with gr.Tab("卡通配图"):
                create_image_tab()
            with gr.Tab("发布准备"):
                create_publish_tab()

        gr.Markdown("---")
        gr.Markdown(
            f"LLM: {llm_name} | "
            f"图片: {image_gen_name} | "
            f"嵌入: {embedding_name}"
        )

    # 启用队列，避免长时间运行的任务阻塞 UI
    app.queue(max_size=5)
    
    logger.info(f"[应用创建] 应用创建完成，总耗时: {time.time() - start_time:.3f}秒")
    return app


def main(port: int | None = None, host: str | None = None, share: bool | None = None):
    """启动 Gradio 应用

    Args:
        port: 覆盖配置中的端口号（命令行 --port 指定）
        host: 覆盖配置中的监听地址（命令行 --host 指定）
        share: 覆盖配置中的分享选项（命令行 --share 指定）
    """
    import time
    import socket
    start_time = time.time()
    
    logger.info("[启动] 开始初始化...")
    
    # 确定启动参数：命令行 > 配置文件
    server_port = port if port is not None else settings.webui.port
    server_name = host if host is not None else settings.webui.host
    server_share = share if share is not None else settings.webui.share

    # 检查端口是否可用，如果被占用则尝试使用其他端口
    def is_port_available(port):
        """检查端口是否可用"""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.bind((server_name, port))
            sock.close()
            return True
        except:
            return False
    
    # 如果端口被占用，尝试使用其他端口
    if not is_port_available(server_port):
        logger.warning(f"[启动] 端口 {server_port} 已被占用，尝试使用其他端口...")
        # 尝试从 7861 开始寻找可用端口
        for i in range(1, 10):
            new_port = server_port + i
            if is_port_available(new_port):
                server_port = new_port
                logger.info(f"[启动] 找到可用端口: {server_port}")
                break
        else:
            logger.error("[启动] 无法找到可用端口，退出启动")
            return

    logger.info(f"[启动] 配置参数: port={server_port}, host={server_name}, share={server_share}")
    
    # 延迟初始化 ArticleStore，避免启动时阻塞
    logger.info("[启动] 正在创建 Gradio 应用...")
    app = create_app()
    
    logger.info(f"[启动] 应用创建完成，耗时: {time.time() - start_time:.2f}秒")
    logger.info(f"[启动] 启动 Gradio Web UI... (http://{server_name}:{server_port})")
    
    # 启动服务器
    app.launch(
        server_name=server_name,
        server_port=server_port,
        share=server_share,
        inbrowser=True,
        theme=gr.themes.Soft()
    )


if __name__ == "__main__":
    main()
