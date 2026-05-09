"""Tab 2: 数据源管理 - 替代原爬虫控制Tab"""
from __future__ import annotations

import threading
import time
from datetime import datetime

import gradio as gr
from loguru import logger

from agent.pipeline import CrawlPipeline, RAGPipeline
from config.settings import settings

_sync_state = {
    "running": False,
    "progress": "",
    "done": False,
}
_sync_lock = threading.Lock()


def create_datasource_tab() -> gr.Blocks:
    """创建数据源管理 Tab"""
    with gr.Blocks() as tab:
        gr.Markdown("## 数据源管理")

        with gr.Row():
            with gr.Column():
                gr.Markdown("### 实时数据同步")
                source_check = gr.CheckboxGroup(
                    choices=["tianapi", "bing", "rss"],
                    value=["tianapi", "rss"],
                    label="选择数据源",
                )
                keywords_input = gr.Textbox(
                    value="职场, 副业, 个人成长, 赚钱, AI, 创业, 裁员, 晋升",
                    label="搜索关键词（逗号分隔，Bing使用）",
                    lines=2,
                )
                sync_btn = gr.Button("开始同步", variant="primary", size="lg")

            with gr.Column():
                sync_output = gr.Textbox(
                    label="同步进度",
                    interactive=False,
                    lines=12,
                    max_lines=30,
                )

        gr.Markdown("### 静态数据集导入")
        gr.Markdown(
            "导入 [nlp_chinese_corpus](https://github.com/brightmart/nlp_chinese_corpus) 数据集。\n"
            "点击「开始导入」时，若本地无数据文件，会**自动从 HuggingFace 镜像下载样本**（约5万条）。\n"
            "如需导入完整数据集，请下载后放到 `data/corpus/` 目录，或在 `models.yaml` 中配置路径。"
        )
        with gr.Row():
            dataset_type = gr.Dropdown(
                choices=["news2016zh", "webtext2019zh"],
                value="news2016zh",
                label="数据集类型",
            )
            import_btn = gr.Button("开始导入", variant="primary")
        import_output = gr.Textbox(label="导入进度", interactive=False, lines=5)

        gr.Markdown("### 配额状态")
        with gr.Row():
            quota_output = gr.Textbox(label="API配额", interactive=False, lines=3)
            refresh_quota_btn = gr.Button("刷新配额")
            reset_bing_btn = gr.Button("重置Bing月度配额")

        gr.Markdown("### RAG 向量库管理")
        gr.Markdown(
            "正常情况下 RAG 会**自动增量索引**新同步的文章，无需手动操作。\n\n"
            "- **增量重建**：清理零向量 + 补齐未索引文章\n"
            "- **全量重建**：清空向量库从头重建（维度变更时使用）\n"
            "- **查重检查**：检查知识库重复文章"
        )
        with gr.Row():
            incr_rebuild_btn = gr.Button("增量重建", variant="primary")
            rebuild_btn = gr.Button("全量重建", variant="stop")
            dedup_check_btn = gr.Button("查重检查", variant="secondary")
        rebuild_output = gr.Textbox(label="重建进度", interactive=False, lines=5)

        sync_btn.click(
            fn=run_sync,
            inputs=[source_check, keywords_input],
            outputs=[sync_output],
        )
        import_btn.click(
            fn=import_corpus,
            inputs=[dataset_type],
            outputs=[import_output],
        )
        refresh_quota_btn.click(fn=get_quota_status, outputs=[quota_output])
        reset_bing_btn.click(fn=reset_bing_quota, outputs=[quota_output])
        rebuild_btn.click(fn=rebuild_vectorstore, outputs=[rebuild_output])
        incr_rebuild_btn.click(fn=incremental_rebuild_vectorstore, outputs=[rebuild_output])
        dedup_check_btn.click(fn=check_rag_duplicates, outputs=[rebuild_output])

    return tab


def run_sync(sources: list[str], keywords_str: str):
    """执行实时数据同步"""
    global _sync_state

    if not sources:
        yield "请选择至少一个数据源"
        return

    keywords = [k.strip() for k in keywords_str.split(",") if k.strip()]

    with _sync_lock:
        if _sync_state["running"]:
            yield "同步正在运行中，请等待完成"
            return
        _sync_state["running"] = True
        _sync_state["done"] = False
        _sync_state["progress"] = f"正在同步...\n数据源: {', '.join(sources)}"

    yield _sync_state["progress"]

    try:
        pipeline = CrawlPipeline()
        context = pipeline.run(sources=sources, keywords=keywords, parallel=True)

        stats = {}
        for art in context.articles:
            stats[art.source] = stats.get(art.source, 0) + 1

        stat_lines = [f"同步完成: {len(context.articles)} 篇文章"]
        for src, cnt in sorted(stats.items()):
            stat_lines.append(f"  {src}: {cnt} 篇")

        rag_lines = []
        try:
            rag_pipeline = RAGPipeline()
            rag_context = rag_pipeline.rebuild_incremental()
            rag_result = rag_context.get_last_result()
            rag_lines.append(f"RAG: {rag_result.status.value} - {rag_result.message}")
        except Exception as e:
            rag_lines.append(f"RAG 构建失败: {e}")

        with _sync_lock:
            _sync_state["progress"] = "\n".join(stat_lines) + "\n" + "\n".join(rag_lines)
            _sync_state["done"] = True
            _sync_state["running"] = False

        yield _sync_state["progress"]

    except Exception as e:
        logger.error(f"数据同步异常: {e}")
        with _sync_lock:
            _sync_state["progress"] = f"同步失败: {e}"
            _sync_state["done"] = True
            _sync_state["running"] = False
        yield _sync_state["progress"]


def import_corpus(dataset_type: str):
    """执行静态数据集导入（未下载时自动下载样本）"""
    try:
        from datasources.corpus_adapter import CorpusAdapter
        adapter = CorpusAdapter()

        file_path = adapter._resolve_dataset_file(dataset_type)
        if not file_path or not file_path.exists():
            yield f"数据集 {dataset_type} 未下载，正在从 HuggingFace 镜像下载样本（约5万条）...\n首次下载可能需要几分钟，请耐心等待。"
            try:
                adapter.download_dataset(dataset_type)
                yield f"下载完成！文件: {file_path}\n开始导入数据集..."
            except Exception as e:
                yield f"下载失败: {e}\n请检查网络连接，或手动下载后放到 data/corpus/ 目录。"
                return
        else:
            yield f"数据集已存在: {file_path}\n正在导入..."

        adapter.fetch(dataset_type=dataset_type, resume=True)
        yield f"数据集 {dataset_type} 导入完成！请点击「增量重建」以更新RAG索引。"
    except Exception as e:
        logger.error(f"数据集导入失败: {e}")
        yield f"导入失败: {e}"


def get_quota_status():
    """获取配额状态"""
    try:
        from datasources.quota_manager import get_quota_manager
        qm = get_quota_manager()
        bing_remaining = qm.get_bing_remaining()
        bing_used = qm.state.bing_used_count
        bing_limit = qm.state.bing_monthly_limit
        tianapi_remaining = qm.get_tianapi_remaining_beans()

        from config.settings import env_settings
        tianapi_key = env_settings.tianapi_key
        bing_key = env_settings.bing_api_key

        tianapi_key_mask = f"...{tianapi_key[-4:]}" if len(tianapi_key) >= 4 else "未配置"
        bing_key_mask = f"...{bing_key[-4:]}" if len(bing_key) >= 4 else "未配置"

        return (
            f"Bing Search API:\n"
            f"  Key: {bing_key_mask}\n"
            f"  本月已用: {bing_used}/{bing_limit} (剩余: {bing_remaining})\n\n"
            f"天行数据API:\n"
            f"  Key: {tianapi_key_mask}\n"
            f"  天豆余额: {tianapi_remaining:.0f}"
        )
    except Exception as e:
        return f"配额查询失败: {e}"


def reset_bing_quota():
    """重置Bing月度配额"""
    try:
        from datasources.quota_manager import get_quota_manager
        qm = get_quota_manager()
        qm.reset_bing_quota()
        return get_quota_status()
    except Exception as e:
        return f"重置失败: {e}"


def rebuild_vectorstore():
    """全量重建RAG向量库"""
    try:
        yield "正在全量重建向量库..."
        pipeline = RAGPipeline()
        context = pipeline.run(force_full=True)
        result = context.get_last_result()
        if result.status.value == "success":
            yield f"全量重建完成!\n{result.message}"
        else:
            yield f"全量重建失败: {result.message}"
    except Exception as e:
        yield f"全量重建失败: {e}"


def incremental_rebuild_vectorstore():
    """增量重建RAG向量库"""
    try:
        yield "正在增量重建向量库..."
        pipeline = RAGPipeline()
        context = pipeline.rebuild_incremental()
        result = context.get_last_result()
        if result.status.value == "success":
            yield f"增量重建完成!\n{result.message}"
        else:
            yield f"增量重建失败: {result.message}"
    except Exception as e:
        yield f"增量重建失败: {e}"


def check_rag_duplicates():
    """检查RAG知识库重复文章"""
    try:
        yield "正在检查重复文章..."
        pipeline = RAGPipeline()
        result = pipeline.check_rag_duplicates()
        yield result
    except Exception as e:
        yield f"查重检查失败: {e}"
