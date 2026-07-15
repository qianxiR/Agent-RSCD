"""
配置模块
- 入参: 无
- 方法: 集中管理所有服务配置, 敏感信息从环境变量读取
- 出参: settings 单例对象

支持真实 PostgreSQL 和 GeoServer 连接,
若连接不可用则自动降级为 mock 模式。
"""
# ★ 必须在任何 numpy/torch/MKL 间接 import 之前设置 (config 是公共早期依赖,
#   多数模块 `from backend.config import settings` 早于 numpy import)。
#   解 sam3 环境 numpy+torch 各带一份 OpenMP 的 "OMP: Error #15" 崩溃
#   (分割完成后 task 异常退出, 结果无法渲染到工作区)。
#   main.py 顶部也设了一份; 此处为 config 被独立导入时的兜底 (setdefault 幂等)。
import os as _os
_os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

import os
import logging
from pathlib import Path
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


def _env(key: str, default: str = "") -> str:
    """读取环境变量, 优先读取 agent-flow-study 根目录的 .env"""
    return os.environ.get(key, default)


def _env_list(key: str, default: list) -> list:
    """
    读取逗号分隔的环境变量为 list。
    - 入参: key (环境变量名), default (取不到时的默认列表)
    - 出参: 去空白后的非空字符串列表
    - 用途: SANDBOX_RW_MOUNT_SOURCES = "samseg/generate,samseg/vector"
            → ["samseg/generate", "samseg/vector"]
    """
    raw = os.environ.get(key, "")
    if not raw or not raw.strip():
        return list(default)
    return [s.strip() for s in raw.split(",") if s.strip()]


@dataclass
class Settings:
    # ==================== 服务端口 ====================
    agent_service_port: int = 8020

    # ==================== DashScope / Qwen LLM 配置 ====================
    dashscope_api_key: str = field(default_factory=lambda: _env("DASHSCOPE_API_KEY", ""))
    dashscope_base_url: str = field(default_factory=lambda: _env(
        "DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"
    ))
    # ★ 2026-06 主对话改用 qwen3.7-plus (额度全新 1,000,000, 到期 2026/09/01,
    #   比 qwen3.6-plus-2026-04-02 余量更足、到期更晚). 走 DashScope 兼容 OpenAI 协议.
    dashscope_model: str = "qwen3.7-plus"
    # ★ 视觉理解工具 (understand_image) 专用模型: qwen3.7-plus 本身是多模态视觉模型,
    #   直接复用主对话模型即可; 独立字段方便将来切换专用 vl 模型 (如 qwen-vl-plus).
    vision_model: str = "qwen3.7-plus"

    # ==================== 百炼可用模型清单 (免费额度, 2026-06 整理) ====================
    # 备选模型字典: {模型名: (适用场景, 免费额度上限, 额度到期日)}
    # 仅用于查阅/切换参考, 不参与运行时逻辑. 切换模型时改 dashscope_model /
    # summary_model / long_term_memory_model 对应字段即可.
    # 注: 各模型免费额度独立计费, 互不占用; "免费额度用完即停" 未开启计费保护,
    #     超额会按量付费, 切换前留意各模型剩余额度.
    available_models: dict = field(default_factory=lambda: {
        # ---- 主对话/Agent 推理 (需强能力 + 函数调用) ----
        "qwen3.7-max-preview":      ("旗舰预览版, 能力最强",        "1,000,000", "未公布"),
        "qwen3.7-max-2026-06-08":   ("旗舰 max, 最新日期锚定",      "1,000,000", "2026/09/08"),
        "qwen3.7-max-2026-05-17":   ("旗舰 max, 上一版本",          "1,000,000", "2026/08/24"),
        "qwen3.7-plus":             ("当前主对话模型, plus 档",     "1,000,000", "2026/09/01"),
        "qwen3.6-plus-2026-04-02":  ("plus 档, 上一版本",           "937,246",   "2026/07/02"),
        "qwen3.6-35b-a3b":          ("35B MoE (激活 3B), 性价比高", "1,000,000", "2026/07/17"),
        "qwen3.6-flash-2026-04-16": ("flash 档, 轻量快速",         "1,000,000", "2026/07/17"),
        "glm-5.1":                  ("智谱 GLM-5.1, 中文表现好",    "1,000,000", "2026/07/14"),
        # ---- 辅助任务 (摘要/长期记忆/分类等轻量任务, 用便宜档省主对话额度) ----
        "deepseek-v4-flash":        ("当前摘要/长期记忆模型, 极快", "1,000,000", "2026/07/24"),
        # ---- 专用模型 (按需调用, 不建议做通用对话) ----
        "qwen3.5-ocr":              ("OCR 专用, 识别图片文字",      "1,000,000", "2026/09/14"),
    })


    # ==================== LLM 参数 ====================
    default_temperature: float = 0.7
    max_tokens: int = 4096

    # ==================== 上下文管理 ====================
    # trim_messages 上限 (token), 给工具结果与回复预留余量
    # ★ 2026-06 调整: system prompt 加入动态工具目录+CoT思维链后膨胀到约 3700 token,
    #   原 6000 上限留给历史/工具结果的余量太窄 (尤其 query_and_render_table 等工具
    #   返回大表格时), 会触发 trim 误删当前 HumanMessage 的边界 bug.
    #   qwen3.7-plus 上下文窗口 128K, 16000 是兼顾成本和稳定性的保守值.
    context_max_tokens: int = 16000

    # ==================== 上下文缓存 (Context Cache) ====================
    # 显式缓存开关; 开启时 system prompt 拆分为带 cache_control 标记的内容块,
    # 跨轮稳定的固定部分 (角色+工具目录+快捷映射+CoT+回复规则) 命中后按 10% 计费.
    # 关闭时退回纯字符串 system prompt (DashScope 隐式缓存仍自动生效, 命中按 20%).
    enable_context_cache: bool = field(
        default_factory=lambda: _env("ENABLE_CONTEXT_CACHE", "true").lower() == "true"
    )
    # 缓存命中率日志开关; 开启时每轮 LLM 调用后打印 cached/total tokens 命中率
    log_cache_hit_rate: bool = True
    # 从 agent_db 加载最近 N 条消息 (LIMIT), 50 轮通常足够覆盖常用对话场景
    history_load_limit: int = 50

    # ==================== 短期记忆 - 摘要 (Summary Memory) ====================
    # 历史累计 token 超过此阈值时, 触发对旧消息的 LLM 摘要
    # 设为 0 可禁用摘要; 默认 8000 配合 context_max_tokens=16000, 保留一半近期消息不摘要
    summarize_threshold: int = 8000
    # 摘要调用用的模型 (用免费/便宜模型降本, deepseek-v4-flash 走百炼免费额度)
    summary_model: str = "deepseek-v4-flash"
    # 单次摘要的目标长度上限 (token), 控制摘要体积
    summary_max_tokens: int = 300

    # ==================== v2.5 多级上下文压缩 ====================
    # Level 1 (drop 工具消息, 可逆): 触发条件同 summarize_threshold
    #   把旧工具结果标记 [outdated], 保留结构, 降低 token 但不丢语义
    # Level 2 (LLM 摘要, 不可逆): 当 Level 1 后 token 仍超阈值 × 此倍数时升级
    summary_level2_multiplier: float = field(
        default_factory=lambda: float(_env("SUMMARY_LEVEL2_MULTIPLIER", "1.5"))
    )
    # Level 1 折叠时, 保留最近的 N 条工具消息不折叠 (近期上下文完整)
    tool_message_keep_tail: int = field(
        default_factory=lambda: int(_env("TOOL_MESSAGE_KEEP_TAIL", "3"))
    )

    # ==================== 长期记忆 - 用户画像 (Long-term Memory) ====================
    # ★ 记忆策略: 用户偏好/事实提取 与 错误案例学习 (lesson/workflow, 由自纠捕捉器产生) 双路并行.
    #   enable_preference_extraction 默认 true: 对话收尾时与自纠捕捉器在同一触发点并行启动
    #   (_async_memory_finale 提取偏好/事实, _async_self_correction_scan 沉淀教训).
    #   历史背景: 此开关曾默认关闭 (实测偏好/事实 ~86% 是低价值空话, 会污染 system prompt),
    #   后按需重新开启. 设 ENABLE_PREFERENCE_EXTRACTION=false 可单独关掉偏好提取, 只留错误案例学习.
    enable_preference_extraction: bool = field(
        default_factory=lambda: _env("ENABLE_PREFERENCE_EXTRACTION", "true").lower() == "true"
    )
    # 收尾时自动提取用户偏好/关键事实的模型 (同样用免费/便宜模型)
    long_term_memory_model: str = "deepseek-v4-flash"
    # 单轮最多提取的记忆条数 (避免 LLM 一次吐太多噪声)
    long_term_memory_max_items: int = 5

    # ==================== WebSocket ====================
    ws_heartbeat_interval: int = 30
    ws_frontend_result_timeout: int = 120

    # ==================== CORS ====================
    cors_origins: list = field(default_factory=lambda: ["*"])

    # ==================== PostgreSQL 数据库配置 ====================
    postgres_host: str = field(default_factory=lambda: _env("POSTGRES_HOST", "localhost"))
    postgres_port: str = field(default_factory=lambda: _env("POSTGRES_PORT", "5432"))
    postgres_db: str = field(default_factory=lambda: _env("POSTGRES_DB", "cd"))
    postgres_user: str = field(default_factory=lambda: _env("POSTGRES_USER", "postgres"))
    postgres_password: str = field(default_factory=lambda: _env("POSTGRES_PASSWORD", "001117"))
    postgres_schema: str = field(default_factory=lambda: _env("POSTGRES_SCHEMA", "public"))
    # Agent 记忆库 (可选)
    agent_db_host: str = field(default_factory=lambda: _env("AGENT_DB_HOST", "localhost"))
    agent_db_port: str = field(default_factory=lambda: _env("AGENT_DB_PORT", "5432"))
    agent_db_name: str = field(default_factory=lambda: _env("AGENT_DB_NAME", "Agent_study"))
    agent_db_user: str = field(default_factory=lambda: _env("AGENT_DB_USER", "postgres"))
    agent_db_password: str = field(default_factory=lambda: _env("AGENT_DB_PASSWORD", "001117"))

    # ==================== GeoServer 配置 ====================
    # ★ 用 127.0.0.1 而非 localhost: 后端 requests/httpx 解析 localhost 时,
    #   部分系统优先返回 IPv6(::1), 而 GeoServer 默认只监听 IPv4(127.0.0.1:8080),
    #   会因连不上 ::1 导致 geoserver_available() 判失败 → WMS 代理返回 502。
    #   显式 IPv4 地址绕过 DNS 解析优先级问题。
    geoserver_url: str = field(default_factory=lambda: _env("GEOSERVER_URL", "http://127.0.0.1:8080/geoserver"))
    geoserver_username: str = field(default_factory=lambda: _env("GEOSERVER_USERNAME", "admin"))
    geoserver_password: str = field(default_factory=lambda: _env("GEOSERVER_PASSWORD", "geoserver"))
    # 默认工作空间留空: 不绑定具体业务工作空间, 由工具层按裸名自动搜索所有工作空间定位
    geoserver_workspace: str = field(default_factory=lambda: _env("GEOSERVER_WORKSPACE", ""))

    # ==================== 统一文件存储 (v2.4 按来源维度组织) ====================
    # ★ 所有文件统一收归 agent-files/, 按数据来源作为一级目录:
    #   agent-files/
    #   ├── samseg/send/{conv_id}/                       → 遥感: 用户上传原图 (单层, 不带 proj)
    #   ├── samseg/generate/{proj_id}/{conv_id}/          → 遥感: 分割/变化检测结果
    #   ├── samseg/vector/{proj_id}/{conv_id}/            → 遥感: 矢量 GeoJSON
    #   ├── geoserver/generate/{proj_id}/{conv_id}/       → GeoServer: 图层下载
    #   ├── report/{proj_id}/{conv_id}/                   → 报表/导出产物
    #   ├── analysis/{proj_id}/{conv_id}/                 → 掩膜分析产物
    #   ├── preprocess/{proj_id}/{conv_id}/               → 预处理产物
    #   └── sandbox/workspace/{proj_id}/{conv_id}/        → 沙盒: 代码执行工作区 (会话级隔离)
    # ★ v2.4: 所有"双层 UUID"目录统一为 {proj_id}/{conv_id} (项目在外, 会话在内)。
    #         兜底常量统一: 无项目 → "_default"; 无会话 → "_anonymous"。
    file_storage_root: str = field(default_factory=lambda: _env(
        "FILE_STORAGE_ROOT",
        str(Path(__file__).resolve().parent.parent / "agent-files"),
    ))
    # SamSeg 遥感分析: 上传 & 输出
    samseg_upload_root: str = field(default_factory=lambda: _env(
        "SAMSEG_UPLOAD_ROOT",
        str(Path(__file__).resolve().parent.parent / "agent-files" / "samseg" / "send"),
    ))
    samseg_output_root: str = field(default_factory=lambda: _env(
        "SAMSEG_OUTPUT_ROOT",
        str(Path(__file__).resolve().parent.parent / "agent-files" / "samseg" / "generate"),
    ))
    # P1 栅格→矢量: 变化图斑/分割结果的矢量 GeoJSON 输出目录 (与 PNG 结果对齐)
    samseg_vector_root: str = field(default_factory=lambda: _env(
        "SAMSEG_VECTOR_ROOT",
        str(Path(__file__).resolve().parent.parent / "agent-files" / "samseg" / "vector"),
    ))
    # GeoServer: 图层下载
    geoserver_download_root: str = field(default_factory=lambda: _env(
        "GEOSERVER_DOWNLOAD_ROOT",
        str(Path(__file__).resolve().parent.parent / "agent-files" / "geoserver" / "generate"),
    ))
    # Sandbox: 沙盒工作区 (按 分组+会话 两级隔离, 与 samseg/geoserver 产物对齐)
    # 实际派生路径: {sandbox_workspace_dir}/{project_id}/{conversation_id}/
    sandbox_workspace_dir: str = field(default_factory=lambda: _env(
        "SANDBOX_WORKSPACE_DIR",
        str(Path(__file__).resolve().parent.parent / "agent-files" / "sandbox" / "workspace"),
    ))

    # ==================== SamSeg 遥感分析配置 ====================
    # SamSeg = SegEarth-OV3 (基于 SAM 3 的训练免开放词汇遥感分割/变化检测)
    # 默认开启但实际可用性由 runner.samseg_available() 检测 (torch 装了 + 权重在)
    samseg_enabled: bool = field(
        default_factory=lambda: _env("SAMSEG_ENABLED", "true").lower() == "true"
    )
    # 模型权重与 BPE 词表: 默认指向 SamSeg 子项目内的 sam3/ 目录 (绝对路径, 不依赖 CWD)
    samseg_model_path: str = field(default_factory=lambda: _env(
        "SAMSEG_MODEL_PATH",
        str(Path(__file__).resolve().parent / "model" / "SamSeg" / "SamSeg" / "sam3" / "sam3.pt"),
    ))
    samseg_bpe_path: str = field(default_factory=lambda: _env(
        "SAMSEG_BPE_PATH",
        str(Path(__file__).resolve().parent / "model" / "SamSeg" / "SamSeg" / "sam3" / "assets" / "bpe_simple_vocab_16e6.txt.gz"),
    ))
    # 计算设备: cuda / cpu (ViT-L 模型在 CPU 上极慢, 实际需 CUDA)
    samseg_device: str = field(default_factory=lambda: _env("SAMSEG_DEVICE", "cuda"))

    # ==================== Sandbox 沙盒配置 (Agent 自写代码执行环境) ====================
    # 沙盒让 Agent 在 Docker 容器中执行任意 Python/Shell 代码, 弥补预定义工具的不足。
    # 容器按 (client_id, work_dir) 隔离: client_id=对话ID, work_dir=按分组ID派生的工作区。
    sandbox_enabled: bool = field(
        default_factory=lambda: _env("SANDBOX_ENABLED", "true").lower() == "true"
    )
    # 沙盒 Docker 镜像名 (需先构建: docker build -t agent-sandbox:latest -f sandbox/Dockerfile .)
    sandbox_docker_image: str = field(default_factory=lambda: _env("SANDBOX_DOCKER_IMAGE", "agent-sandbox:latest"))
    # Compose/Docker-outside-of-Docker 场景: 后端容器与沙盒容器共享同一 Docker volume。
    # 为空时保持本机开发模式: 直接 bind mount work_dir 到沙盒 /workspace。
    sandbox_docker_volume: str = field(default_factory=lambda: _env("SANDBOX_DOCKER_VOLUME", ""))
    # Compose 场景下让沙盒加入同一网络, 可访问 postgres/geoserver 等服务名。
    # 为空时保持原行为: --network host。
    sandbox_docker_network: str = field(default_factory=lambda: _env("SANDBOX_DOCKER_NETWORK", ""))
    # 单次代码/命令执行超时 (秒), 防止死循环卡死
    sandbox_exec_timeout: int = field(default_factory=lambda: int(_env("SANDBOX_EXEC_TIMEOUT", "60")))
    # ★ 沙盒容器 RW 挂载的产物源列表 (相对 agent-files 的子路径)。
    #   每个源按当前 (proj短, conv短) 动态挂载到容器内 /files/{source_下划线化}, 实现会话隔离。
    #   默认只挂 samseg/generate (分割/变化结果); 需要矢量/分析产物时追加 "samseg/vector",
    #   "analysis", "report" 等 (逗号分隔)。例:
    #     SANDBOX_RW_MOUNT_SOURCES=samseg/generate,samseg/vector,analysis
    sandbox_rw_mount_sources: list = field(default_factory=lambda: _env_list(
        "SANDBOX_RW_MOUNT_SOURCES", ["samseg/generate"]
    ))

    # ==================== 派生属性 ====================
    @property
    def postgres_url(self) -> str:
        return f"postgresql://{self.postgres_user}:{self.postgres_password}@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"

    @property
    def agent_db_url(self) -> str:
        return f"postgresql://{self.agent_db_user}:{self.agent_db_password}@{self.agent_db_host}:{self.agent_db_port}/{self.agent_db_name}"

    @property
    def geoserver_wms_url(self) -> str:
        return f"{self.geoserver_url}/wms"

    @property
    def geoserver_rest_url(self) -> str:
        return f"{self.geoserver_url}/rest"


settings = Settings()
logger.info(f"配置加载完成: DB={settings.postgres_host}:{settings.postgres_port}/{settings.postgres_db}, GeoServer={settings.geoserver_url}")
