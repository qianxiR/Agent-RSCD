"""
重复操作偏好识别 (Agent 层 / 记忆子系统)
- 入参: user_id / tool_name / tool_args (工具调用记录)
- 方法: 内存 LRU 计数器, 同一操作签名累计达到阈值时, 触发偏好写入
- 出参: 触发时返回偏好条目 [{key, value}], 未触发返回 None

设计目标:
  用户重复做同一件事 (如反复查看图层、反复查询某张表), 说明这是 TA 的高频工作流.
  自动识别这种模式并写入长期记忆 (user_memory), 让后续对话能主动适配用户行为.

抽象化设计:
  ★ 签名中的参数值 (图层名/表名) 仅用于内部重复检测, 不写入长期记忆.
  ★ 写入记忆的是"抽象行为描述" (如"频繁查看和切换图层"), 而非具体实体名.
  ★ _TOOL_BEHAVIOR_DESC 映射表将工具名转化为抽象行为类别.

权衡:
  - 纯内存 LRU, 不落库 → 重启清零 (可接受, 偏好会随使用重新积累)
  - 阈值默认 3 次 → 太低易误判, 太高感知不到; 暴露 settings 可调
  - 签名的关键参数 (图层名/表名/类别) 仅用于行为频次统计, 最终落库时被抽象化

签名规则 (signature):
  signature = tool_name + "|" + 标准化关键参数 (仅内部计数用)
  例: "toggle_layer_visibility|layer_name=建筑用地"
      "query_and_render_table|table_name=land_use"
  关键参数由 _extract_key_args 按工具名提取, 不同工具关注不同参数.
"""
import logging
import threading
from collections import OrderedDict
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)

# 阈值: 同一操作签名累计多少次后触发偏好写入
TRIGGER_THRESHOLD = 3

# LRU 容量上限 (每个 user_id 的签名数)
MAX_SIGNATURES_PER_USER = 200

# ★ 各工具的"关键参数"白名单 (只有这些参数参与签名, 其余忽略)
#   未列出的工具: 用所有 string 类型参数 (兜底)
_TOOL_KEY_ARGS: Dict[str, List[str]] = {
    # 图层类: 关注图层名 (workspace/action 等忽略)
    "toggle_layer_visibility": ["layer_name"],
    "hide_layer": ["layer_name"],
    "download_geoserver_layer": ["layer_name", "output_format"],
    "download_shapefile_layer": ["layer_name"],
    "download_raster_layer": ["layer_name"],
    # 数据库类: 关注表名
    "query_and_render_table": ["table_name"],
    "read_table_statistics": ["table_name"],
    "list_table_schema": ["table_name"],
    # 遥感类: 关注 classes (用户想识别的地物类别, 反映工作重点)
    "segment_image": ["classes"],
    "detect_change": ["classes"],
}

# ★ 工具到抽象行为描述的映射 (偏好写入时, 不再包含具体参数值)
#   注意: 签名中的参数值仅用于内部重复计数, 最终写入记忆的是这里的抽象描述
_TOOL_BEHAVIOR_DESC: Dict[str, str] = {
    # 图层操作类
    "toggle_layer_visibility": "查看和切换图层",
    "hide_layer": "隐藏图层",
    "download_geoserver_layer": "下载 GeoServer 图层",
    "download_shapefile_layer": "下载 Shapefile 图层",
    "download_raster_layer": "下载栅格图层",
    # 数据库操作类
    "query_and_render_table": "查询数据库表",
    "read_table_statistics": "分析表统计信息",
    "list_table_schema": "查看表结构",
    # 遥感分析类
    "segment_image": "影像分割分析",
    "detect_change": "变化检测分析",
}

# 计数器: {user_id: OrderedDict[signature, {"count": int, "tool": str, "args_snapshot": dict}]}
#   OrderedDict 实现 LRU (move_to_end 访问即更新)
_counters: Dict[str, OrderedDict] = {}
_lock = threading.Lock()


def _get_bucket(user_id: str) -> OrderedDict:
    """获取/创建某用户的计数桶 (线程安全, 带 LRU 淘汰)"""
    with _lock:
        bucket = _counters.get(user_id)
        if bucket is None:
            bucket = OrderedDict()
            _counters[user_id] = bucket
        return bucket


def _extract_key_args(tool_name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    """从 args 中提取关键参数 (按工具白名单), 忽略易变参数"""
    if not isinstance(args, dict):
        return {}
    key_list = _TOOL_KEY_ARGS.get(tool_name)
    if key_list is None:
        # 兜底: 取所有 str 类型参数 (排除明显易变的: file_path, image_path 等)
        skip = {"file_path", "image_path", "t1_path", "t2_path", "output_path"}
        return {k: v for k, v in args.items() if isinstance(v, str) and k not in skip}
    return {k: args.get(k) for k in key_list if k in args}


def _make_signature(tool_name: str, key_args: Dict[str, Any]) -> str:
    """构造操作签名: tool_name|k1=v1;k2=v2"""
    parts = [f"{k}={v}" for k, v in sorted(key_args.items()) if v is not None]
    return f"{tool_name}|{';'.join(parts)}"


def record(user_id: str, tool_name: str, tool_args: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    记录一次工具调用, 返回触发时的偏好条目 (dict) 或 None.

    调用时机: chat_service 里每个工具成功执行后 (同步调用, 纯内存 O(1), 不阻塞).

    触发逻辑:
      1. 构造签名, 计数 +1
      2. count 达到 TRIGGER_THRESHOLD → 构造偏好 → 该签名重置 (避免反复写)
      3. 返回偏好条目 {key, value, category, source}, 由上层落库
    """
    if not user_id or not tool_name:
        return None
    key_args = _extract_key_args(tool_name, tool_args or {})
    if not key_args:
        # 没有关键参数的操作 (如 list_geoserver_services) 不追踪, 无法形成偏好
        return None
    signature = _make_signature(tool_name, key_args)

    bucket = _get_bucket(user_id)
    with _lock:
        entry = bucket.get(signature)
        if entry is None:
            entry = {"count": 0, "tool": tool_name, "args": key_args}
            bucket[signature] = entry
            # LRU 淘汰: 超容量删最旧
            if len(bucket) > MAX_SIGNATURES_PER_USER:
                bucket.popitem(last=False)
        else:
            bucket.move_to_end(signature)  # 访问即更新 LRU
        entry["count"] += 1
        count = entry["count"]

    if count < TRIGGER_THRESHOLD:
        return None

    # ★ 达到阈值: 构造偏好, 重置计数 (移除该项, 下次重新从 0 计)
    pref = _build_preference(tool_name, key_args)
    with _lock:
        bucket.pop(signature, None)

    if pref:
        logger.info(
            f"[PatternTracker] 触发偏好写入: user={user_id} tool={tool_name} "
            f"count={count} pref={pref['key']}={pref['value']}"
        )
    return pref


def _build_preference(tool_name: str, key_args: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    把高频操作签名转成抽象的用户行为偏好条目.
    返回 {key, value, category, source} 或 None (无法生成有意义的偏好时)

    抽象化设计:
      - value 只包含抽象行为描述 (如"频繁查看和切换图层"), 不含具体参数值
      - key_args 中的具体参数值仅用于重复计数, 不写入最终记忆
      - 通过 _TOOL_BEHAVIOR_DESC 查找工具对应的行为描述
    """
    behavior = _TOOL_BEHAVIOR_DESC.get(tool_name)
    if behavior:
        return {
            "key": "操作偏好",
            "value": f"频繁{behavior}",
            "category": "preference",
            "source": "pattern",
        }
    # 兜底: 工具不在映射表中时, 用工具名生成抽象描述
    if key_args:
        return {
            "key": "操作偏好",
            "value": f"频繁使用 {tool_name} 工具",
            "category": "preference",
            "source": "pattern",
        }
    return None


def reset_user(user_id: str) -> None:
    """清空某用户的计数器 (调试用, 或记忆被清空时联动)"""
    with _lock:
        _counters.pop(user_id, None)


def get_stats() -> Dict[str, Any]:
    """返回计数器统计 (调试/日志用)"""
    with _lock:
        return {
            "users": len(_counters),
            "total_signatures": sum(len(b) for b in _counters.values()),
        }
