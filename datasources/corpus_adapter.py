"""静态数据集适配器 - nlp_chinese_corpus

支持自动下载样本数据集到 data/corpus/ 目录，
也支持手动配置完整数据集路径（models.yaml）。
"""
from __future__ import annotations

import gzip
import json
import time
from pathlib import Path
from typing import Optional

import httpx
from loguru import logger

from config.settings import PROJECT_ROOT, settings
from datasources.base import BaseSourceAdapter
from datasources.models import SourceHealth, SourceStatus, ImportResult
from models.article import ArticleData, ArticleMetrics
from models.article_store import get_article_store

CORPUS_DIR = PROJECT_ROOT / "data" / "corpus"

DATASET_INFO = {
    "news2016zh": {
        "filename": "news2016zh_sample.jsonl",
        "desc": "新闻语料（样本约5万条，完整版250万条）",
        "sample_urls": [
            "https://modelscope.cn/api/v1/datasets/zhujiee/news2016zh/repo?Revision=master&FilePath=news2016zh_train.jsonl",
            "https://hf-mirror.com/datasets/clue/news2016zh/resolve/main/news2016zh_train.jsonl",
        ],
        "max_sample_lines": 50000,
    },
    "webtext2019zh": {
        "filename": "webtext2019zh_sample.jsonl",
        "desc": "社区问答（样本约5万条，完整版410万条）",
        "sample_urls": [
            "https://modelscope.cn/api/v1/datasets/zhujiee/webtext2019zh/repo?Revision=master&FilePath=webtext2019zh_train.jsonl",
            "https://hf-mirror.com/datasets/clue/webtext2019zh/resolve/main/webtext2019zh_train.jsonl",
        ],
        "max_sample_lines": 50000,
    },
}


class CorpusAdapter(BaseSourceAdapter):
    """nlp_chinese_corpus 静态数据集适配器"""

    @property
    def name(self) -> str:
        return "corpus"

    def fetch(
        self,
        dataset_type: str = "news2016zh",
        resume: bool = True,
        max_count: int = 0,
        batch_size: int = 1000,
    ) -> list[ArticleData]:
        """流式读取数据集，字段映射+质量过滤+批量写入ArticleStore

        Args:
            dataset_type: "news2016zh" 或 "webtext2019zh"
            resume: 是否从断点续传
            max_count: 最大导入条数（0=不限）
            batch_size: 批量写入大小
        """
        start_time = time.time()
        result = ImportResult(dataset_type=dataset_type)

        file_path = self._resolve_dataset_file(dataset_type)
        if not file_path or not file_path.exists():
            msg = f"数据集文件未找到: {dataset_type} (path={file_path})"
            result.error = msg
            logger.info(msg)
            return []

        checkpoint = self._load_checkpoint(dataset_type) if resume else 0
        result.resumed_from = checkpoint

        mapper = self._map_news2016zh if dataset_type == "news2016zh" else self._map_webtext2019zh
        store = get_article_store()
        batch: list[ArticleData] = []
        imported_total = 0

        logger.info(f"开始导入数据集: {dataset_type}, 文件: {file_path}, 断点: {checkpoint}")

        try:
            opener = gzip.open if str(file_path).endswith(".gz") else open
            with opener(file_path, "rt", encoding="utf-8") as f:
                for line_no, line in enumerate(f):
                    if line_no < checkpoint:
                        continue
                    if max_count > 0 and imported_total >= max_count:
                        break

                    result.total_read += 1

                    try:
                        raw = json.loads(line.strip())
                    except json.JSONDecodeError:
                        continue

                    article = mapper(raw)
                    if article is None:
                        result.filtered_quality += 1
                        continue

                    batch.append(article)

                    if len(batch) >= batch_size:
                        saved = self._save_batch(store, batch)
                        result.imported += saved
                        imported_total += saved
                        self._save_checkpoint(dataset_type, line_no + 1)
                        batch = []

                if batch:
                    saved = self._save_batch(store, batch)
                    result.imported += saved
                    imported_total += saved
                    self._save_checkpoint(dataset_type, line_no + 1)

        except Exception as e:
            result.error = str(e)
            logger.error(f"数据集导入异常: {e}")

        result.elapsed_seconds = time.time() - start_time
        logger.info(
            f"数据集导入完成: {dataset_type}, "
            f"读取={result.total_read}, 过滤={result.filtered_quality}, "
            f"导入={result.imported}, 耗时={result.elapsed_seconds:.1f}s"
        )
        return []

    def health_check(self) -> SourceHealth:
        has_any = False
        for ds_type in DATASET_INFO:
            fp = self._resolve_dataset_file(ds_type)
            if fp and fp.exists():
                has_any = True
                break
        if has_any:
            return SourceHealth(name=self.name, status=SourceStatus.available, message="数据集文件就绪")
        return SourceHealth(name=self.name, status=SourceStatus.disabled, message="未下载数据集（可在数据源Tab中点击导入）")

    def download_dataset(
        self,
        dataset_type: str = "news2016zh",
        max_lines: int = 0,
    ) -> Path:
        """下载样本数据集到 data/corpus/ 目录

        优先级：modelscope国内 → hf-mirror → huggingface → 内置示例数据

        Args:
            dataset_type: "news2016zh" 或 "webtext2019zh"
            max_lines: 最多下载行数（0=使用默认5万条样本）

        Returns:
            下载后的文件路径
        """
        if dataset_type not in DATASET_INFO:
            raise ValueError(f"未知数据集: {dataset_type}, 可选: {list(DATASET_INFO.keys())}")

        info = DATASET_INFO[dataset_type]
        CORPUS_DIR.mkdir(parents=True, exist_ok=True)
        target_file = CORPUS_DIR / info["filename"]

        if target_file.exists() and target_file.stat().st_size > 0:
            logger.info(f"数据集已存在: {target_file}，跳过下载")
            return target_file

        max_lines = max_lines or info["max_sample_lines"]
        urls = info.get("sample_urls", [])

        for url in urls:
            try:
                logger.info(f"正在下载数据集样本: {dataset_type} <- {url}")
                logger.info(f"将截取前 {max_lines} 行保存到: {target_file}")
                return self._stream_download(url, target_file, max_lines)
            except Exception as e:
                logger.warning(f"下载失败 ({url}): {e}")
                if target_file.exists():
                    target_file.unlink()
                continue

        logger.warning("所有远程源均不可达，生成内置示例数据作为替代")
        return self._generate_builtin_sample(dataset_type, target_file)

    def _stream_download(self, url: str, target: Path, max_lines: int) -> Path:
        """流式下载，边下载边写，截取前N行"""
        tmp_file = target.with_suffix(".tmp")
        line_count = 0

        with httpx.stream("GET", url, timeout=15, follow_redirects=True) as resp:
            resp.raise_for_status()
            total_size = int(resp.headers.get("content-length", 0))
            if total_size > 0:
                logger.info(f"文件总大小: {total_size / 1024 / 1024:.1f} MB")

            with open(tmp_file, "w", encoding="utf-8") as f:
                buffer = ""
                for chunk in resp.iter_text(chunk_size=65536):
                    buffer += chunk
                    while "\n" in buffer:
                        line, buffer = buffer.split("\n", 1)
                        line = line.strip()
                        if not line:
                            continue
                        f.write(line + "\n")
                        line_count += 1
                        if line_count % 10000 == 0:
                            logger.info(f"  已下载 {line_count}/{max_lines} 行...")
                        if line_count >= max_lines:
                            break
                    if line_count >= max_lines:
                        break

        tmp_file.rename(target)
        logger.info(f"数据集下载完成: {target} ({line_count} 行, {target.stat().st_size / 1024 / 1024:.1f} MB)")
        return target

    def _generate_builtin_sample(self, dataset_type: str, target: Path) -> Path:
        """生成内置示例数据（所有远程源不可达时的兜底方案）"""
        CORPUS_DIR.mkdir(parents=True, exist_ok=True)

        if dataset_type == "news2016zh":
            sample_data = [
                {"title": "AI大模型赋能职场效率提升", "content": "随着人工智能技术的快速发展，大语言模型正在深刻改变职场工作方式。从文档撰写到数据分析，从客户服务到创意设计，AI工具的应用场景不断拓展。企业需要积极拥抱这一技术趋势，培养员工的AI素养，才能在数字化转型中保持竞争力。专家建议，职场人士应重点掌握提示词工程、AI辅助决策等核心技能，将AI作为提升个人价值的工具而非威胁。", "source": "科技日报", "time": "2026-01-15"},
                {"title": "副业收入超过主业的时代来了", "content": "数字经济的蓬勃发展催生了大量灵活就业机会。从自媒体创作到电商运营，从在线教育到远程咨询，越来越多的人通过副业实现了收入多元化。数据显示，2025年有超过40%的白领拥有副业收入，其中15%的人副业收入超过主业。专家提醒，选择副业应结合自身优势和市场需求，避免盲目跟风，同时注意平衡主业与副业的时间精力分配。", "source": "财经周刊", "time": "2026-02-20"},
                {"title": "35岁职场危机如何破局", "content": "35岁被称为职场的分水岭，许多人在这个年龄面临晋升瓶颈、薪资停滞和转型压力。然而，35岁危机并非不可逾越。成功破局的关键在于：一是持续学习，保持知识更新和技术迭代；二是构建不可替代性，深耕专业领域形成壁垒；三是拓展人脉资源，建立行业影响力和个人品牌；四是适时转型，向管理岗或新兴领域发展。记住，年龄不是限制，固化的思维才是。", "source": "人力资源报", "time": "2026-03-10"},
                {"title": "创业失败的十个常见误区", "content": "据统计，90%的创业项目在三年内失败。总结常见误区包括：第一，过度乐观估计市场需求，没有做充分的市场调研和用户验证；第二，资金规划不合理，烧钱速度超出预期而没有预留缓冲；第三，团队组建仓促，合伙人选择不当导致决策内耗；第四，忽视现金流管理，利润为正却因资金链断裂而倒闭；第五，产品过度设计，追求完美而错过市场窗口期；第六，营销投入不足，好产品无人知晓；第七，忽视竞争对手动态；第八，固守最初方案不懂变通；第九，扩张过快管理跟不上；第十，创始人精力分散同时做多个项目。", "source": "创业邦", "time": "2026-01-28"},
                {"title": "远程办公的利与弊深度解析", "content": "后疫情时代，远程办公已成为新常态。其优势包括：节省通勤时间提升效率、工作地点灵活降低生活成本、更少办公室政治干扰专注。但也存在挑战：工作与生活边界模糊容易过度工作、缺少面对面交流影响团队凝聚力、家庭环境干扰注意力、职业发展可能因不在场而受影响、孤独感影响心理健康。建议采用混合办公模式，每周2-3天到岗，并建立明确的工作时间边界和沟通规范。", "source": "管理世界", "time": "2026-04-05"},
            ]
        else:
            sample_data = [
                {"title": "如何用AI工具提升工作效率", "content": "分享我使用ChatGPT、Claude等AI工具提升工作效率的经验。首先在文档撰写方面，AI可以帮助快速生成初稿，大幅减少写作时间。其次在代码开发方面，AI编程助手能显著提升编码速度和减少bug。第三在数据分析方面，AI可以快速处理大量数据并生成可视化报告。关键是要学会写好提示词，将任务分解为AI擅长的小步骤。", "url": "", "author": "效率达人", "star": 85},
                {"title": "从月薪5千到年入百万的复盘", "content": "三年时间从基层员工到年入百万，分享一下我的成长路径和关键决策。第一年专注提升专业技能，考取了PMP和数据分析师认证。第二年主动承担跨部门项目，积累了管理和协调经验。第三年开始做知识付费，把工作经验系统化输出。核心体会是：收入增长的本质是价值创造能力的提升，而不是工作时间的延长。", "url": "", "author": "成长笔记", "star": 120},
                {"title": "裁员潮下如何保持职场安全感", "content": "近期多家大厂裁员引发职场焦虑。我的应对策略是建立三道防线：第一道是核心技能壁垒，成为团队中不可替代的人；第二道是多元收入来源，不把所有鸡蛋放在一个篮子里；第三道是人脉储备，保持与行业内外人士的活跃连接。此外，保持3-6个月的应急储蓄金，做到心态上不慌、行动上有备。", "url": "", "author": "职场观察", "star": 96},
            ]

        with open(target, "w", encoding="utf-8") as f:
            for item in sample_data:
                f.write(json.dumps(item, ensure_ascii=False) + "\n")

        logger.info(f"内置示例数据已生成: {target} ({len(sample_data)} 条)")
        return target

    def _resolve_dataset_file(self, dataset_type: str) -> Optional[Path]:
        """按优先级解析数据集文件路径:
        1. models.yaml 中配置的路径
        2. data/corpus/ 下的样本文件
        3. data/corpus/ 下的完整文件（.jsonl / .jsonl.gz）
        """
        configured = self._get_configured_path(dataset_type)
        if configured and configured.exists():
            return configured

        if dataset_type not in DATASET_INFO:
            return None

        info = DATASET_INFO[dataset_type]
        sample_path = CORPUS_DIR / info["filename"]
        if sample_path.exists():
            return sample_path

        for ext in ["", ".gz"]:
            full_path = CORPUS_DIR / f"{dataset_type}.jsonl{ext}"
            if full_path.exists():
                return full_path

        return sample_path

    def _get_configured_path(self, dataset_type: str) -> Optional[Path]:
        """从配置文件读取数据集路径"""
        cfg = getattr(settings, "datasource", None)
        if cfg is None:
            return None
        if dataset_type == "news2016zh":
            path = getattr(cfg, "corpus_news_path", "")
        elif dataset_type == "webtext2019zh":
            path = getattr(cfg, "corpus_qa_path", "")
        else:
            return None
        return Path(path) if path else None

    def _map_news2016zh(self, raw: dict) -> Optional[ArticleData]:
        """news2016zh 字段映射"""
        title = raw.get("title", "").strip()
        content = raw.get("content", "").strip()
        if not title or not content or len(content) < 200:
            return None
        return ArticleData(
            source="corpus_news",
            title=title,
            content=content,
            author=raw.get("source", ""),
            publish_time=raw.get("time", ""),
            metrics=ArticleMetrics(),
            quality_score=0.5,
            ttl_days=0,
        )

    def _map_webtext2019zh(self, raw: dict) -> Optional[ArticleData]:
        """webtext2019zh 字段映射"""
        title = raw.get("title", "").strip()
        content = raw.get("content", "").strip()
        if not title or not content or len(content) < 200:
            return None
        star = raw.get("star", 0)
        return ArticleData(
            source="corpus_qa",
            title=title,
            content=content,
            url=raw.get("url", ""),
            author=raw.get("author", ""),
            metrics=ArticleMetrics(likes=star if star else None),
            quality_score=min(1.0, star / 100) if star else 0.5,
            ttl_days=0,
        )

    def _save_batch(self, store, articles: list[ArticleData]) -> int:
        """批量保存到ArticleStore"""
        try:
            for article in articles:
                store.add(article)
            return len(articles)
        except Exception as e:
            logger.error(f"批量保存失败: {e}")
            return 0

    def _load_checkpoint(self, dataset_type: str) -> int:
        """加载断点"""
        cp_dir = PROJECT_ROOT / "data" / "corpus_checkpoint"
        cp_file = cp_dir / f"{dataset_type}.json"
        if not cp_file.exists():
            return 0
        try:
            with open(cp_file, "r", encoding="utf-8") as f:
                return json.load(f).get("line_no", 0)
        except Exception:
            return 0

    def _save_checkpoint(self, dataset_type: str, line_no: int) -> None:
        """保存断点"""
        cp_dir = PROJECT_ROOT / "data" / "corpus_checkpoint"
        cp_dir.mkdir(parents=True, exist_ok=True)
        cp_file = cp_dir / f"{dataset_type}.json"
        try:
            with open(cp_file, "w", encoding="utf-8") as f:
                json.dump({"line_no": line_no}, f)
        except Exception as e:
            logger.warning(f"断点保存失败: {e}")
