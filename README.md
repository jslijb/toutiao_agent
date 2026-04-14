# 头条内容 Agent

基于 qwen-agent 框架的 Python Agent 系统，实现从多平台爬取高质量文章 → 构建 RAG 知识库 → AI 生成今日头条风格微头条 → 自动生成卡通配图的完整流程。

## 功能

- **多平台爬虫**: 爬取今日头条、知乎、微信公众号、百家号、36氪五大平台文章
- **RAG 知识库**: FAISS 向量存储 + DashScope Embedding
- **AI 文章生成**: qwen3-max 多步生成（大纲→扩写→润色→标题优化）
- **卡通配图**: wanx2.1-t2i-turbo 异步生成 3D 卡通风格图片
- **Web 控制台**: Gradio 5个Tab页面管理全流程

## 快速开始

### 1. 环境准备

```bash
conda create -n coze python=3.10 -y
conda activate coze
pip install -r requirements.txt
playwright install chromium
```

### 2. 配置 API Key

通过系统环境变量设置 DashScope API Key（无需 `.env` 文件）：

```powershell
# Windows PowerShell（临时）
$env:DASHSCOPE_API_KEY="你的DashScope API Key"

# Windows PowerShell（永久）
setx DASHSCOPE_API_KEY "你的DashScope API Key"

# Windows CMD
set DASHSCOPE_API_KEY=你的DashScope API Key

# Linux / macOS
export DASHSCOPE_API_KEY="你的DashScope API Key"
```

API Key 获取：https://bailian.console.aliyun.com/

> 代理（`HTTP_PROXY`、`HTTPS_PROXY`）同样通过环境变量设置，非必需

### 3. 启动
coze 是我的虚拟环境名称，这里需要更换自己的虚拟环境
```bash
conda activate coze
python main.py
```

访问 http://127.0.0.1:7860

## 使用流程

1. **配置管理** Tab: 查看和修改模型配置
2. **爬虫控制** Tab: 选择平台和关键词，启动爬虫
3. **文章生成** Tab: 选择热点或输入主题，一键生成
4. **卡通配图** Tab: 预览和重新生成配图
5. **自动发布** Tab: 登录头条账号，一键发布到今日头条（支持微头条和文章）
   - Cookie 自动登录：首次扫码后 Cookie 持久保存，后续启动自动复用，过期时才需重新扫码
   - 微头条发布：自动填写内容 + 上传配图 + 添加位置/话题/AI声明 → 一键发布
   - 发布失败时自动保存截图+HTML诊断文件，便于排查问题

## 项目结构

```
├── main.py              # 入口
├── config/              # 配置（models.yaml，API Key 通过环境变量）
├── models/              # 数据模型
├── crawlers/            # 5个平台爬虫
├── rag/                 # RAG 知识库（清洗/分块/Embedding/FAISS）
├── agent/               # 文章生成 Agent + Pipeline
├── image_gen/           # 图片生成（wanx2.1-t2i-turbo）
├── webui/               # Gradio Web UI
├── utils/               # 工具（日志/HTTP/Cookie）
├── data/                # 数据存储
└── output/              # 输出（文章+图片）
```

## 模型配置

所有模型名称在 `config/models.yaml` 中集中管理，更换模型只需修改此文件：

- LLM: `qwen3-max-2026-01-23`
- Embedding: `text-embedding-v3`
- 图片: `wanx2.1-t2i-turbo`


## 工程亮点
- 由于从多个平台爬取数据会存在重复的文章，需要去重。零成本语义去重：SimHash + Jaccard 算法，无需调用 LLM API
- Gradio 线程安全：强制浏览器重建策略解决 greenlet 绑定冲突
- 配置驱动：所有模型和参数集中在 models.yaml，支持热切换
- 增量索引：百万级向量库支持增量更新，避免全量重建
- 两阶段详情抓取：列表页 API 优先，详情页 Playwright 并发回退
## 工程待优化
- 爬取文章时，爬虫速度比较慢，由于需要应对反爬加了等待时间
- 文章质量不高，只有阅读量+评论量+点赞量这类数据，打分用的三个指标加权平均，权重是拍的，没有验证
- 分析低质量文章功能待新增
- 卡通图片质量一般，需要优化画图提示词和更换更好的文生图模型
- 自动发布只验证了微头条，文章类体验不好，需要调整格式
- 主题可以自定义也可是获取热点，只是每次获取的热点和自己写作主题方向不相关，只有极少数情况相关
