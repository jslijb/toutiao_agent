"""Agent 工具定义 - 热点获取、RAG 检索、场景提取等"""
from __future__ import annotations

import json
from typing import Optional

from loguru import logger

from rag.retriever import Retriever
from rag.vectorstore import FAISSVectorStore
from config.settings import settings


class RagRetrieveTool:
    """RAG 知识库语义检索工具（支持增量索引）"""

    def __init__(self, retriever: Optional[Retriever] = None):
        if retriever:
            self.retriever = retriever
            self._vectorstore = None
        else:
            self._vectorstore = FAISSVectorStore()
            loaded = self._vectorstore.load()
            need_rebuild = False

            if loaded and self._vectorstore.index is not None:
                # 检测维度是否匹配配置
                index_dim = self._vectorstore.index.d
                config_dim = settings.models.embedding.dimension
                if index_dim != config_dim:
                    logger.warning(
                        f"[RAG] 维度不匹配: 索引={index_dim}d, 配置={config_dim}d, 需要重建"
                    )
                    need_rebuild = True
                elif self._vectorstore.total == 0:
                    need_rebuild = True
            elif not loaded or self._vectorstore.total == 0:
                need_rebuild = True

            if need_rebuild:
                self._auto_build_index(self._vectorstore)
            self.retriever = Retriever(vectorstore=self._vectorstore)

    def _auto_build_index(self, vs: FAISSVectorStore):
        """自动从 ArticleStore 构建 RAG 索引（增量模式，自动处理维度变更）"""
        try:
            from agent.pipeline import RAGPipeline
            logger.info("[RAG] 索引需要构建/重建，自动执行增量重建...")
            pipeline = RAGPipeline()
            pipeline.vectorstore = vs
            # 使用 rebuild_incremental 而非 run，自动检测维度变更
            result = pipeline.rebuild_incremental()
            last = result.get_last_result()
            if last.status.value == "success":
                logger.info(f"[RAG] 自动构建完成: {last.message}")
            else:
                logger.warning(f"[RAG] 自动构建失败: {last.message}")
        except Exception as e:
            logger.warning(f"[RAG] 自动构建索引失败: {e}")

    def _ensure_index_fresh(self):
        """检查是否有新文章需要增量索引"""
        if not self._vectorstore:
            return

        try:
            from models.article_store import get_article_store
            store = get_article_store()
            indexed_urls = self._vectorstore.indexed_urls

            # 找出未索引的文章
            store_urls = {a.url for a in store.get_all() if a.url}
            new_urls = store_urls - indexed_urls

            if len(new_urls) >= 1:
                logger.info(f"[RAG] 检测到 {len(new_urls)} 篇新文章，增量索引...")
                self._auto_build_index(self._vectorstore)
                # 重建后刷新 retriever
                self.retriever = Retriever(vectorstore=self._vectorstore)
        except Exception as e:
            logger.debug(f"[RAG] 索引刷新检查失败: {e}")

    def retrieve(self, query: str, top_k: int = 10) -> list[dict]:
        """从 RAG 知识库中检索与给定主题相关的文章片段"""
        self._ensure_index_fresh()
        return self.retriever.retrieve(query, top_k)

    def retrieve_context(self, query: str, top_k: int = 10) -> str:
        """检索并拼接为上下文文本"""
        self._ensure_index_fresh()
        return self.retriever.retrieve_with_context(query, top_k)


class HotTopicTool:
    """热点获取工具 - 多源聚合 + 关键词过滤 + LLM 智能筛选"""

    # 领域关键词库（与项目定位：职场/副业/个人成长 相关）
    DOMAIN_KEYWORDS = [
        # 职场
        "职场", "工作", "打工", "996", "007", "内卷", "裁员", "跳槽", "升职",
        "加薪", "面试", "简历", "领导", "老板", "同事", "离职", "创业", "就业",
        "招聘", "失业", "转行", "加班", "摸鱼", "上班", "下班", "通勤",
        "社保", "公积金", "劳动", "辞退", "绩效", "考核", "涨薪", "降薪",
        # 副业/赚钱
        "副业", "搞钱", "赚钱", "收入", "理财", "投资", "兼职", "自由职业",
        "变现", "财务自由", "月薪", "年薪", "薪资", "工资", "存款", "负债",
        "借贷", "消费", "花销", "省钱", "带货", "直播", "电商",
        # 个人成长
        "成长", "自律", "提升", "学习", "读书", "认知", "思维",
        "坚持", "努力", "奋斗", "逆袭", "翻盘", "改变", "突破",
        "焦虑", "压力", "抑郁", "选择", "方向", "目标", "规划",
        # 科技/趋势
        "AI", "ChatGPT", "人工智能", "大模型", "数字化",
        "裁员潮", "经济", "消费降级", "就业难", "35岁",
    ]

    def __init__(self):
        self.sources = {
            "toutiao": self._fetch_toutiao_hot,
            "weibo": self._fetch_weibo_hot,
            "baidu": self._fetch_baidu_hot,
        }

    def fetch_hot_topics(self, sources: list[str] | None = None, max_topics: int = 5,
                         keywords: list[str] | None = None, use_llm_filter: bool = True) -> list[dict]:
        """
        获取实时热点话题列表（经过关键词过滤 + LLM 智能筛选）

        Args:
            sources: 热点源列表
            max_topics: 最大返回数量
            keywords: 自定义过滤关键词（默认使用配置中的 crawler.keywords）
            use_llm_filter: 是否使用 LLM 智能筛选（默认开启）
        """
        sources = sources or settings.hot_topics.sources
        # 合并配置关键词 + 内置领域关键词（去重）
        crawler_kws = getattr(settings.crawler, "keywords", [])
        filter_keywords = list(set((keywords or crawler_kws) + self.DOMAIN_KEYWORDS))

        # 第一步：广量获取各平台热榜
        all_topics = []
        for source in sources:
            if source in self.sources:
                try:
                    topics = self.sources[source]()
                    all_topics.extend(topics)
                    logger.info(f"[热点] {source}: 获取 {len(topics)} 条原始热点")
                except Exception as e:
                    logger.error(f"[热点] {source} 获取失败: {e}")

        if not all_topics:
            return []

        # 第一步半：跨平台去重（相同标题只保留热度最高的）
        before_dedup = len(all_topics)
        all_topics = self._dedup_topics(all_topics)
        logger.info(f"[热点] 跨平台去重: {before_dedup} → {len(all_topics)} 条")

        # 保存原始列表供语义扩展使用
        self._all_raw_topics = all_topics

        # 第二步：关键词快速过滤（命中任一关键词即保留）
        filtered = self._keyword_filter(all_topics, filter_keywords)
        logger.info(f"[热点] 关键词过滤: {len(all_topics)} → {len(filtered)} 条")

        # 第三步：LLM 语义扩展（从关键词未命中的热点中，发现可切入的话题）
        if use_llm_filter:
            before = len(filtered)
            filtered = self._semantic_expand(filtered, filter_keywords)
            expanded = len(filtered) - before
            if expanded > 0:
                logger.info(f"[热点] 语义扩展: +{expanded} 条")

        if not filtered:
            logger.warning("[热点] 过滤后无相关热点，建议检查关键词配置或等待热点更新")
            # 回退时也只取热度 Top5，避免返回大量无关热点
            all_topics.sort(key=lambda x: x.get("heat", 0), reverse=True)
            return all_topics[:5]

        # 第四步：LLM 智能筛选精选（从候选中选出最贴合领域的高质量热点）
        if use_llm_filter and len(filtered) > max_topics:
            try:
                filtered = self._llm_filter(filtered, max_topics=max_topics * 2)
                logger.info(f"[热点] LLM 精选: → {len(filtered)} 条")
            except Exception as e:
                logger.warning(f"[热点] LLM 筛选失败，回退到热度排序: {e}")

        # 第五步：按热度排序取 Top N
        filtered.sort(key=lambda x: x.get("heat", 0), reverse=True)
        return filtered[:max_topics]

    def _keyword_filter(self, topics: list[dict], keywords: list[str]) -> list[dict]:
        """关键词快速过滤：标题命中任一关键词即保留"""
        result = []
        for t in topics:
            title = t.get("title", "")
            title_lower = title.lower()
            matched_kw = ""
            for kw in keywords:
                if kw.lower() in title_lower:
                    matched_kw = kw
                    break
            if matched_kw:
                t["matched_keyword"] = matched_kw
                result.append(t)
        return result

    def _dedup_topics(self, topics: list[dict]) -> list[dict]:
        """跨平台去重：相同标题只保留热度最高的那条"""
        seen: dict[str, dict] = {}
        for t in topics:
            title = t.get("title", "").strip()
            if not title:
                continue
            # 标题作为去重 key（归一化：去空格、小写）
            key = title.replace(" ", "").lower()
            if key not in seen or t.get("heat", 0) > seen[key].get("heat", 0):
                seen[key] = t
        return list(seen.values())

    def _semantic_expand(self, topics: list[dict], keywords: list[str]) -> list[dict]:
        """
        语义扩展：用 LLM 从未被关键词命中的热点中，
        找出能从"职场/副业/个人成长"角度切入的话题
        """
        # 先分离已命中和未命中的
        matched_titles = {t["title"] for t in topics}
        unmatched = [t for t in self._all_raw_topics if t["title"] not in matched_titles]

        if not unmatched or len(topics) >= 15:
            return topics

        from openai import OpenAI
        client = OpenAI(
            api_key=settings.dashscope_api_key,
            base_url=settings.models.llm.api_base,
        )

        candidates = "\n".join(
            f"{i+1}. {t['title']}"
            for i, t in enumerate(unmatched[:40])  # 最多看40条
        )

        prompt = f"""你是自媒体选题专家，专注「职场、副业、个人成长」领域。

以下热点虽然标题没有直接包含领域关键词，但有些可以从职场/赚钱/成长角度切入解读。

候选热点：
{candidates}

请选出能从"职场经验、副业搞钱、个人成长反思"角度切入的话题序号（JSON数组），最多10个：
[2, 5, 8, ...]"""

        try:
            resp = client.chat.completions.create(
                model=settings.models.llm.name,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=256,
            )
            import re
            content = resp.choices[0].message.content.strip()
            match = re.search(r'\[.*?\]', content, re.DOTALL)
            if match:
                indices = json.loads(match.group())
                for idx in indices:
                    idx_int = int(idx) - 1
                    if 0 <= idx_int < len(unmatched):
                        t = unmatched[idx_int]
                        t["matched_keyword"] = "LLM语义匹配"
                        topics.append(t)
        except Exception as e:
            logger.warning(f"[热点] 语义扩展失败: {e}")

        return topics

    def _llm_filter(self, topics: list[dict], max_topics: int = 10) -> list[dict]:
        """使用 LLM 从候选热点中筛选与职场/副业/个人成长最相关的话题"""
        from openai import OpenAI

        client = OpenAI(
            api_key=settings.dashscope_api_key,
            base_url=settings.models.llm.api_base,
        )

        # 构建候选列表
        candidates = "\n".join(
            f"{i+1}. {t['title']} (来源:{t.get('source','?')}, 热度:{t.get('heat',0)})"
            for i, t in enumerate(topics)
        )

        prompt = f"""你是一个自媒体选题专家，专注于「职场、副业、个人成长」领域。

请从以下实时热点中，筛选出能与本领域结合的话题——即可以通过"职场视角"、"搞钱思路"或"个人成长反思"来解读的热点。

候选热点：
{candidates}

筛选标准：
1. 该热点能自然关联到职场经验、副业思路、个人成长中的至少一个
2. 优先选择热度高、受众广的话题
3. 即使是娱乐/社会新闻，只要能从"职场/赚钱/成长"角度切入也可保留
4. 排除纯娱乐八卦、体育赛事等无法关联的话题

请输出筛选结果的序号（JSON数组），最多{max_topics}个：
[1, 3, 5, ...]"""

        resp = client.chat.completions.create(
            model=settings.models.llm.name,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=256,
        )
        content = resp.choices[0].message.content.strip()

        # 解析 JSON 数组
        import re
        match = re.search(r'\[.*?\]', content, re.DOTALL)
        if match:
            indices = json.loads(match.group())
            result = []
            for idx in indices:
                idx_int = int(idx) - 1  # 转为0-based
                if 0 <= idx_int < len(topics):
                    result.append(topics[idx_int])
            return result

        return topics[:max_topics]

    def _fetch_toutiao_hot(self) -> list[dict]:
        """获取今日头条热榜（信息流接口）"""
        try:
            import requests
            url = "https://www.toutiao.com/api/pc/feed/?category=news_hot"
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Referer": "https://www.toutiao.com/",
            }
            resp = requests.get(url, headers=headers, timeout=10)
            data = resp.json()
            return [
                {"title": item.get("title", ""), "heat": item.get("hot_value", 0), "source": "toutiao"}
                for item in data.get("data", [])[:20]
                if item.get("title")
            ]
        except Exception as e:
            logger.warning(f"[热点] 头条热榜获取失败: {e}")
            return []

    def _fetch_weibo_hot(self) -> list[dict]:
        """获取微博热搜（通过微博移动端页面）"""
        try:
            import requests
            # 使用微博热搜移动端接口
            url = "https://m.weibo.cn/api/container/getIndex?containerid=106003type%3D25%26t%3D3%26disable_hot%3D1%26filter_type%3Drealtimehot"
            headers = {
                "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1",
                "Referer": "https://m.weibo.cn/",
            }
            resp = requests.get(url, headers=headers, timeout=10)
            data = resp.json()

            topics = []
            cards = data.get("data", {}).get("cards", [])
            for card in cards:
                card_group = card.get("card_group", [])
                for item in card_group:
                    desc = item.get("desc", "")
                    title = desc if desc else item.get("title_sub", "")
                    if not title:
                        continue
                    # 微博热度格式: "123.4万" 或 "1234"
                    hot_str = item.get("desc", "")
                    try:
                        heat = float("".join(c for c in hot_str if c.isdigit() or c == "."))
                        if "万" in hot_str:
                            heat *= 10000
                    except (ValueError, TypeError):
                        heat = 0
                    topics.append({
                        "title": title.strip(),
                        "heat": int(heat),
                        "source": "weibo",
                    })

            if not topics:
                # 备选：用百度热搜的微博 tab
                topics = self._fetch_weibo_from_baidu()

            return topics[:20]
        except Exception as e:
            logger.warning(f"[热点] 微博热搜获取失败: {e}")
            return self._fetch_weibo_from_baidu()

    def _fetch_weibo_from_baidu(self) -> list[dict]:
        """从百度热搜获取微博相关话题（备选方案）"""
        try:
            import requests
            url = "https://top.baidu.com/api/board?platform=wise&tab=realtime"
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Referer": "https://top.baidu.com/",
            }
            resp = requests.get(url, headers=headers, timeout=10)
            data = resp.json()
            topics = []
            for card in data.get("data", {}).get("cards", []):
                for item in card.get("content", []):
                    inner = item.get("content", [])
                    if isinstance(inner, list):
                        for sub in inner:
                            word = sub.get("word", "")
                            if word:
                                topics.append({
                                    "title": word,
                                    "heat": sub.get("hotScore", 0),
                                    "source": "weibo_baidu",
                                })
            return topics[:20]
        except Exception as e:
            logger.warning(f"[热点] 微博(百度备选)获取失败: {e}")
            return []

    def _fetch_baidu_hot(self) -> list[dict]:
        """获取百度热搜"""
        try:
            import requests
            url = "https://top.baidu.com/api/board?platform=wise&tab=realtime"
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Referer": "https://top.baidu.com/",
            }
            resp = requests.get(url, headers=headers, timeout=10)
            data = resp.json()
            topics = []
            for card in data.get("data", {}).get("cards", []):
                for item in card.get("content", []):
                    # 百度热搜数据有两层嵌套: content -> content[]
                    inner = item.get("content", [])
                    if isinstance(inner, list):
                        for sub in inner:
                            word = sub.get("word", "")
                            if word:
                                topics.append({
                                    "title": word,
                                    "heat": sub.get("hotScore", 0),
                                    "source": "baidu",
                                })
                    else:
                        word = item.get("word", "")
                        if word:
                            topics.append({
                                "title": word,
                                "heat": item.get("hotScore", 0),
                                "source": "baidu",
                            })
            return topics[:20]
        except Exception as e:
            logger.warning(f"[热点] 百度热搜获取失败: {e}")
            return []



