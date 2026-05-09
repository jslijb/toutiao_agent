"""Tab: 质量标注 - 标注文章质量+查看分析结果+管理教训经验"""
from __future__ import annotations

import gradio as gr
from loguru import logger

from quality.cause_attributor import get_available_categories, get_statistics
from quality.models import CAUSE_CATEGORY_LABELS, AutoAnalysisResult

_pending_auto_result: AutoAnalysisResult | None = None


def create_quality_tab() -> gr.Blocks:
    """创建质量标注 Tab"""
    categories = get_available_categories()
    category_choices = [(c["label"], c["value"]) for c in categories]

    with gr.Blocks() as tab:
        gr.Markdown("## 质量标注")

        gr.Markdown("### 自动分析")
        with gr.Row():
            auto_title_input = gr.Textbox(label="文章标题", placeholder="请输入文章标题", scale=1)
        auto_content_input = gr.Textbox(label="文章正文", lines=10, placeholder="请黏贴文章正文")
        with gr.Row():
            auto_analyze_btn = gr.Button("自动分析", variant="primary", size="lg")
            auto_ingest_btn = gr.Button("确认保存到教训/经验库", variant="secondary", size="lg", interactive=False)
        auto_result_output = gr.Textbox(label="分析结果", interactive=False, lines=12)

        with gr.Row():
            with gr.Column():
                gr.Markdown("### 标注文章")
                article_id_input = gr.Textbox(label="文章ID", placeholder="输入文章ID")
                quality_radio = gr.Radio(
                    choices=["positive", "negative"],
                    label="质量分类",
                    info="positive=优质, negative=劣质",
                )
                limit_flow_check = gr.Checkbox(label="被平台限流", value=False)
                cause_check = gr.CheckboxGroup(
                    choices=category_choices,
                    label="原因分类（劣质必填）",
                )
                reason_input = gr.Textbox(label="标注原因备注", lines=2, placeholder="简述为什么好/差")
                label_btn = gr.Button("提交标注", variant="primary", size="lg")

            with gr.Column():
                label_output = gr.Textbox(label="标注结果", interactive=False, lines=15)

        gr.Markdown("### 教训与经验管理")
        with gr.Row():
            list_btn = gr.Button("刷新列表", variant="secondary")
            delete_id_input = gr.Textbox(label="删除条目ID", placeholder="输入要删除的ID")
            delete_type_radio = gr.Radio(choices=["lesson", "experience"], label="条目类型")
            delete_btn = gr.Button("删除（标记废弃）", variant="stop")
        knowledge_output = gr.Textbox(label="教训/经验列表", interactive=False, lines=10)

        gr.Markdown("### 检索匹配记录")
        with gr.Row():
            records_type_radio = gr.Radio(
                choices=["all", "rag_reference", "negative_lesson", "positive_experience"],
                value="all",
                label="记录类型",
            )
            refresh_records_btn = gr.Button("刷新记录", variant="secondary")
        records_output = gr.Textbox(label="检索匹配记录", interactive=False, lines=10)

        gr.Markdown("### 原因分类统计")
        stats_btn = gr.Button("刷新统计", variant="secondary")
        stats_output = gr.Textbox(label="原因分类统计", interactive=False, lines=8)

        label_btn.click(
            fn=do_label,
            inputs=[article_id_input, quality_radio, limit_flow_check, cause_check, reason_input],
            outputs=[label_output],
        )
        auto_analyze_btn.click(
            fn=do_auto_analyze,
            inputs=[auto_title_input, auto_content_input],
            outputs=[auto_result_output, auto_analyze_btn, auto_ingest_btn],
        )
        auto_ingest_btn.click(
            fn=do_confirm_ingest,
            outputs=[auto_result_output, auto_ingest_btn],
        )
        list_btn.click(fn=do_list_knowledge, outputs=[knowledge_output])
        delete_btn.click(
            fn=do_delete_knowledge,
            inputs=[delete_id_input, delete_type_radio],
            outputs=[knowledge_output],
        )
        refresh_records_btn.click(
            fn=do_query_records,
            inputs=[records_type_radio],
            outputs=[records_output],
        )
        stats_btn.click(fn=do_get_stats, outputs=[stats_output])

    return tab


def do_auto_analyze(title: str, content: str):
    """执行自动分析"""
    global _pending_auto_result
    _pending_auto_result = None

    if not title.strip() or not content.strip():
        yield "请填写文章标题和正文内容", gr.update(), gr.update()
        return

    yield "正在分析中...", gr.update(interactive=False, value="分析中..."), gr.update(interactive=False)

    from quality.auto_analyzer import auto_analyze
    result = auto_analyze(title.strip(), content.strip())
    _pending_auto_result = result

    parts = []
    if result.quality_category == "positive":
        parts.append(f"质量分类: 优质 (positive)")
    elif result.quality_category == "negative":
        parts.append(f"质量分类: 劣质 (negative)")
    else:
        parts.append(f"质量分类: 未确定")

    if result.classify_reason:
        parts.append(f"分类理由: {result.classify_reason}")
    if result.cause_categories:
        labels = [CAUSE_CATEGORY_LABELS.get(c, c) for c in result.cause_categories]
        parts.append(f"原因分类: {', '.join(labels)}")
    if result.detail:
        parts.append(f"\n分析详情:\n{result.detail}")
    if result.lesson_text:
        parts.append(f"\n教训摘要: {result.lesson_text}")
    if result.experience_text:
        parts.append(f"\n经验摘要: {result.experience_text}")
    if result.analysis_status == "failed":
        parts.append(f"\n[分析状态: 失败]")
    elif result.analysis_status == "partial":
        parts.append(f"\n[分析状态: 部分完成]")

    ingest_interactive = result.analysis_status == "done" and result.quality_category in ("positive", "negative")
    yield "\n".join(parts), gr.update(interactive=True, value="自动分析"), gr.update(interactive=ingest_interactive)


def do_confirm_ingest():
    """确认入库分析结果"""
    global _pending_auto_result

    if not _pending_auto_result:
        yield "无待保存的分析结果", gr.update()
        return

    from quality.auto_analyzer import confirm_ingest
    item_id = confirm_ingest(_pending_auto_result)

    if item_id:
        cat = "教训" if _pending_auto_result.quality_category == "negative" else "经验"
        yield f"入库成功！{cat}ID: {item_id}", gr.update(interactive=False, value="已保存")
    else:
        yield "入库失败，数据已暂存", gr.update()


def do_label(article_id: str, quality_category: str, limit_flow: bool, causes: list[str], reason: str):
    """执行标注"""
    if not article_id or not quality_category:
        yield "请填写文章ID和质量分类"
        return

    yield "正在标注并触发LLM分析..."

    from quality.labeler import submit_label
    result = submit_label(
        article_id=article_id.strip(),
        quality_category=quality_category,
        limit_flow=limit_flow,
        cause_categories=causes,
        label_reason=reason,
    )

    output_parts = [f"标注结果: {'成功' if result.success else '失败'}"]
    output_parts.append(f"消息: {result.message}")
    if result.lesson_ids:
        output_parts.append(f"入库教训: {result.lesson_ids}")
    if result.experience_ids:
        output_parts.append(f"入库经验: {result.experience_ids}")
    if result.analysis_report:
        if result.analysis_report.get("detail"):
            output_parts.append(f"\n分析详情:\n{result.analysis_report['detail']}")
        if result.analysis_report.get("lesson_text"):
            output_parts.append(f"\n教训摘要: {result.analysis_report['lesson_text']}")
        if result.analysis_report.get("experience_text"):
            output_parts.append(f"\n经验摘要: {result.analysis_report['experience_text']}")

    yield "\n".join(output_parts)


def do_list_knowledge():
    """列出教训和经验"""
    from quality.ingester import list_knowledge
    knowledge = list_knowledge()
    parts = []
    lessons = knowledge.get("lessons", [])
    experiences = knowledge.get("experiences", [])
    parts.append(f"=== 教训 ({len(lessons)} 条) ===")
    for l in lessons[-20:]:
        dep = " [废弃]" if l.get("deprecated") else ""
        parts.append(f"  {l['id']}: {l.get('lesson_text', '')[:80]}{dep}")
    parts.append(f"\n=== 经验 ({len(experiences)} 条) ===")
    for e in experiences[-20:]:
        dep = " [废弃]" if e.get("deprecated") else ""
        parts.append(f"  {e['id']}: {e.get('experience_text', '')[:80]}{dep}")
    return "\n".join(parts)


def do_delete_knowledge(item_id: str, item_type: str):
    """删除教训或经验"""
    from quality.ingester import delete_knowledge
    ok = delete_knowledge(item_type, item_id)
    result = f"已标记废弃: {item_id}" if ok else f"未找到: {item_id}"
    return result + "\n\n" + do_list_knowledge()


def do_query_records(retrieval_type: str):
    """查询检索匹配记录"""
    from quality.retrieval_match_logger import query_records
    rt = None if retrieval_type == "all" else retrieval_type
    records = query_records(retrieval_type=rt, limit=20)
    if not records:
        return "暂无记录"
    parts = []
    for r in records[-20:]:
        parts.append(
            f"[{r.get('retrieved_at', '')[:16]}] "
            f"type={r.get('retrieval_type', '')} "
            f"matches={r.get('match_count', 0)} "
            f"injected={r.get('injected_count', 0)} "
            f"query={r.get('query', '')[:30]}"
        )
    return "\n".join(parts)


def do_get_stats():
    """获取原因分类统计"""
    from quality.retrieval_match_logger import get_statistics as get_retrieval_stats
    try:
        from models.article_store import get_article_store
        store = get_article_store()
        articles = store.get_with_content()
        cause_stats = get_statistics(articles)

        parts = ["=== 原因分类统计 ==="]
        for cat, count in cause_stats.items():
            label = CAUSE_CATEGORY_LABELS.get(cat, cat)
            parts.append(f"  {label}: {count} 篇")

        retrieval_stats = get_retrieval_stats()
        parts.append(f"\n=== 检索匹配记录 ===")
        parts.append(f"  总记录数: {retrieval_stats.get('total', 0)}")
        for rt, info in retrieval_stats.get("by_type", {}).items():
            parts.append(f"  {rt}: {info.get('count', 0)}次, 匹配{info.get('total_matches', 0)}条, 注入{info.get('total_injected', 0)}条")

        return "\n".join(parts)
    except Exception as e:
        return f"统计查询失败: {e}"
