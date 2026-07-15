"""
Docker 沙盒容器管理 (Model 层 / 工具箱)
- 入参: client_id (对话 ID), work_dir (分组+会话工作区路径)
- 方法: 按 (client_id, work_dir) 懒创建容器、执行代码/命令、回收容器
- 出参: {stdout, stderr, returncode}

设计要点 (仿 Apix agent_sandbox_manager):
  1. 多容器隔离: 每个 (client_id, work_dir) 一个独立容器, hash 作 key
     - client_id = conversation_id (对话 ID), 标识"谁在用"
     - work_dir  = 按 (project_id, conversation_id) 派生的工作区路径,
       标识"在哪个分组+会话的工作区操作"
     → 同一 (分组, 会话) 的多次调用共享同一工作区文件,
       不同会话相互隔离 (与 samseg/geoserver 产物目录结构一致)
  2. 懒创建: 首次调用工具时才 docker run, 启动时不创建
  3. bind mount: work_dir 挂载到容器 /workspace, 代码产物落盘可见
  4. 并发安全: 每个 key 一把 asyncio.Lock, 创建容器原子化
  5. 优雅降级: Docker/镜像不可用时返回明确错误, 不影响其他工具

依赖: 宿主机需安装 Docker 且已构建沙盒镜像 (见 sandbox/Dockerfile)。
"""
import asyncio
import hashlib
import logging
from pathlib import Path
from typing import Optional, Dict, Any
from uuid import uuid4

from backend.config import settings
from backend.utils.async_subprocess import run_subprocess

logger = logging.getLogger(__name__)

# 可用性检测缓存 (惰性, 只检测一次)
_availability_checked = False
_docker_available = False

# ★ 会话标签 key: 打在沙盒容器上, 形如 sandbox.conversation=<conversation_id>
#   用于"删除会话时回收容器": 按 label 反查容器, 不依赖随机容器名或内存索引。
SANDBOX_CONV_LABEL = "sandbox.conversation"


# ★ matplotlib 中文字体前导: 注入到每次 exec_python 的用户代码之前。
#   容器装了 fonts-noto-cjk, 但 matplotlib 字体缓存/mpl-data 配置可能让默认
#   sans-serif 仍是 DejaVu Sans, 导致中文标签 (如 "地貌"/"水体") 显示成方块并刷
#   "Glyph missing" 警告。这段代码运行时强制把 Noto Sans CJK SC 设为首选字体。
#   放 try/except 内: matplotlib 未装时不影响纯 numpy/pandas 代码。
_MATPLOTLIB_PREAMBLE = (
    "try:\n"
    "    import matplotlib\n"
    "    import matplotlib.font_manager as _fm\n"
    "    # 找到 Noto Sans CJK SC 的实际文件路径, 显式注册 (绕过缓存)\n"
    "    for _f in _fm.fontManager.ttflist:\n"
    "        if _f.name == 'Noto Sans CJK SC':\n"
    "            _fm.fontManager.addfont(_f.fname)\n"
    "            break\n"
    "    import matplotlib.pyplot as _plt\n"
    "    _plt.rcParams['font.sans-serif'] = ['Noto Sans CJK SC', 'DejaVu Sans']\n"
    "    _plt.rcParams['font.family'] = 'sans-serif'\n"
    "    _plt.rcParams['axes.unicode_minus'] = False  # 负号正常显示\n"
    "except Exception:\n"
    "    pass  # matplotlib 未装, 跳过\n"
)


class SandboxManager:
    """
    Docker 沙盒容器管理 (全局单例, 多容器复用)。

    容器按 (client_id, work_dir) 隔离:
      - hash(client_id + work_dir) 作为容器 key
      - 同 key 的请求复用同一容器
      - 不同 (分组, 会话) → 不同 work_dir → 不同容器相互隔离
    """

    def __init__(self):
        # key (hash) → {container_id, expire_at, status}
        self._containers: Dict[str, Dict] = {}
        # key → 锁 (保证单 key 创建容器原子性)
        self._locks: Dict[str, asyncio.Lock] = {}
        self._global_lock = asyncio.Lock()
        self._last_unavailable_reason = ""

    # ==================== 可用性检测 ====================

    async def is_available(self) -> bool:
        """
        检测 Docker + 沙盒镜像是否可用。
        ★ 缓存策略: 只有成功时才缓存 (返回 True 后不再重复检测);
          失败时不缓存, 下次调用会重新检测 (避免启动时镜像未构建好就永久判定不可用)。
        """
        global _availability_checked, _docker_available
        if _availability_checked and _docker_available:
            return True  # 成功过 → 直接复用

        if not settings.sandbox_enabled:
            self._last_unavailable_reason = "配置已禁用: SANDBOX_ENABLED=false"
            logger.info(f"[Sandbox] {self._last_unavailable_reason}")
            return False

        # 1. 检测 docker 命令是否可用
        try:
            version_info = await run_subprocess(["docker", "--version"])
            if version_info["returncode"] != 0:
                reason = version_info["stderr"].decode("utf-8", errors="replace").strip()
                self._last_unavailable_reason = reason or "docker 命令不可用"
                logger.warning(f"[Sandbox] {self._last_unavailable_reason}")
                return False
        except FileNotFoundError:
            self._last_unavailable_reason = "未找到 docker 命令, 沙盒功能不可用"
            logger.warning(f"[Sandbox] {self._last_unavailable_reason}")
            return False

        # 2. 检测沙盒镜像是否已构建
        image = settings.sandbox_docker_image
        try:
            inspect_info = await run_subprocess(["docker", "image", "inspect", image])
            if inspect_info["returncode"] != 0:
                detail = inspect_info["stderr"].decode("utf-8", errors="replace").strip()
                self._last_unavailable_reason = (
                    f"沙盒镜像或 Docker daemon 不可用: {detail or f'镜像 {image} 未构建'}"
                )
                logger.warning(f"[Sandbox] {self._last_unavailable_reason}")
                return False
        except Exception as e:
            self._last_unavailable_reason = f"镜像检测失败: {e!r}"
            logger.warning(f"[Sandbox] {self._last_unavailable_reason}")
            return False

        _docker_available = True
        _availability_checked = True
        self._last_unavailable_reason = ""
        logger.info(f"[Sandbox] 就绪: docker 镜像={image}")
        return True

    # ==================== work_dir 派生 ====================

    @staticmethod
    def _short_ids(project_id: str, conversation_id: str):
        """
        把 (project_id, conversation_id) 短化为目录名安全串 (UUID 前 8 位)。
        - 复用 _paths.ID_SHORT_LEN 常量, 保证与 samseg/geoserver 等产物目录的短化规则一致。
        - 兜底: 空值 → "_default"/"_anonymous" (与 _paths.PROJECT_FALLBACK 对齐)。
        出参: (proj_short, conv_short)
        """
        from backend.model.tools._paths import safe_id, PROJECT_FALLBACK, CONVERSATION_FALLBACK, ID_SHORT_LEN
        proj_full = safe_id(project_id or "", fallback=PROJECT_FALLBACK)
        conv_full = safe_id(conversation_id or "", fallback=CONVERSATION_FALLBACK)
        proj = proj_full if proj_full == PROJECT_FALLBACK else proj_full[:ID_SHORT_LEN]
        conv = conv_full if conv_full == CONVERSATION_FALLBACK else conv_full[:ID_SHORT_LEN]
        return proj, conv

    def resolve_work_dir(self, project_id: str, conversation_id: str = "") -> str:
        """
        根据 project_id (分组) + conversation_id (会话) 派生沙盒工作区路径。

        ★ v2.5 两级结构 + 短 ID (与 samseg/geoserver 产物对齐, 保证会话级隔离):
          {sandbox_workspace_root}/{proj短}/{conv短}/
          例: .../sandbox/workspace/449148e8/abc12345/

        - ★ 目录名用 UUID 前 8 位 (DB 主键仍是完整 UUID, 不受影响)。
        - 无 project_id → .../_default/...; 无 conversation_id → .../_anonymous/ (兜底)。

        同一 (分组, 会话) 的多次工具调用共享同一工作区文件;
        不同会话之间相互隔离, 不会互相覆盖同名产物。
        """
        root = Path(settings.sandbox_workspace_dir)
        sub_proj, sub_conv = self._short_ids(project_id, conversation_id)
        return str(root / sub_proj / sub_conv)

    def _build_key(self, client_id: str, work_dir: str) -> str:
        """(client_id, work_dir) → hash key"""
        raw = f"{client_id}:{work_dir}"
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    # ==================== 容器生命周期 ====================

    async def ensure_container(self, client_id: str, work_dir: str,
                               project_id: str = "", conversation_id: str = "") -> Optional[str]:
        """
        确保指定 (client_id, work_dir) 的沙盒容器存在且存活, 返回 container_id。
        已存活则复用; 不存活则重建。

        ★ v2.5: project_id/conversation_id 用于派生产物目录挂载 (见 _build_product_mount_args)。
          首次创建容器时按当前会话挂载 /files/* 产物目录; 已存在的同 key 容器复用其挂载。
          (容器的挂载点在创建时固定; 若运行中改了 sandbox_rw_mount_sources 配置, 需新建会话才生效。)
        """
        if not await self.is_available():
            logger.warning("[Sandbox] ensure_container 失败: is_available()=False")
            return None

        key = self._build_key(client_id, work_dir)
        lock = await self._get_lock(key)

        async with lock:
            entry = self._containers.get(key)

            if entry:
                container_id = entry["container_id"]
                if await self._container_alive(container_id):
                    return container_id
                # 容器已死 → 清理引用, 重建
                await self._safe_remove(container_id)
                del self._containers[key]

            container_id = await self._create_container(work_dir, project_id, conversation_id)
            if not container_id:
                logger.warning(f"[Sandbox] ensure_container 失败: _create_container 返回 None, work_dir={work_dir}")
                return None
            self._containers[key] = {"container_id": container_id}
            logger.info(
                    f"[Sandbox] 容器已创建: key={key} client={client_id[:8]} "
                    f"work_dir={work_dir} container={container_id[:12]}"
                )
            return container_id

    def _build_workspace_mount_args(self, work_dir: str) -> list:
        """
        入参:
            - work_dir: 当前会话沙盒工作区宿主视角路径, 必须位于 sandbox_workspace_dir 下。
        方法:
            - 本机开发模式使用 bind mount, 保持原有路径语义。
            - Compose 模式使用 Docker named volume + volume-subpath, 避免后端容器内路径
              被宿主 Docker daemon 当作宿主机路径解析。
        出参:
            - docker run 可直接拼接的挂载参数列表。
        """
        if not settings.sandbox_docker_volume:
            # 做什么: 保持原有 bind mount; 为什么: 本机非容器化开发时 work_dir 就是宿主机真实路径。
            return ["-v", f"{work_dir}:/workspace"]

        root = Path(settings.sandbox_workspace_dir).resolve()
        rel = Path(work_dir).resolve().relative_to(root).as_posix()
        volume_subpath = f"sandbox/workspace/{rel}"
        # 做什么: 使用 volume-subpath 只挂载当前会话目录; 为什么: 保持会话隔离且规避跨 OS 路径问题。
        return [
            "--mount",
            (
                f"type=volume,src={settings.sandbox_docker_volume},dst=/workspace,"
                f"volume-subpath={volume_subpath}"
            ),
        ]

    # ==================== 产物目录 RW 挂载 (v2.5 新增) ====================

    @staticmethod
    def _container_mount_name(source: str) -> str:
        """
        把产物源子路径转成容器内挂载点名。
        - 例: "samseg/generate" → "/files/samseg_generate"; "analysis" → "/files/analysis"
        - 规则: source 中的 "/" 换成 "_", 前缀 "/files/"。
        """
        safe = source.strip("/").replace("/", "_")
        return f"/files/{safe}"

    def resolve_product_mounts(self, project_id: str, conversation_id: str) -> list:
        """
        入参:
            - project_id, conversation_id: 当前会话的 (项目, 对话) id (原始值, 内部短化)。
        方法:
            - 遍历 settings.sandbox_rw_mount_sources, 对每个 source (相对 agent-files 子路径):
              算宿主路径 = file_storage_root / source / {proj短} / {conv短}, 确保目录存在。
        出参:
            - list of (host_path_str, container_path_str, source_str), 供挂载参数构建与 path_map 复用。
            - 任一 source 派生失败 (如路径越权) 跳过该条并记日志, 不影响其他 source。
        """
        root = Path(settings.file_storage_root).resolve()
        proj_short, conv_short = self._short_ids(project_id, conversation_id)
        mounts = []
        for source in settings.sandbox_rw_mount_sources:
            src = source.strip().strip("/")
            if not src:
                continue
            host_path = (root / src / proj_short / conv_short)
            # 防越权: host_path 必须落在 file_storage_root 内
            try:
                host_path.resolve().relative_to(root)
            except ValueError:
                logger.warning(f"[Sandbox] 产物挂载源越权, 跳过: source={src} -> {host_path}")
                continue
            try:
                host_path.mkdir(parents=True, exist_ok=True)
            except Exception as e:
                logger.warning(f"[Sandbox] 产物挂载目录创建失败, 跳过: {host_path} ({e!r})")
                continue
            container_path = self._container_mount_name(src)
            mounts.append((str(host_path), container_path, src))
        return mounts

    def _build_product_mount_args(self, mounts: list) -> list:
        """
        入参:
            - mounts: resolve_product_mounts 的返回值 [(host, container, source), ...]。
        方法:
            - 本机模式: 每条一个 bind mount (-v host:container)。
            - Compose 模式: 用 named volume + volume-subpath。
        出参:
            - docker run 可拼接的挂载参数列表 (可能为空, 表示无产物挂载)。
        """
        args = []
        if not settings.sandbox_docker_volume:
            for host_path, container_path, _src in mounts:
                args += ["-v", f"{host_path}:{container_path}"]
            return args

        volume = settings.sandbox_docker_volume
        root = Path(settings.file_storage_root).resolve()
        for host_path, container_path, src in mounts:
            # compose 模式下 host_path 是后端容器内路径, 需反算相对 file_storage_root 的 subpath
            try:
                rel = Path(host_path).resolve().relative_to(root).as_posix()
            except ValueError:
                continue
            args += [
                "--mount",
                f"type=volume,src={volume},dst={container_path},volume-subpath={rel}",
            ]
        return args

    def resolve_path_map(self, work_dir: str, project_id: str, conversation_id: str) -> dict:
        """
        入参:
            - work_dir: 当前会话沙盒写区宿主路径 (容器内 /workspace 的实体)。
            - project_id, conversation_id: 当前会话 id (内部短化派生产物路径)。
        出参:
            - 标准结构化路径映射 dict: {容器内路径: 宿主机路径}。
              例: {"/workspace": "E:\\...\\sandbox\\workspace\\proj8\\conv8",
                   "/files/samseg_generate": "E:\\...\\samseg\\generate\\proj8\\conv8"}
            - 由 sandbox_tools 注入到返回值的 data.path_map, 供 AI 精确解析路径。
            - 即使产物挂载列表为空, 也至少返回 {"/workspace": work_dir}。
        """
        path_map = {"/workspace": work_dir}
        for host_path, container_path, _src in self.resolve_product_mounts(project_id, conversation_id):
            path_map[container_path] = host_path
        return path_map

    def resolve_container_path_to_host(self, file_path: str, project_id: str,
                                       conversation_id: str, work_dir: str) -> str:
        """
        入参:
            - file_path: 容器内路径或 agent-files 相对路径。支持三种写法:
                ① /workspace/xxx                  → work_dir/xxx
                ② /files/{source_下划线}/xxx      → file_storage_root/{source还原}/{proj8}/{conv8}/xxx
                ③ agent-files/... 或 {source}/... 相对 → file_storage_root/...
        出参:
            - 对应的宿主机绝对路径 (字符串)。找不到匹配挂载点时返回空串。
        - 供 download_file_from_sandbox 工具与 /sandbox/download-to-host 端点共用。
        """
        from backend.model.tools._paths import safe_id, PROJECT_FALLBACK, CONVERSATION_FALLBACK, ID_SHORT_LEN
        fp = file_path.replace("\\", "/").strip()
        if not fp:
            return ""

        root = Path(settings.file_storage_root).resolve()
        proj_full = safe_id(project_id or "", fallback=PROJECT_FALLBACK)
        conv_full = safe_id(conversation_id or "", fallback=CONVERSATION_FALLBACK)
        proj_short = proj_full if proj_full == PROJECT_FALLBACK else proj_full[:ID_SHORT_LEN]
        conv_short = conv_full if conv_full == CONVERSATION_FALLBACK else conv_full[:ID_SHORT_LEN]

        # ① /workspace 前缀
        if fp == "/workspace" or fp.startswith("/workspace/"):
            rel = fp[len("/workspace"):].lstrip("/")
            return str(Path(work_dir) / rel) if rel else work_dir

        # ② /files/{source_下划线} 前缀: 反查挂载源
        if fp.startswith("/files/"):
            # 拆出第一段作为挂载点名 (如 /files/samseg_generate/x.tif → samseg_generate)
            rest = fp[len("/files/"):]
            seg, _, sub = rest.partition("/")
            # 反查 source: samseg_generate → samseg/generate
            src_map = {self._container_mount_name(s).replace("/files/", ""): s
                       for s in settings.sandbox_rw_mount_sources}
            src = src_map.get(seg)
            if not src:
                return ""
            return str(root / src.strip("/") / proj_short / conv_short / sub) if sub else \
                   str(root / src.strip("/") / proj_short / conv_short)

        # ③ 相对 agent-files 的写法 (如 samseg/generate/... 或 agent-files/samseg/...)
        #   ★ 防裸文件名误解析: 只接受以已知来源前缀开头的相对路径,
        #     拒绝裸字符串 (如 "test"/"x.png") 被误当成 agent-files/test。
        KNOWN_SOURCES = (
            "samseg/", "geoserver/", "report/", "analysis/",
            "preprocess/", "sandbox/", "smoke_test/",
        )
        rel = fp
        if rel.lower().startswith("agent-files/"):
            rel = rel[len("agent-files/"):]
        if not any(rel.lower().startswith(s) for s in KNOWN_SOURCES):
            # 不是已知来源前缀 → 拒绝 (避免任意字符串被当相对路径)
            return ""
        candidate = (root / rel).resolve()
        try:
            candidate.relative_to(root)
            return str(candidate)
        except ValueError:
            return ""

    async def _create_container(self, work_dir: str,
                                project_id: str = "", conversation_id: str = "") -> Optional[str]:
        """
        入参:
            - work_dir: 当前会话沙盒工作区路径 (/workspace 的宿主实体)。
            - project_id, conversation_id: 用于派生产物目录 RW 挂载 (/files/*)。
        方法:
            - 确保 work_dir + 产物目录存在后 docker run 创建保活容器。
            - 同时挂载 /workspace (写区) 和 /files/{source} (本会话产物 RW 挂载点)。
        出参:
            - 成功返回 container_id, 失败返回 None 并记录最近失败原因。
        """
        # 确保 work_dir 存在
        Path(work_dir).mkdir(parents=True, exist_ok=True)

        # 确保产物挂载目录存在 + 构建挂载参数 (本会话隔离)
        product_mounts = self.resolve_product_mounts(project_id, conversation_id)
        for host_path, _cp, _src in product_mounts:
            logger.info(f"[Sandbox] 产物挂载: {_src} -> {_cp} ({host_path})")

        container_name = f"agent_sandbox_{uuid4().hex[:8]}"
        network_args = ["--network", settings.sandbox_docker_network or "host"]
        # ★ 会话标签: 打 label 便于按 conversation_id 反查回收。
        #   容器名是随机 uuid, 无法从 conversation_id 反推; 内存索引 _containers
        #   在后端重启后丢失, label 是重启后兜底回收孤儿容器的唯一线索。
        label_args = []
        if conversation_id:
            label_args = ["--label", f"{SANDBOX_CONV_LABEL}={conversation_id}"]
        cmd = [
            "docker", "run",
            "-d",
            "--rm",                            # 容器停止时自动删除
            "--name", container_name,
            *label_args,
            *network_args,
            *self._build_workspace_mount_args(work_dir),
            *self._build_product_mount_args(product_mounts),
            settings.sandbox_docker_image,
            "tail", "-f", "/dev/null",         # 保活
        ]
        try:
            create_info = await run_subprocess(cmd)
            if create_info["returncode"] != 0:
                self._last_unavailable_reason = create_info["stderr"].decode("utf-8", errors="replace").strip()
                logger.error(f"[Sandbox] 容器创建失败: {self._last_unavailable_reason}")
                return None
            return create_info["stdout"].decode().strip()
        except Exception as e:
            self._last_unavailable_reason = f"容器创建异常: {e!r}"
            logger.error(f"[Sandbox] {self._last_unavailable_reason}")
            return None

    async def _container_alive(self, container_id: str) -> bool:
        """检查容器是否存活"""
        try:
            inspect_info = await run_subprocess(
                ["docker", "inspect", "-f", "{{.State.Running}}", container_id]
            )
            return inspect_info["returncode"] == 0 and inspect_info["stdout"].decode().strip() == "true"
        except Exception:
            return False

    async def _safe_remove(self, container_id: str):
        """停止并移除容器 (静默)"""
        try:
            await run_subprocess(["docker", "stop", container_id])
        except Exception:
            pass

    async def _get_lock(self, key: str) -> asyncio.Lock:
        async with self._global_lock:
            if key not in self._locks:
                self._locks[key] = asyncio.Lock()
            return self._locks[key]

    # ==================== 代码/命令执行 ====================

    async def exec_python(self, client_id: str, work_dir: str,
                          code: str, timeout: int = None,
                          project_id: str = "", conversation_id: str = "") -> Dict[str, Any]:
        """
        在 (client_id, work_dir) 对应的沙盒中执行 Python 代码。
        - 写入临时 .py 文件 → docker exec python3 → 返回结果
        - ★ 工作目录固定为容器内 /workspace (与 bind mount 一致),
          保证代码里的相对路径 (如 plt.savefig("chart.png")) 落到挂载区,
          宿主机和 render_sandbox_image 才能看到产物。
        - ★ project_id/conversation_id 透传给 ensure_container, 用于本会话产物目录挂载。
        """
        container_id = await self.ensure_container(client_id, work_dir, project_id, conversation_id)
        if not container_id:
            logger.warning(f"[Sandbox] exec_python 失败: ensure_container 返回 None, client={client_id[:12]}...")
            return self._unavailable_result()

        logger.info(f"[Sandbox] exec_python: container={container_id[:12]}... code_len={len(code)}")

        timeout = timeout or settings.sandbox_exec_timeout

        # 写临时脚本到 workspace (通过 bind mount, 容器内可见)
        tmp_dir = Path(work_dir) / ".tmp_exec"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        script_name = f"_{uuid4().hex}.py"
        host_script = tmp_dir / script_name
        container_script = f"/workspace/.tmp_exec/{script_name}"

        try:
            # ★ 注入 matplotlib 中文字体前导: 容器装了 fonts-noto-cjk 但 matplotlibrc
            #   可能被字体缓存覆盖, 导致中文标签用 DejaVu Sans 显示成方块。
            #   在用户代码前强制 rcParams 指向 Noto Sans CJK SC, 彻底消除 Glyph missing 警告。
            full_code = _MATPLOTLIB_PREAMBLE + code
            host_script.write_text(full_code, encoding="utf-8")
            # ★ -w /workspace: 强制工作目录为挂载区, os.getcwd() 返回 /workspace,
            #    相对路径 savefig 也能落到宿主机可见的目录
            cmd = ["docker", "exec", "-w", "/workspace", container_id, "python3", container_script]
            return await self._run_with_timeout(cmd, timeout)
        finally:
            try:
                host_script.unlink(missing_ok=True)
            except Exception:
                pass

    async def exec_shell(self, client_id: str, work_dir: str,
                         command: str, timeout: int = None,
                         project_id: str = "", conversation_id: str = "") -> Dict[str, Any]:
        """
        在 (client_id, work_dir) 对应的沙盒中执行 Shell 命令。
        - ★ 工作目录固定为容器内 /workspace, 与代码执行一致。
        - ★ project_id/conversation_id 透传给 ensure_container, 用于本会话产物目录挂载。
        """
        container_id = await self.ensure_container(client_id, work_dir, project_id, conversation_id)
        if not container_id:
            return self._unavailable_result()

        timeout = timeout or settings.sandbox_exec_timeout
        # ★ -w /workspace: 同 exec_python, 保证 pwd/cd/相对路径行为一致
        cmd = ["docker", "exec", "-w", "/workspace", container_id, "bash", "-lc", command]
        return await self._run_with_timeout(cmd, timeout)

    # ==================== 内部辅助 ====================

    async def _run_with_timeout(self, cmd: list, timeout: int) -> Dict[str, Any]:
        """执行命令并带超时控制, 返回统一格式结果"""
        try:
            exec_info = await run_subprocess(cmd, timeout=timeout)
            stdout_text = exec_info["stdout"].decode("utf-8", errors="replace")
            stderr_text = exec_info["stderr"].decode("utf-8", errors="replace")
            returncode = exec_info["returncode"]

            # 截断过长输出
            max_len = 8000
            if len(stdout_text) > max_len:
                stdout_text = stdout_text[:max_len] + "\n...[stdout truncated]"
            if len(stderr_text) > max_len:
                stderr_text = stderr_text[:max_len] + "\n...[stderr truncated]"

            return {
                "type": "success" if returncode == 0 else "error",
                "stdout": stdout_text,
                "stderr": stderr_text,
                "returncode": returncode,
            }
        except Exception as e:
            return {
                "type": "error",
                "stdout": "",
                "stderr": str(e),
                "returncode": -1,
            }

    def _unavailable_result(self) -> Dict[str, Any]:
        """
        入参:
            - 无。读取最近一次 Docker/镜像/容器检测失败原因。
        方法:
            - 优先返回真实失败原因, 无记录时返回构建镜像提示。
        出参:
            - 沙盒不可用的标准错误结果, stderr/msg 均非空。
        """
        reason = self._last_unavailable_reason or (
            "Docker 未安装、Docker daemon 未启动或沙盒镜像未构建。"
        )
        msg = (
            f"沙盒不可用: {reason} "
            f"请确认 Docker Desktop/daemon 已启动, 并执行: "
            f"docker build -t {settings.sandbox_docker_image} -f sandbox/Dockerfile ."
        )
        # ★ 终极兜底: 即使所有字符串拼接后为空 (理论上不可能), 也返回有意义的消息
        if not msg.strip():
            msg = (
                f"沙盒不可用 (未知原因)。"
                f"请确认 Docker Desktop/daemon 已启动, 并执行: "
                f"docker build -t {settings.sandbox_docker_image} -f sandbox/Dockerfile ."
            )
        return {
            "type": "error",
            "stdout": "",
            "stderr": msg,
            "msg": msg,
            "returncode": -1,
        }

    async def cleanup_by_conversation(self, conversation_id: str,
                                      project_id: str = "") -> int:
        """
        回收指定会话的沙盒容器 (会话级回收, best-effort)。

        入参:
            - conversation_id: 要回收的会话 ID (= 沙盒 client_id)。
            - project_id: 会话所属分组 ID。若提供则只清内存索引中精确匹配的 key;
              留空时跳过内存索引反查 (label 兜底仍会查 Docker)。
        方法:
            1. 反算 work_dir, 从内存索引 _containers 删除对应 key (无论容器是否存活);
            2. docker ps --filter label=sandbox.conversation=<conv_id> 兜底查找
               (重启后内存索引丢失, label 是唯一线索), 对找到的容器 docker stop;
            3. 容器带 --rm, stop 后由 Docker 自动删除。
        出参:
            - 实际停止的容器数量。全程静默, 失败仅日志, 不抛异常。
        ★ 设计: 回收不阻塞调用方 (会话删除), 任何环节出错都吞掉异常,
          保证删会话/回滚主流程不受沙盒回收失败影响。
        """
        if not conversation_id:
            return 0

        stopped = 0

        # 1) 清内存索引 (同一 conversation_id 理论上只对应一个 key)
        if project_id:
            work_dir = self.resolve_work_dir(project_id, conversation_id)
            client_id = conversation_id
            key = self._build_key(client_id, work_dir)
            entry = self._containers.pop(key, None)
            if entry:
                cid = entry["container_id"]
                await self._safe_remove(cid)
                stopped += 1
                logger.info(
                    f"[Sandbox] 会话级回收 (内存索引): conv={conversation_id[:8]} "
                    f"key={key} container={cid[:12]}"
                )

        # 2) label 兜底: 内存索引可能丢失 (后端重启), 按 label 反查 Docker 中的容器。
        #    这一步覆盖"重启前创建、重启后变孤儿"的场景。
        try:
            list_info = await run_subprocess([
                "docker", "ps", "-q",
                "--filter", f"label={SANDBOX_CONV_LABEL}={conversation_id}",
            ])
            if list_info["returncode"] == 0:
                orphans = [
                    c.strip() for c in list_info["stdout"].decode(errors="replace").splitlines()
                    if c.strip()
                ]
                for cid in orphans:
                    await self._safe_remove(cid)
                    stopped += 1
                if orphans:
                    logger.info(
                        f"[Sandbox] 会话级回收 (label 兜底): conv={conversation_id[:8]} "
                        f"停止 {len(orphans)} 个容器"
                    )
        except Exception as e:
            logger.warning(f"[Sandbox] 会话级回收 label 查询失败 conv={conversation_id[:8]}: {e}")

        return stopped

    async def cleanup_all(self):
        """手动清理所有沙盒容器 (服务退出时可选调用)"""
        for entry in list(self._containers.values()):
            await self._safe_remove(entry["container_id"])
        self._containers.clear()
        logger.info("[Sandbox] 所有沙盒容器已清理")


# 全局单例
sandbox_manager = SandboxManager()
