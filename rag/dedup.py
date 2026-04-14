"""文章语义去重模块 — 零 token 消耗

使用 SimHash（正文）+ 标题分词 Jaccard 相似度进行本地去重，
不调用任何 embedding API，不消耗 token。

策略:
  1. 标题分词 Jaccard > title_threshold → 判定重复
  2. 正文 SimHash Hamming 距离 ≤ content_hamming_threshold → 判定重复
  3. 两者满足其一即为重复
"""
from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional

from loguru import logger


# ── SimHash 实现 ──────────────────────────────────────

class SimHash:
    """局部敏感哈希（LSH），用于快速判断文本相似度

    原理：将文本分词后哈希，累加各 bit 位的权重，
    最终生成固定长度的指纹。相似文本的指纹 Hamming 距离很小。
    """

    def __init__(self, tokens: list[str], hashbits: int = 64):
        self.hashbits = hashbits
        self.hash = self._build(tokens)

    def _build(self, tokens: list[str]) -> int:
        """从分词列表构建 SimHash 指纹"""
        v = [0] * self.hashbits
        for token in tokens:
            token_hash = int(hashlib.md5(token.encode("utf-8")).hexdigest(), 16)
            for i in range(self.hashbits):
                bitmask = 1 << i
                if token_hash & bitmask:
                    v[i] += 1
                else:
                    v[i] -= 1
        fingerprint = 0
        for i in range(self.hashbits):
            if v[i] > 0:
                fingerprint |= (1 << i)
        return fingerprint

    def hamming_distance(self, other: SimHash) -> int:
        """计算两个 SimHash 的 Hamming 距离"""
        x = self.hash ^ other.hash
        distance = 0
        while x:
            distance += 1
            x &= x - 1
        return distance

    def similarity(self, other: SimHash) -> float:
        """计算相似度（0~1），1 = 完全相同"""
        dist = self.hamming_distance(other)
        return 1.0 - dist / self.hashbits


# ── 中文分词（无需 jieba，基于规则的轻量分词） ──────────

def _tokenize_chinese(text: str) -> list[str]:
    """轻量中文分词：按标点/空白分割 + 双字滑动窗口

    不依赖 jieba，零外部依赖。对于去重场景足够用。
    如果已安装 jieba，会自动优先使用。
    """
    try:
        import jieba
        return list(jieba.cut(text))
    except ImportError:
        pass

    # 降级方案：双字滑动窗口 + 标点分割
    # 先按标点和空白分句
    segments = re.split(r'[，。！？、；：""''（）\s,.!?;:\'\"()\[\]{}<>]+', text)
    tokens = []
    for seg in segments:
        seg = seg.strip()
        if not seg:
            continue
        # 双字窗口
        for i in range(len(seg) - 1):
            tokens.append(seg[i:i + 2])
        # 单字也保留（短词场景）
        if len(seg) == 1:
            tokens.append(seg)
    return tokens


def _tokenize_for_jaccard(text: str) -> set[str]:
    """为 Jaccard 相似度分词，返回词集合"""
    return set(_tokenize_chinese(text))


# ── Jaccard 相似度 ────────────────────────────────────

def jaccard_similarity(set_a: set[str], set_b: set[str]) -> float:
    """计算两个集合的 Jaccard 相似度"""
    if not set_a and not set_b:
        return 1.0
    if not set_a or not set_b:
        return 0.0
    intersection = set_a & set_b
    union = set_a | set_b
    return len(intersection) / len(union)


# ── 去重结果 ──────────────────────────────────────────

@dataclass
class DuplicationInfo:
    """单篇文章的去重检测结果"""
    article_url: str
    article_title: str
    is_duplicate: bool
    duplicate_of: str = ""           # 重复的那篇文章 URL
    duplicate_title: str = ""        # 重复的那篇文章标题
    title_similarity: float = 0.0    # 标题 Jaccard 相似度
    content_similarity: float = 0.0  # 正文 SimHash 相似度
    match_type: str = ""             # "title" / "content" / "exact_url" / ""


@dataclass
class DedupResult:
    """去重检测结果汇总"""
    total: int = 0
    unique: int = 0
    duplicates: int = 0
    details: list[DuplicationInfo] = field(default_factory=list)

    @property
    def duplicate_rate(self) -> float:
        return self.duplicates / self.total if self.total > 0 else 0.0


# ── 去重引擎 ──────────────────────────────────────────

class ArticleDeduplicator:
    """文章语义去重引擎

    三级去重策略（优先级从高到低）:
      1. URL 精确匹配 → 直接判重
      2. 标题分词 Jaccard 相似度 > threshold → 判重
      3. 正文 SimHash Hamming 距离 ≤ threshold → 判重
    """

    def __init__(
        self,
        title_threshold: float = 0.6,
        content_hamming_threshold: int = 3,
        min_content_length: int = 50,
    ):
        """
        Args:
            title_threshold: 标题 Jaccard 相似度阈值，超过则判重（0~1）
            content_hamming_threshold: 正文 SimHash Hamming 距离阈值，
                                       小于等于此值判重（0~64）
            min_content_length: 正文最短长度，短于此不参与内容去重
        """
        self.title_threshold = title_threshold
        self.content_hamming_threshold = content_hamming_threshold
        self.min_content_length = min_content_length

        # 缓存：避免重复计算 SimHash
        self._simhash_cache: dict[str, SimHash] = {}
        # 索引：用于快速查找
        self._url_index: dict[str, dict] = {}         # url -> article dict
        self._title_token_index: dict[str, set] = {}   # url -> title token set
        self._simhash_index: dict[str, SimHash] = {}   # url -> SimHash

    def build_index(self, articles: list[dict]):
        """构建去重索引

        Args:
            articles: 文章列表，每篇需包含 url, title, content 字段
        """
        self._url_index.clear()
        self._title_token_index.clear()
        self._simhash_index.clear()
        self._simhash_cache.clear()

        for art in articles:
            url = art.get("url", "")
            title = art.get("title", "")
            content = art.get("content", "")

            if not url:
                continue

            self._url_index[url] = art
            self._title_token_index[url] = _tokenize_for_jaccard(title)

            if content and len(content) >= self.min_content_length:
                tokens = _tokenize_chinese(content)
                sh = SimHash(tokens)
                self._simhash_index[url] = sh
                self._simhash_cache[url] = sh

        logger.info(
            f"[Dedup] 索引构建完成: {len(self._url_index)} 篇文章, "
            f"{len(self._simhash_index)} 篇有 SimHash"
        )

    def check_article(self, article: dict) -> DuplicationInfo:
        """检查单篇文章是否与索引中的文章重复

        Args:
            article: 需包含 url, title, content 字段

        Returns:
            DuplicationInfo 去重检测结果
        """
        url = article.get("url", "")
        title = article.get("title", "")
        content = article.get("content", "")

        info = DuplicationInfo(
            article_url=url,
            article_title=title,
            is_duplicate=False,
        )

        # 1. URL 精确匹配
        if url and url in self._url_index:
            existing = self._url_index[url]
            info.is_duplicate = True
            info.duplicate_of = url
            info.duplicate_title = existing.get("title", "")
            info.match_type = "exact_url"
            return info

        # 2. 标题 Jaccard 相似度
        new_title_tokens = _tokenize_for_jaccard(title)
        best_title_sim = 0.0
        best_title_url = ""
        for existing_url, existing_tokens in self._title_token_index.items():
            if existing_url == url:
                continue
            sim = jaccard_similarity(new_title_tokens, existing_tokens)
            if sim > best_title_sim:
                best_title_sim = sim
                best_title_url = existing_url

        if best_title_sim > self.title_threshold and best_title_url:
            existing = self._url_index.get(best_title_url, {})
            info.is_duplicate = True
            info.duplicate_of = best_title_url
            info.duplicate_title = existing.get("title", "")
            info.title_similarity = best_title_sim
            info.match_type = "title"
            return info

        # 3. 正文 SimHash Hamming 距离
        if content and len(content) >= self.min_content_length:
            tokens = _tokenize_chinese(content)
            new_sh = SimHash(tokens)

            best_content_sim = 0.0
            best_content_url = ""
            min_hamming = 65  # 大于最大可能值 64

            for existing_url, existing_sh in self._simhash_index.items():
                if existing_url == url:
                    continue
                hamming = new_sh.hamming_distance(existing_sh)
                if hamming < min_hamming:
                    min_hamming = hamming
                    best_content_url = existing_url
                    best_content_sim = new_sh.similarity(existing_sh)

            if min_hamming <= self.content_hamming_threshold and best_content_url:
                existing = self._url_index.get(best_content_url, {})
                info.is_duplicate = True
                info.duplicate_of = best_content_url
                info.duplicate_title = existing.get("title", "")
                info.content_similarity = best_content_sim
                info.match_type = "content"
                return info

        return info

    def check_articles(self, articles: list[dict]) -> DedupResult:
        """批量检查文章去重

        Args:
            articles: 文章列表

        Returns:
            DedupResult 汇总结果
        """
        result = DedupResult(total=len(articles))
        duplicates = []

        for art in articles:
            info = self.check_article(art)
            result.details.append(info)
            if info.is_duplicate:
                duplicates.append(info)
                logger.debug(
                    f"[Dedup] 重复文章: '{info.article_title}' ≈ "
                    f"'{info.duplicate_title}' "
                    f"(类型={info.match_type}, "
                    f"标题相似度={info.title_similarity:.2f}, "
                    f"内容相似度={info.content_similarity:.2f})"
                )

        result.duplicates = len(duplicates)
        result.unique = result.total - result.duplicates

        logger.info(
            f"[Dedup] 检查完成: {result.total} 篇中 {result.duplicates} 篇重复 "
            f"(重复率 {result.duplicate_rate:.1%})"
        )
        return result

    def check_existing_articles(self, articles: list[dict]) -> DedupResult:
        """检查已有文章之间的内部去重（两两对比）

        与 check_articles 不同，此方法检查已有文章之间是否互相重复，
        用于清理知识库中已存在的重复。

        Args:
            articles: 已有文章列表

        Returns:
            DedupResult 汇总结果，duplicates 中每对只记后出现的那篇
        """
        result = DedupResult(total=len(articles))

        # 构建临时索引用于两两对比
        url_list = []
        title_tokens_list = []
        simhash_list = []

        for art in articles:
            url = art.get("url", "")
            title = art.get("title", "")
            content = art.get("content", "")
            url_list.append(url)
            title_tokens_list.append(_tokenize_for_jaccard(title))

            if content and len(content) >= self.min_content_length:
                tokens = _tokenize_chinese(content)
                simhash_list.append(SimHash(tokens))
            else:
                simhash_list.append(None)

        # 标记已判为重复的（后出现的记为重复）
        duplicate_flags = [False] * len(articles)
        duplicate_infos: list[DuplicationInfo] = []

        for i in range(len(articles)):
            if duplicate_flags[i]:
                continue

            for j in range(i + 1, len(articles)):
                if duplicate_flags[j]:
                    continue

                # URL 精确匹配
                if url_list[i] and url_list[i] == url_list[j]:
                    duplicate_flags[j] = True
                    duplicate_infos.append(DuplicationInfo(
                        article_url=url_list[j],
                        article_title=articles[j].get("title", ""),
                        is_duplicate=True,
                        duplicate_of=url_list[i],
                        duplicate_title=articles[i].get("title", ""),
                        match_type="exact_url",
                    ))
                    continue

                # 标题 Jaccard
                title_sim = jaccard_similarity(
                    title_tokens_list[i], title_tokens_list[j]
                )
                if title_sim > self.title_threshold:
                    duplicate_flags[j] = True
                    duplicate_infos.append(DuplicationInfo(
                        article_url=url_list[j],
                        article_title=articles[j].get("title", ""),
                        is_duplicate=True,
                        duplicate_of=url_list[i],
                        duplicate_title=articles[i].get("title", ""),
                        title_similarity=title_sim,
                        match_type="title",
                    ))
                    continue

                # 正文 SimHash
                if simhash_list[i] and simhash_list[j]:
                    hamming = simhash_list[i].hamming_distance(simhash_list[j])
                    if hamming <= self.content_hamming_threshold:
                        content_sim = simhash_list[i].similarity(simhash_list[j])
                        duplicate_flags[j] = True
                        duplicate_infos.append(DuplicationInfo(
                            article_url=url_list[j],
                            article_title=articles[j].get("title", ""),
                            is_duplicate=True,
                            duplicate_of=url_list[i],
                            duplicate_title=articles[i].get("title", ""),
                            content_similarity=content_sim,
                            match_type="content",
                        ))
                        continue

        result.duplicates = len(duplicate_infos)
        result.unique = result.total - result.duplicates
        result.details = duplicate_infos

        logger.info(
            f"[Dedup] 内部去重检查: {result.total} 篇中 {result.duplicates} 篇重复 "
            f"(重复率 {result.duplicate_rate:.1%})"
        )
        return result

    def filter_duplicates(self, articles: list[dict]) -> list[dict]:
        """过滤掉重复文章，返回去重后的列表

        Args:
            articles: 待过滤的文章列表

        Returns:
            去重后的文章列表
        """
        result = self.check_articles(articles)
        unique_articles = []
        for art, info in zip(articles, result.details):
            if not info.is_duplicate:
                unique_articles.append(art)
            else:
                logger.info(
                    f"[Dedup] 过滤重复: '{info.article_title}' "
                    f"(与 '{info.duplicate_title}' 重复, "
                    f"类型={info.match_type})"
                )
        logger.info(
            f"[Dedup] 过滤完成: {len(articles)} → {len(unique_articles)} 篇 "
            f"(移除 {len(articles) - len(unique_articles)} 篇重复)"
        )
        return unique_articles
