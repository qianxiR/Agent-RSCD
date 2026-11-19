# 06 MCP 协议

## 1. 目标

通过 MCP 接入外部工具服务器，把远程工具注册为 LangChain 工具，并纳入工具发现、schema、权限、超时、审计体系。

## 2. 当前基线

### 2.1 客户端

[`backend/agent/mcp/client.py`](../../backend/agent/mcp/client.py)

支持：

```text
SSE
stdio
```

每次 `list_tools` / `call_tool` 建立短连接，超时 8 秒。

### 2.2 动态注册

[`backend/agent/mcp/registry.py`](../../backend/agent/mcp/registry.py)

```text
读取 enabled 服务器
  -> list_tools
  -> 包装为 StructuredTool
  -> register_dynamic_tool(category="mcp", expose=True)
```

工具名：

```text
mcp__{server}__{tool}
```

刷新 TTL 300 秒，刷新时先移除旧 `mcp__*` 工具。

### 2.3 管理 API

[`backend/api/mcp.py`](../../backend/api/mcp.py)

```text
GET    /api/v1/mcp/servers
POST   /api/v1/mcp/servers
PATCH  /api/v1/mcp/servers/{id}
DELETE /api/v1/mcp/servers/{id}
POST   /api/v1/mcp/refresh
GET    /api/v1/mcp/tools
```

### 2.4 当前配置

```text
MCP_ENABLED=true
MCP_TIMEOUT_SECONDS=8
已配置服务器：0
```

## 3. 差距

1. 每次调用重新建立连接，无法复用 session。
2. 只支持 tools，不支持 resources、prompts、sampling。
3. 没有认证头、环境变量、密钥管理。
4. 没有服务器健康检查、退避、熔断。
5. 没有工具级权限和项目隔离。
6. 工具名冲突只做字符串截断，缺少稳定哈希。
7. MCP 工具 description 直接塞入 inputSchema 字符串，长度和可读性不可控。
8. 没有 MCP 调用审计。
9. 没有测试用 MCP server。
10. 没有工具 schema 转换校验；远程 schema 非法时可能污染 `bind_tools`。

## 4. 目标设计

### 4.1 连接层

```text
McpServer
  id, name, transport, url/command, enabled
  auth_type, auth_ref, env, timeout, retry
```

- SSE / stdio 统一接口。
- 连接池按 server 复用。
- 健康状态：`unknown/healthy/unhealthy`。
- 失败退避，不阻塞主聊天。

### 4.2 工具发现

```text
list_tools
  -> schema 校验
  -> 名称规范化
  -> 冲突检测
  -> 注册
```

工具元数据：

```json
{
  "server_id": "...",
  "remote_name": "...",
  "local_name": "mcp__server__tool",
  "input_schema": {},
  "description": "...",
  "enabled": true
}
```

### 4.3 调用

```text
LLM tool_call
  -> 校验 server/tool 启用
  -> 校验参数 schema
  -> 调用 MCP
  -> 转换 content
  -> 统一 ToolResult
```

### 4.4 安全

- 服务器配置只允许管理员写入。
- 密钥不进入数据库明文，使用环境变量或密钥引用。
- 工具调用按 server 记录审计。
- 默认拒绝未启用 server。
- 限制单次返回大小。

## 5. 改造任务

### P0

- [ ] 增加 MCP schema 校验与失败隔离。
- [ ] 增加连接复用或至少 session 缓存。
- [ ] 增加工具名冲突检测与稳定命名。
- [ ] 增加调用审计日志。
- [ ] 增加一个本地测试 MCP server。
- [ ] 增加 `list_tools` / `call_tool` 回归测试。

### P1

- [ ] 增加认证配置。
- [ ] 增加健康检查与退避。
- [ ] 增加 resources / prompts 支持评估。
- [ ] 增加 MCP 工具在工具目录中的分组。
- [ ] 增加超时、重试、熔断。

### P2

- [ ] 增加项目级 MCP 权限。
- [ ] 增加 MCP 服务器版本兼容检查。
- [ ] 增加 MCP 工具评估集。

## 6. 验收标准

```text
1. 未启用 server 不能被调用。
2. 非法 inputSchema 不会进入 bind_tools。
3. 工具名冲突可检测。
4. 每次 MCP 调用有 server/tool/耗时/状态记录。
5. 不可达 server 不阻塞主聊天。
6. 至少一个 SSE 或 stdio 测试 server 可完成 list/call。
```

## 7. 风险

- 远程工具质量不可控，必须保留主控验收。
- MCP session 复用会增加连接状态管理复杂度。
- 认证信息进入数据库会带来泄露风险。
