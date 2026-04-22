# 头条内容 Agent

基于 Python 的全链路自动化内容创作与发布平台，从多平台爬取文章 → 构建 RAG 知识库 → AI 生成微头条 → 自动生成卡通配图 → 一键发布到今日头条。

## 技术栈

| 类别 | 技术 | 用途 |
|------|------|------|
| **Web UI** | Gradio 5.x | 5个Tab页面交互界面 |
| **大语言模型** | Qwen3.5plus | 文章生成、标题优化、热点筛选、场景提取 |
| **Embedding** | text-embedding-v2 (DashScope) | RAG 向量化 |
| **向量检索** | FAISS (IndexFlatIP + L2归一化=余弦相似度) | 语义检索 |
| **浏览器自动化** | Playwright (Chromium) + playwright-stealth | 爬虫详情页、登录、发布操作 |
| **图片生成** | 通义万相 wanx2.1-t2i-turbo (异步API) | 3D 卡通风格配图 |
| **HTTP 客户端** | httpx | 图片下载、热点API调用 |
| **日志** | loguru | 结构化日志输出 |
| **配置管理** | PyYAML + 环境变量 | 集中参数管理 |
| **数据持久化** | JSON (文章/生成记录) + FAISS 二进制 (向量索引) | 本地存储 |

## 数据流程总览

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         用户通过 Web UI 操作                              │
└─────────────────────────────────────────────────────────────────────────┘
                                   │
                    ┌──────────▼───────────┐
                    │    Tab: 爬虫控制         │
                    │  选择平台 + 关键词      │
                    └──────────┬───────────┘
                               │
        ┌──────────────────────┼───────────────────────┐
        ▼                      ▼                       ▼
┌──────────────┐   ┌──────────────┐   ┌──────────────┐
│  今日头条     │   │  知乎        │   │  百家号      │
│  搜索爬虫     │   │  搜索爬虫     │   │  搜索爬虫     │
└──────┬───────┘   └──────┬───────┘   └──────┬───────┘
       │                 │                  │
       ┌─────────────────┼──────────────────┐
       ▼                 ▼                  ▼
┌──────────────┐   ┌──────────────┐   ┌──────────────┐
│  微信公众号   │   │  36氪        │   │   ...         │
│  (搜狗搜索)   │   │  搜索爬虫     │   │              │
└──────┬───────┘   └──────┬───────┘   └───────────────┘
       │                 │                  │
       └─────────────────┴──────────────────┘
                               │
                    ┌──────────▼───────────┐
                    │   CrawlPipeline       │
                    │  多平台并行(ThreadPool)   │
                    │  URL去重 → 详情抓取 → 存储 │
                    └──────────┬───────────┘
                               │
                    ┌──────────▼───────────┐
                    │    RAGPipeline         │
                    │  清洗 → 分块 → Embedding   │
                    │  → FAISS 向量索引构建     │
                    └──────────┬───────────┘
                               │
          ┌──────────────────────┼───────────────────────┐
          ▼                      ▼                       ▼
   ┌─────────────┐       ┌─────────────┐       ┌─────────────┐
   │ Tab: 文章生成 │       │ Tab: 卡通配图 │       │ Tab: 发布准备 │
   │              │       │              │       │              │
   │ 选择热点/主题  │       │ 4张3D卡通图  │       │ 登录/预览/发布│
   │      ↓        │       │              │       │      ↓        │
   │ ContentAgent  │       │ wanx2.1-t2i  │       │ ToutiaoPublisher│
   │ 9步生成流水线 │       │ 异步生成     │       │ Playwright    │
   └──────┬───────┘       └──────┬───────┘       └──────┬───────┘
        │                      │                     │
        └──────────┬───────────┘                     │
                   ▼                                 │
          ┌──────────────┐                          │
          │ GeneratedStore│ ← 文章+配图持久化缓存      │
          │ (JSON)       │                          │
          └──────────────┘                          │
                                                   ▼
                                    ┌───────────────────┐
                                    │  今日头条创作者后台    │
                                    │  微头条/文章 发布     │
                                    └───────────────────┘
```

## 核心模块详解

### 1. 爬虫模块 (`crawlers/`)

**基类 `BaseCrawler`** 定义统一爬虫接口：

```
BaseCrawler
 ├── search(keyword, max_count) → ArticleData[]    # 搜索（子类实现）
 ├── crawl(keywords, max_count) → ArticleData[]     # 编排：搜索→去重→时效过滤→详情抓取→存储
 ├── fetch_content_http(url) → str                # HTTP详情页抓取（无浏览器开销）
 ├── fetch_detail_from_page(page, url) → (str, metrics)  # Playwright详情页抓取（回退）
 └── _BrowserPool                                  # 线程安全浏览器实例池（同线程复用）
```

**5 个平台爬虫**：

| 平台 | 类 | 搜索策略 | 特殊处理 |
|------|-----|---------|----------|
| 今日头条 | `ToutiaoCrawler` | 移动端API | 质量评分(阅读/点赞/评论/收藏) |
| 知乎 | `ZhihuCrawler` | Cookie登录+搜索 | 需要单独登录获取Cookie |
| 微信公众号 | `WechatCrawler` | 搜狗微信搜索 | 公开文章无需登录 |
| 百家号 | `BaijiahaoCrawler` | 百家号搜索 | - |
| 36氪 | `Kr36Crawler` | 36氪网站 | - |

**反爬与优化策略**：
- 随机延迟（搜索 3-6s / 详情 1-2s）
- Playwright stealth 注入反检测
- 两阶段详情抓取：HTTP 批量优先（极快）→ 失败的用 Playwright 并发标签页回退
- 浏览器池按线程复用，避免反复启动开销
- 时效过滤：支持 ISO/时间戳/中文日期/相对时间解析，可配置最大天数

### 2. RAG 知识库 (`rag/`)

```
原始 HTML
    │
    ▼ TextCleaner.clean()
    │  HTML → Markdown（markdownify）
    │  去广告/噪声/特殊字符
    │  智能正文提取（article/content容器优先）
    │
    ▼ TextChunker.chunk_text()
    │  按段落分割（chunk_size=500, overlap=100）
    │  过长块在句子边界切分
    │
    ▼ DashScopeEmbedder.embed_texts()
    │  批量调用 DashScope API（batch_size=25）
    │  v1/v2/v3 兼容处理
    │  403额度用完直接抛异常（不静默填零向量）
    │
    ▼ FAISSVectorStore
    │  L2归一化 + IndexFlatIP = 余弦相似度
    │  增量追加 / 全量重建 / 维度变更自动检测
    │  零向量清理（embedding API失败产生）
    │  持久化：faiss.index + meta.json + indexed_urls.json
    │
    ▼ Retriever.retrieve(query, top_k)
    │  查询向量化 → FAISS search → Top-K 结果 + 上下文拼接
```

**去重引擎 (`rag/dedup.py`)** — 三级策略，零 token 消耗：

| 层级 | 方法 | 阈值 | 说明 |
|------|------|------|------|
| L1 | URL 精确匹配 | - | 同一URL直接判重 |
| L2 | 标题 Jaccard 相似度 | >0.6 | 中文分词后计算集合交集/并集 |
| L3 | 正文 SimHash Hamming 距离 | <=3 | 64位局部敏感哈希，汉明距离 |

分词策略：优先 jieba → 降级为双字滑动窗口+标点分割（零依赖）

### 3. 文章生成 Agent (`agent/`)

**ContentAgent** — 9 步生成流水线：

```
Step 1: 确定话题
   ├─ 自定义主题（优先）
   └─ HotTopicTool 多源聚合热点（头条热榜 + 微博热搜 + 百度热搜）
       ↓ 关键词快速过滤 → LLM语义扩展 → LLM智能精选

Step 2: RAG 检索
   └─ RagRetrieveTool: 查询向量化 → FAISS search → Top-K 参考素材
       （自动检测新文章增量索引）

Step 3: 生成大纲 (JSON)
   └─ OUTLINE_PROMPT: theme / hook(悬念开头) / points(3~5个要点+目标字数) / ending(互动结尾)

Step 4: 逐段扩写正文 (~1000字)
   └─ ARTICLE_PROMPT: 大纲 + RAG参考素材 → qwen3-max 口语化微头条风格
       写作人设：口语化、短句、个人案例穿插、emoji点缀、互动引导

Step 5: 全文润色
   └─ REFINE_PROMPT: 增加口语化、优化节奏、加强情绪共鸣

Step 6: 字数校验
   └─ 目标范围 [900, 1100] 字
       · 不足 → LLM 补充细节
       · 过多 → 截断到最大值附近

Step 7: 敏感词检测

Step 8: 标题优化 (TitleGenerator)
   ├─ 生成 N 个候选标题（数字冲击型/痛点共鸣型/反常识型/悬念钩子型/猎奇型）
   └─ LLM 四维评分选优（好奇心/情绪/数字感/平台适配度）→ 最佳标题

Step 9: 提取 4 个配图场景
   └─ SCENE_EXTRACT_PROMPT: 从正文中提取适合3D卡通表现的关键场景描述
```

### 4. 图片生成 (`image_gen/`)

**WanxImageGenerator** — 通义万相异步文生图：

```
场景描述
    │
    ▼ CartoonPromptBuilder.build()
    │  统一前缀: "3D卡通风格, 明亮色彩, 扁平插画, 简洁背景"
    │  + 场景描述 + 主题关键词
    │
    ▼ WanxImageGenerator.generate()
    │
    ├─ Step 1: 提交异步任务 (POST dashscope API)
    │   model: wanx2.1-t2i-turbo
    │   size: 1024*1024 | style: <3D卡通>
    │   negative_prompt: "模糊, 低质量, 变形..."
    │   返回 task_id 列表
    │
    ├─ Step 2: 并行轮询所有任务 (GET task/{id})
    │   每 5 秒查询一次，最多 60 次（5分钟超时）
    │   SUCCEEDED → 获取图片 URL
    │
    └─ Step 3: 下载图片到本地 output/images/
        返回文件路径列表
```

### 5. 自动发布 (`publisher/`)

**ToutiaoPublisher** — Playwright 浏览器自动化：

```
登录流程:
  加载已保存Cookie → 访问 mp.toutiao.com
  → Cookie有效? → 自动登录成功,刷新保存最新Cookie
  → Cookie过期? → 弹出扫码窗口 → 等待扫码 → 保存Cookie

发布流程 (以微头条为例):
  ① 加载Cookie → 导航到微头条发布页 (PC端)
  ② wait_for_selector 等待 SPA 编辑器渲染完成 (最多20s)
 ③ 关闭发布助手弹窗 (ESC ×5次 → JS强制移除遮罩层)
  ④ 填写编辑器 (div.syl-editor div.ProseMirror[contenteditable="true"])
     JS execCommand('insertHTML') 插入完整内容
  ⑤ 上传配图 (base64 → ClipboardEvent('paste') 触发粘贴事件)
 ⑥ 添加位置 (div.position-select 组件 → 输入"龙华区")
 ⑦ 勾选AI声明 ("引用AI" checkbox)
  ⑧ 点击发布按钮 (button.publish-content)
  ⑧ wait_for_function 等待发布响应 (URL变化/提示弹窗/按钮disabled)
  ⑧ 保存结果截图
```

**错误诊断机制** — 找不到元素 = 代码 bug，立即中断：
- 保存页面截图 (.png)
- 保存完整 HTML (.html)
- 保存当前 URL (.url)
- 打印页面上所有 checkbox/button/contenteditable/input 元素列表
- 抛出 `ElementNotFoundError` 附带详细诊断路径

### 6. Web UI (`webui/`)

**5 个 Tab 页面**：

| Tab | 文件 | 功能 |
|-----|------|------|
| 配置管理 | `config_tab.py` | 查看/修改 models.yaml 配置 |
| 爬虫控制 | `crawler_tab.py` | 平台选择/关键词/启动爬虫/RAG管理/知乎登录 |
| 文章生成 | `generate_tab.py` | 热点选择/自定义主题 → 一键生成文章+4张配图 |
| 卡通配图 | `image_tab.py` | 预览和重新生成配图 |
| 发布准备 | `publish_tab.py` | 登录管理/文章选择/一键发布/批量发布/发布历史 |

**Gradio 线程安全**：Playwright 同步 API 必须在独立线程中运行（避免 asyncio/greenlet 冲突），通过 `run_sync_in_thread()` 统一处理。

## 快速开始

### 1. 环境准备

```bash
conda create -n coze python=3.10 -y
conda activate coze
pip install -r requirements.txt
playwright install chromium
```

### 2. 配置 API Key

```powershell
# Windows PowerShell（永久）
setx DASHSCOPE_API_KEY "你的DashScope API Key"

# Linux / macOS
export DASHSCOPE_API_KEY="你的DashScope API Key"
```

API Key 获取：https://bailian.console.aliyun.com/

> 代理（`HTTP_PROXY`、`HTTPS_PROXY`）同样通过环境变量设置，非必需

### 3. 启动

```bash
conda activate coze
python main.py
```

访问 http://127.0.0.1:7860

## 使用流程

1. **配置管理** Tab: 查看和修改模型配置
2. **爬虫控制** Tab: 选择平台和关键词，启动爬虫 → 自动构建 RAG 知识库
3. **文章生成** Tab: 选择热点或输入主题，一键生成文章 + 4 张 3D 卡通配图
4. **卡通配图** Tab: 预览和重新生成配图
5. **自动发布** Tab: 登录头条号 → 选择文章 → 一键发布到今日头条

## 项目结构

```
coze/
├── main.py                      # 入口（命令行参数: --port / --host / --share）
├── config/
│   ├── settings.py               # 全局配置单例（从 models.yaml + 环境变量加载）
│   └── models.yaml               # 所有运行时参数集中配置
├── models/
│   ├── article.py                # 数据模型 (ArticleData / GeneratedArticle / ArticleMetrics)
│   ├── article_store.py           # 文章存储 (JSON持久化 / 去重 / 过期清理)
│   ├── generated_store.py         # 生成文章缓存
│   └── pipeline.py               # Pipeline 数据模型 (StageResult / PipelineContext)
├── crawlers/
│   ├── base.py                   # 爬虫基类 (BrowserPool / BaseCrawler / 反爬 / 详情批抓取)
│   ├── toutiao_crawler.py        # 今日头条爬虫
│   ├── zhihu_crawler.py          # 知乎爬虫
│   ├── wechat_crawler.py         # 微信公众号爬虫
│   ├── baijiahao_crawler.py      # 百家号爬虫
│   ├── kr36_crawler.py           # 36氪爬虫
│   └── quality_scorer.py          # 文章质量评分
├── rag/
│   ├── cleaner.py                # 文本清洗 (HTML→Markdown / 正文提取 / 去广告)
│   ├── chunker.py                # 文本分块 (段落分割 / 重叠保留)
│   ├── embedder.py               # DashScope Embedding 封装
│   ├── vectorstore.py            # FAISS 向量索引管理 (增/删/查/存/载)
│   ├── retriever.py              # 语义检索封装
│   └── dedup.py                 # 语义去重 (SimHash + Jaccard, 零token)
├── agent/
│   ├── content_agent.py          # 核心 Agent (9步生成流水线)
│   ├── prompts.py                # Prompt模板库 (系统人设/大纲/正文/润色/标题/场景)
│   ├── tools.py                  # Agent工具 (RAG检索 / 热点聚合)
│   ├── title_gen.py              # 爆款标题生成器 (多候选+LLM评分)
│   └── pipeline.py               # Pipeline 编排器 (CrawlPipeline / RAGPipeline)
├── image_gen/
│   ├── cartoon_gen.py            # wanx2.1-t2i-turbo 异步图片生成
│   ├── prompt_builder.py         # 3D卡通 Prompt 构建器
│   └── scene_extractor.py        # 文章→场景描述提取
├── publisher/
│   ├── publisher_base.py         # 发布器基类 (抽象接口 / PublishResult)
│   └── toutiao_publisher.py      # 头条发布器 (登录/编辑器/上传/位置/AI声明/发布/诊断)
├── webui/
│   ├── app.py                    # Gradio 应用入口 (创建/启动/端口冲突处理)
│   └── tabs/
│       ├── config_tab.py          # Tab 1: 配置管理
│       ├── crawler_tab.py         # Tab 2: 爬虫控制 (含RAG管理/知乎登录)
│       ├── generate_tab.py        # Tab 3: 文章生成 (含热点/配图/自动发布)
│       ├── image_tab.py            # Tab 4: 卡通配图
│       └── publish_tab.py          # Tab 5: 自动发布 (登录/预览/发布/历史)
├── utils/
│   ├── logger.py                 # loguru 日志配置
│   ├── http_client.py            # HTTP 客户头
│   ├── cookie_manager.py         # Cookie 持久化管理
│   ├── image_cache.py            # 配图缓存
│   └── text_utils.py             # 文本工具 (字数统计/敏感词/清洗)
├── data/
│   ├── cookies/                  # 各平台登录 Cookie
│   ├── store/                    # 爬取文章 JSON
│   ├── db/                       # FAISS 向量索引 + 元数据
│   ├── raw/                       # 各平台原始爬取数据
│   └── publisher/                 # 发布诊断文件 (截图/HTML/URL)
└── output/
    └── images/                   # 生成的配图
```

## 模型配置

所有模型名称在 `config/models.yaml` 中集中管理，更换模型只需修改此文件：

| 模型类型 | 当前配置 | 说明 |
|----------|----------|------|
| LLM | `qwen3-max-2026-01-23` | DashScope 兼容 OpenAI 接口 |
| Embedding | `text-embedding-v2` | 1536维, batch_size=25 |
| 图片生成 | `wanx2.1-t2i-turbo` | 1024*1024, 3D卡通风格, 异步模式 |

## 工程亮点

- **零成本语义去重**: SimHash(64位指纹) + Jaccard(标题分词)，不调任何 LLM API 即可实现三级去重
- **增量向量索引**: FAISS 支持追加向量，自动检测维度变更并触发全量重建
- **两阶段详情抓取**: HTTP 无浏览器批量抓取(极速) + Playwright 并发标签页回退(兼容动态渲染)
- **浏览器池复用**: 按线程共享浏览器实例，避免每次创建/销毁的开销
- **Gradio 线程安全**: Playwright 同步 API 在独立线程中运行，解决 asyncio/greenlet 冲突
- **Cookie 自动登录**: 首次扫码后持久保存，后续启动自动复用，过期才需重新扫码
- **SPA 渲染等待**: 发布页是 React SPA，使用 `wait_for_selector` 等待编辑器 DOM 出现而非固定 sleep
- **发布失败即中断**: 找不到任何元素 = 代码 bug，自动截图+保存 HTML+打印元素列表，不做容错猜测
