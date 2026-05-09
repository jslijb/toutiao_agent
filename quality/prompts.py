"""LLM分析Prompt模板"""

NEGATIVE_ANALYSIS_PROMPT = """你是一位文章质量分析师。以下文章已被用户标注为"劣质"（质量差），请分析原因并提炼教训摘要。

## 文章信息
标题: {title}
正文: {content}
原因分类: {cause_categories}
用户备注: {label_reason}

## 分析要求
1. 分析这篇文章为什么质量差（注意：不要评分，质量分类已由用户确定）
2. 将分析结果提炼为**精炼的教训摘要**（1-2句话），如"标题使用夸大词汇'震惊'易被限流"
3. 建议可能的原因分类标签（从以下选择）: title_issue, content_hollow, forbidden_words, structure_chaos, irrelevant_topic, limit_flow_penalty, other

## 输出格式（JSON）
```json
{{
  "cause_suggestion": ["分类1", "分类2"],
  "detail": "详细分析（3-5句话）",
  "lesson_text": "精炼教训摘要（1-2句话，可供下次生成时避坑参考）"
}}
```"""

POSITIVE_ANALYSIS_PROMPT = """你是一位文章质量分析师。以下文章已被用户标注为"优质"（质量好），请分析成功因素并提炼经验摘要。

## 文章信息
标题: {title}
正文: {content}
用户备注: {label_reason}

## 分析要求
1. 分析这篇文章为什么写得好（注意：不要评分，质量分类已由用户确定）
2. 将分析结果提炼为**精炼的经验摘要**（1-2句话），如"用具体数据开头的文章可信度高"

## 输出格式（JSON）
```json
{{
  "detail": "详细分析（3-5句话）",
  "experience_text": "精炼经验摘要（1-2句话，可供下次生成时参考模仿）"
}}
```"""

QUALITY_CLASSIFY_PROMPT = """你是一位文章质量裁判。请根据文章标题和正文内容，判定文章质量分类，并识别可能的原因。

## 文章信息
标题: {title}
正文: {content}

## 分类标准（从严判定，宁可误判劣质也不放过低质内容）
- positive（优质）：标题新颖有吸引力且不夸大、正文内容充实有深度有独到见解、结构清晰逻辑连贯、无违规词汇、有具体数据或案例支撑
- negative（劣质）：标题夸大/标题党/误导、正文空洞/泛泛而谈/堆砌套话/无实质内容、包含违规词汇、结构混乱、与主题无关、缺少数据或案例、内容过短不足以支撑观点

## 特别注意
- 阅读量=0或极低的文章，通常质量不佳或未被平台推荐，倾向判为negative
- 正文全为抽象论述而无具体案例/数据支撑的，倾向判为negative
- 仅堆砌"众所周知""不可忽视"等套话的，判为negative(content_hollow)

## 常见劣质原因分类
- title_issue: 标题问题（夸大、标题党、误导）
- content_hollow: 内容空洞（缺乏实质内容、泛泛而谈）
- forbidden_words: 违规词汇（敏感词、低俗词、夸张极限词）
- structure_chaos: 结构混乱（逻辑跳跃、段落不衔接）
- irrelevant_topic: 与热点无关（蹭热点但内容偏离）
- limit_flow_penalty: 限流降权（已被平台限流降权）
- other: 其他

## 分析要求
1. 判定文章质量分类（positive 或 negative）
2. 若为 negative，识别原因分类标签（可多选）
3. 提供简明分析说明（2-3句话）
4. 注意：不要对文章进行数值评分

## 输出格式（JSON）
```json
{{
  "quality_category": "positive 或 negative",
  "cause_categories": ["分类1", "分类2"],
  "classify_reason": "分类判定理由（2-3句话）"
}}
```"""
