"""SAM3 指代表达解析和实例几何选择测试。"""

from types import SimpleNamespace

import torch

from backend.model.SamSeg.referring import (
    parse_referring_expression,
    run_referring_inference,
    select_referring_instance,
)


def _candidate_masks(areas):
    """
    入参: 每个候选需要占用的像素数量。
    方法: 在固定 10x10 掩膜中按行展开前 N 个像素，构造可比较的实例面积。
    出参: (N, 1, 10, 10) bool 张量。
    """
    masks = torch.zeros((len(areas), 1, 10, 10), dtype=torch.bool)
    for index, area in enumerate(areas):
        masks[index].view(-1)[:area] = True
    return masks


def test_parse_relative_largest_building():
    """
    入参: 含目标、参照物、相对方向和大小限定的中文表达。
    方法: 调用结构化解析并检查各语义槽位。
    出参: 解析不完整时触发断言失败。
    """
    query = parse_referring_expression("道路北侧最大的建筑", "建筑")

    assert query.target_prompt == "building"
    assert query.anchor_prompt == "road"
    assert query.relation == "north_of"
    assert query.selector == "largest"


def test_select_largest_candidate_north_of_anchor():
    """
    入参: 两个北侧建筑、一个南侧建筑和一个道路参照框。
    方法: 先过滤道路北侧候选，再按掩膜面积选择较大实例。
    出参: 应选择索引 1，而不是南侧的全局最大实例。
    """
    boxes = torch.tensor(
        [[10, 10, 20, 20], [30, 10, 50, 30], [20, 70, 60, 95]],
        dtype=torch.float32,
    )
    anchor_boxes = torch.tensor([[0, 45, 100, 55]], dtype=torch.float32)
    masks = _candidate_masks([10, 30, 60])
    scores = torch.tensor([0.8, 0.7, 0.95])
    query = parse_referring_expression("道路北侧最大的建筑", "建筑")

    selected = select_referring_instance(boxes, masks, scores, query, anchor_boxes)

    assert selected == 1


def test_select_nearest_candidate_to_water():
    """
    入参: 两个建筑候选和一个水体参照框。
    方法: 对目标/参照中心点计算距离并选择最近实例。
    出参: 应选择靠近水体的索引 1。
    """
    boxes = torch.tensor([[0, 0, 10, 10], [70, 70, 80, 80]], dtype=torch.float32)
    anchor_boxes = torch.tensor([[80, 80, 90, 90]], dtype=torch.float32)
    masks = _candidate_masks([20, 20])
    scores = torch.tensor([0.95, 0.6])
    query = parse_referring_expression("靠近水体的建筑", "建筑")

    selected = select_referring_instance(boxes, masks, scores, query, anchor_boxes)

    assert selected == 1


def test_select_top_left_candidate():
    """
    入参: 分布在图像左上和右下的两个建筑候选。
    方法: 使用中心点 x+y 的单调方位代价选择左上实例。
    出参: 应选择索引 0。
    """
    boxes = torch.tensor([[5, 5, 15, 15], [70, 70, 90, 90]], dtype=torch.float32)
    masks = _candidate_masks([10, 40])
    scores = torch.tensor([0.5, 0.99])
    query = parse_referring_expression("左上角的建筑", "建筑")

    selected = select_referring_instance(boxes, masks, scores, query)

    assert selected == 0


class _SharedStateProcessor:
    """
    入参: 无外部模型依赖。
    方法: 模拟真实 processor 在连续文本 prompt 间复用并覆盖同一个 state 字典。
    出参: 可用于验证目标候选是否在 anchor 推理前被正确快照的测试替身。
    """

    def set_image(self, image):
        """
        入参: 带 height/width 的图像替身。
        方法: 创建与真实 processor 一致的共享可变状态。
        出参: 空状态字典。
        """
        return {}

    def reset_all_prompts(self, state):
        """
        入参: 共享状态字典。
        方法: 保留字典对象，仅清除上一 prompt 的结果字段。
        出参: 无。
        """
        state.clear()

    def set_text_prompt(self, prompt, state):
        """
        入参: building/road prompt 和共享状态。
        方法: 原位写入两组不同候选，复现真实 processor 的覆盖语义。
        出参: 被原位修改的同一状态字典。
        """
        if prompt == "building":
            state.update(
                boxes=torch.tensor(
                    [[0, 0, 2, 2], [3, 0, 8, 4]], dtype=torch.float32
                ),
                masks=_candidate_masks([4, 20]),
                scores=torch.tensor([0.8, 0.7]),
            )
        else:
            state.update(
                boxes=torch.tensor([[0, 6, 10, 8]], dtype=torch.float32),
                masks=_candidate_masks([20]),
                scores=torch.tensor([0.9]),
            )
        return state


def test_referring_inference_preserves_target_state_before_anchor_prompt():
    """
    入参: 会覆盖共享 state 的 processor 替身和带参照物的指代表达。
    方法: 运行完整指代推理，检查 anchor prompt 后仍使用建筑候选进行选择。
    出参: 应保留 2 个目标候选并选择面积为 20 的建筑实例。
    """
    image = SimpleNamespace(height=10, width=10)

    segmentation, metadata = run_referring_inference(
        _SharedStateProcessor(), image, "道路北侧最大的建筑", "建筑"
    )

    assert metadata["candidate_count"] == 2
    assert metadata["anchor_count"] == 1
    assert metadata["selected_index"] == 1
    assert int(segmentation.sum()) == 20
