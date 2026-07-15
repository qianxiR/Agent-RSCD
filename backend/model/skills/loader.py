"""
Skills 加载器 (Model 层 / Skills)
- 入参: 无 (启动时自动扫描 skills 目录)
- 方法: 扫描 *.md 文档, 解析为可检索的 Skill 结构, 提供关键词检索
- 出参: Skill 字典 / 检索结果列表

设计要点:
  - 纯文件扫描 + 文本解析, 无外部依赖 (不引入向量库)
  - 每个 .md 通过首行标题/文件名/正文关键词建立索引
  - lookup_skill 工具调用 retrieve_skills(query) 拿到匹配文档, 返回给 agent 按需执行

Skill 文档约定 (Markdown 头部可含 YAML-like 元信息, 解析尽力而为):
  - 首个 "# 标题" → title
  - 文件名 (去扩展名) → name
  - "## 触发场景" 段落 → keywords (用于匹配)
  - 全文 → content (供 agent 阅读)

依赖方向: model.skills 仅依赖标准库 + config, 无跨层依赖。
"""
import os
import re
import logging
from typing import List, Dict, Any, Optional
from pathlib import Path

logger = logging.getLogger(__name__)

# skills 目录 (本文件所在目录)
_SKILLS_DIR = Path(__file__).resolve().parent

# 已加载的 skill 列表 (首次访问时懒加载, 内容稳定后缓存)
_skills_cache: Optional[List[Dict[str, Any]]] = None


def _parse_markdown(file_path: Path) -> Dict[str, Any]:
    """
    解析单个 Markdown skill 文档, 提取结构化字段。
    - title: 首个 "# " 标题 (去掉前缀和括号说明)
    - name: 文件名 (去 .md), 作为稳定标识
    - keywords: 从"触发场景"段落与标题/小标题提取的关键词
    - content: 全文 (供 agent 阅读执行)
    """
    try:
        text = file_path.read_text(encoding="utf-8")
    except Exception as e:
        logger.warning(f"读取 skill 文档失败 [{file_path.name}]: {e}")
        return None

    name = file_path.stem  # 文件名去扩展名

    # 提取首个一级标题作为 title
    title_match = re.search(r"^#\s+(.+)$", text, re.MULTILINE)
    # 去掉标题里的 ">" 引用块/括号说明, 取主标题
    raw_title = title_match.group(1).strip() if title_match else name
    # 形如 "Skill: 数据入库流水线 (Data Ingestion Pipeline)" → 取冒号后的中文主名
    title = raw_title
    if ":" in title or "：" in title:
        title = re.split(r"[:：]", title, 1)[1].strip()
    title = re.sub(r"[（(].*?[)）]", "", title).strip() or raw_title

    # 提取关键词: 标题 + 所有小标题 + "触发场景"段内的短语
    keywords = set()
    keywords.add(title)
    keywords.add(name)
    # 所有标题行 (## ### 等) 作为关键词候选
    for m in re.finditer(r"^#{1,6}\s+(.+)$", text, re.MULTILINE):
        heading = m.group(1).strip()
        # 去掉 markdown 强调符号
        heading = re.sub(r"[`*_>]", "", heading).strip()
        if heading:
            keywords.add(heading)

    # 触发场景段: 找 "## 触发场景" 到下一个 "##" 之间的引号内容/短句
    trigger_match = re.search(
        r"##\s*触发场景.*?\n(.*?)(?=\n##\s|\Z)",
        text,
        re.DOTALL,
    )
    if trigger_match:
        trigger_text = trigger_match.group(1)
        # 提取引号内的短语 (典型意图表达)。支持英文 " 和中文弯引号 “ ”
        # 用 \u201c \u201d 显式转义, 避免 raw string 里中文引号与 " 混淆
        for q in re.finditer(r'["\u201c]([^"\u201d]+)["\u201d]', trigger_text):
            keywords.add(q.group(1))
        # 提取列表项首部短语
        for li in re.finditer(r"^[-*]\s+(.+)$", trigger_text, re.MULTILINE):
            line = li.group(1).strip()
            # 去掉引号内容本身, 取前 20 字
            keywords.add(line[:20])

    return {
        "name": name,
        "title": title,
        "keywords": [k for k in keywords if k],
        "content": text,
        "file": file_path.name,
    }


def _load_all() -> List[Dict[str, Any]]:
    """扫描 skills 目录加载所有 .md 文档 (排除 README)。结果缓存。"""
    global _skills_cache
    if _skills_cache is not None:
        return _skills_cache

    skills = []
    if not _SKILLS_DIR.is_dir():
        logger.warning(f"skills 目录不存在: {_SKILLS_DIR}")
        _skills_cache = []
        return _skills_cache

    for md_file in sorted(_SKILLS_DIR.glob("*.md")):
        if md_file.name.upper() == "README.MD":
            continue
        parsed = _parse_markdown(md_file)
        if parsed:
            skills.append(parsed)
            logger.info(f"[Skills] 已加载: {parsed['name']} ({parsed['title']})")

    _skills_cache = skills
    logger.info(f"[Skills] 共加载 {len(skills)} 个 skill 文档")
    return _skills_cache


def reload() -> List[Dict[str, Any]]:
    """强制重新扫描 (开发期 skill 文档变更后调用, 生产无需)。"""
    global _skills_cache
    _skills_cache = None
    return _load_all()


def list_skills() -> List[Dict[str, Any]]:
    """返回所有已加载 skill 的元信息 (不含全文 content, 供 agent 选择)。"""
    return [
        {"name": s["name"], "title": s["title"], "keywords": s["keywords"]}
        for s in _load_all()
    ]


def retrieve_skills(query: str, top_k: int = 3) -> List[Dict[str, Any]]:
    """
    关键词检索: 用 query 匹配每个 skill 的 title/keywords, 按命中数排序。
    - 入参: query (用户意图, 自然语言)
    - 出参: 匹配的 skill 列表 [{name, title, keywords, content, score}], 含全文供执行
    - 无向量库, 用关键词重叠计数 (Jaccard 风格) 做粗排, 足够按需检索用
    """
    skills = _load_all()
    if not skills:
        return []

    # 把 query 切成有意义的词 (中文按字/2-4字滑窗, 英文按词)
    query_lower = query.lower()
    query_tokens = set()
    # 英文/数字词
    for w in re.findall(r"[a-z0-9]+", query_lower):
        if len(w) >= 2:
            query_tokens.add(w)
    # 中文 2-4 字滑窗 (覆盖"数据入库""流水线""上传影像"等复合词)
    cn_chars = re.findall(r"[\u4e00-\u9fa5]", query)
    for size in (2, 3, 4):
        for i in range(len(cn_chars) - size + 1):
            query_tokens.add("".join(cn_chars[i : i + size]))

    if not query_tokens:
        return []

    scored = []
    for s in skills:
        # 把 title + keywords 拼成可匹配文本
        haystack_lower = (s["title"] + " " + " ".join(s["keywords"])).lower()
        score = 0
        for tok in query_tokens:
            if tok in haystack_lower:
                score += 1
        # 最低分阈值: 过滤单一操作的低分噪音命中。
        # 长任务意图通常命中多个关键词 (如"数据/入库/影像/流水线" 等, score≥3);
        # 单一操作 (如"显示图层") 命中词少 (score 1-2), 不应误触发长任务检索。
        if score >= 3:
            scored.append({**s, "score": score})

    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[:top_k]


def get_skill_by_name(name: str) -> Optional[Dict[str, Any]]:
    """按 name (文件名) 精确获取单个 skill 全文。"""
    for s in _load_all():
        if s["name"] == name:
            return s
    return None
