"""
SAM3 指代分割解析与实例选择。

入参: 用户指代表达、目标类别以及 SAM3 实例候选。
方法: 将大小/方位/参照物关系解析为结构化查询，再使用实例几何属性确定唯一目标。
出参: 单实例二值掩膜和可解释的选择元数据。
"""

from dataclasses import asdict, dataclass
from typing import Dict, Optional, Sequence, Tuple

import numpy as np
import torch


_TERM_TO_CLASS = {
    "建筑物": "building",
    "建筑": "building",
    "房屋": "building",
    "房子": "building",
    "屋顶": "building",
    "building": "building",
    "house": "building",
    "roof": "building",
    "道路": "road",
    "公路": "road",
    "街道": "road",
    "road": "road",
    "水体": "water",
    "河流": "water",
    "湖泊": "water",
    "water": "water",
    "river": "water",
    "lake": "water",
    "植被": "vegetation",
    "森林": "vegetation",
    "林地": "vegetation",
    "vegetation": "vegetation",
    "forest": "vegetation",
    "农田": "farmland",
    "耕地": "farmland",
    "farmland": "farmland",
    "裸地": "bareland",
    "裸土": "bareland",
    "bareland": "bareland",
}

_SELECTOR_PATTERNS = (
    ("top_left", ("左上角", "左上", "top left")),
    ("top_right", ("右上角", "右上", "top right")),
    ("bottom_left", ("左下角", "左下", "bottom left")),
    ("bottom_right", ("右下角", "右下", "bottom right")),
    ("largest", ("面积最大", "最大的", "最大", "largest")),
    ("smallest", ("面积最小", "最小的", "最小", "smallest")),
    ("leftmost", ("最左侧", "最左边", "leftmost")),
    ("rightmost", ("最右侧", "最右边", "rightmost")),
    ("topmost", ("最上方", "最北侧", "topmost", "northernmost")),
    ("bottommost", ("最下方", "最南侧", "bottommost", "southernmost")),
)

_RELATION_PATTERNS = (
    ("north_of", ("北侧", "北边", "上方", "north of", "above")),
    ("south_of", ("南侧", "南边", "下方", "south of", "below")),
    ("west_of", ("西侧", "西边", "左侧", "west of", "left of")),
    ("east_of", ("东侧", "东边", "右侧", "east of", "right of")),
    ("near", ("附近", "靠近", "邻近", "near", "nearest to", "close to")),
)


@dataclass(frozen=True)
class ReferringQuery:
    """
    入参: 原始表达、标准目标 prompt、可选参照物 prompt、空间关系和实例选择器。
    方法: 保存解析后的不可变指代语义，供模型 grounding 与几何选择共同使用。
    出参: 可序列化的结构化指代查询。
    """

    expression: str
    target_prompt: str
    anchor_prompt: Optional[str]
    relation: Optional[str]
    selector: Optional[str]


def _canonical_class(text: str) -> Optional[str]:
    """
    入参: 中文或英文类别文本。
    方法: 优先精确匹配，再按最长已知类别词搜索，避免“建筑物”被截成“建筑”。
    出参: 标准英文类别；未识别时返回 None。
    """
    normalized = text.strip().lower()
    exact = _TERM_TO_CLASS.get(normalized)
    if exact:
        return exact
    matches = [
        (len(term), canonical)
        for term, canonical in _TERM_TO_CLASS.items()
        if term in normalized
    ]
    return max(matches, default=(0, None))[1]


def _match_pattern(text: str, patterns: Sequence[Tuple[str, Sequence[str]]]) -> Optional[str]:
    """
    入参: 已标准化文本和有序的 (语义值, 触发短语) 表。
    方法: 按表顺序返回首个命中的语义值，使组合方位优先于单方向词。
    出参: 命中的语义值；无命中时返回 None。
    """
    return next(
        (name for name, phrases in patterns if any(phrase in text for phrase in phrases)),
        None,
    )


def _class_mentions(text: str) -> list:
    """
    入参: 原始指代表达。
    方法: 收集已知类别词的位置并按文本顺序去除同位置、同类别的重复命中。
    出参: [(字符位置, 标准英文类别), ...]。
    """
    normalized = text.lower()
    mentions = sorted(
        (normalized.find(term), -len(term), canonical)
        for term, canonical in _TERM_TO_CLASS.items()
        if term in normalized
    )
    ordered = []
    seen = set()
    for position, _, canonical in mentions:
        key = (position, canonical)
        if key not in seen:
            ordered.append((position, canonical))
            seen.add(key)
    return ordered


def parse_referring_expression(expression: str, target_class: str = "") -> ReferringQuery:
    """
    入参: expression 为完整指代表达；target_class 可显式指定待分割类别。
    方法: 提取目标类别、参照类别、相对方位以及最大/最小/绝对方位选择器。
    出参: ReferringQuery；无法识别类别时保留完整表达作为开放词汇 prompt。
    """
    normalized = expression.strip().lower()
    mentions = _class_mentions(normalized)
    explicit_target = _canonical_class(target_class)
    target_prompt = explicit_target or (mentions[-1][1] if mentions else normalized)
    anchor_prompt = next(
        (canonical for _, canonical in reversed(mentions) if canonical != target_prompt),
        None,
    )
    relation = _match_pattern(normalized, _RELATION_PATTERNS) if anchor_prompt else None
    selector = _match_pattern(normalized, _SELECTOR_PATTERNS)
    return ReferringQuery(
        expression=expression.strip(),
        target_prompt=target_prompt,
        anchor_prompt=anchor_prompt,
        relation=relation,
        selector=selector,
    )


def _candidate_indices_by_relation(
    target_centers: torch.Tensor,
    anchor_centers: Optional[torch.Tensor],
    relation: Optional[str],
) -> torch.Tensor:
    """
    入参: 目标/参照物中心点张量以及关系名称。
    方法: 对 north/south/east/west 计算至少满足一个参照物的候选集合；near 保留全部候选。
    出参: 一维候选索引；不存在有效关系过滤时返回全部索引。
    """
    all_indices = torch.arange(target_centers.shape[0], device=target_centers.device)
    if anchor_centers is None or anchor_centers.numel() == 0 or relation is None:
        return all_indices
    comparisons = {
        "north_of": target_centers[:, None, 1] < anchor_centers[None, :, 1],
        "south_of": target_centers[:, None, 1] > anchor_centers[None, :, 1],
        "west_of": target_centers[:, None, 0] < anchor_centers[None, :, 0],
        "east_of": target_centers[:, None, 0] > anchor_centers[None, :, 0],
        "near": torch.ones(
            (target_centers.shape[0], anchor_centers.shape[0]),
            device=target_centers.device,
            dtype=torch.bool,
        ),
    }
    valid = comparisons[relation].any(dim=1)
    filtered = all_indices[valid]
    return filtered if filtered.numel() > 0 else all_indices


def select_referring_instance(
    boxes: torch.Tensor,
    masks: torch.Tensor,
    scores: torch.Tensor,
    query: ReferringQuery,
    anchor_boxes: Optional[torch.Tensor] = None,
) -> int:
    """
    入参: N 个 xyxy 框、N 个实例掩膜、N 个分数、结构化查询及可选参照框。
    方法: 先按相对关系过滤，再按大小/绝对方位选择；near 使用中心距离，默认使用模型分数。
    出参: [0, N) 内的目标实例索引；N=0 时返回 -1。
    """
    if boxes.shape[0] == 0:
        return -1
    centers = (boxes[:, :2] + boxes[:, 2:]) / 2
    anchor_centers = None
    if anchor_boxes is not None and anchor_boxes.shape[0] > 0:
        anchor_centers = (anchor_boxes[:, :2] + anchor_boxes[:, 2:]) / 2
    candidates = _candidate_indices_by_relation(centers, anchor_centers, query.relation)

    if query.relation == "near" and anchor_centers is not None:
        distances = torch.cdist(centers[candidates], anchor_centers).amin(dim=1)
        return int(candidates[distances.argmin()].item())

    candidate_centers = centers[candidates]
    areas = masks[candidates].reshape(candidates.shape[0], -1).sum(dim=1)
    selector_values: Dict[str, torch.Tensor] = {
        "largest": -areas,
        "smallest": areas,
        "leftmost": candidate_centers[:, 0],
        "rightmost": -candidate_centers[:, 0],
        "topmost": candidate_centers[:, 1],
        "bottommost": -candidate_centers[:, 1],
        "top_left": candidate_centers[:, 0] + candidate_centers[:, 1],
        "top_right": -candidate_centers[:, 0] + candidate_centers[:, 1],
        "bottom_left": candidate_centers[:, 0] - candidate_centers[:, 1],
        "bottom_right": -candidate_centers[:, 0] - candidate_centers[:, 1],
    }
    ranking = selector_values.get(query.selector, -scores[candidates])
    return int(candidates[ranking.argmin()].item())


@torch.inference_mode()
def run_referring_inference(processor, image, expression: str, target_class: str = ""):
    """
    入参: 已加载 processor、PIL 图像、完整指代表达和可选目标类别。
    方法: 分别 grounding 目标与参照物，按结构化关系选出一个实例并生成类别 1 掩膜。
    出参: (H,W) int64 掩膜和包含解析结果、候选数、选择索引的元数据。
    """
    query = parse_referring_expression(expression, target_class)
    state = processor.set_image(image)
    processor.reset_all_prompts(state)
    target_state = processor.set_text_prompt(query.target_prompt, state)
    target_boxes = target_state["boxes"]
    target_masks = target_state["masks"]
    target_scores = target_state["scores"]

    anchor_boxes = None
    if query.anchor_prompt:
        processor.reset_all_prompts(state)
        anchor_state = processor.set_text_prompt(query.anchor_prompt, state)
        anchor_boxes = anchor_state["boxes"]

    selected_index = select_referring_instance(
        target_boxes,
        target_masks,
        target_scores,
        query,
        anchor_boxes,
    )
    height, width = image.height, image.width
    segmentation = np.zeros((height, width), dtype=np.int64)
    if selected_index >= 0:
        selected_mask = target_masks[selected_index].squeeze().cpu().numpy()
        segmentation[selected_mask] = 1
    metadata = {
        **asdict(query),
        "candidate_count": int(target_boxes.shape[0]),
        "anchor_count": int(anchor_boxes.shape[0]) if anchor_boxes is not None else 0,
        "selected_index": selected_index,
    }
    return segmentation, metadata
