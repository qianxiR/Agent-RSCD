# 知识库子层 (Knowledge)

本子层承载 **知识库**（可被检索的领域知识），与业务库（`business_db`）职责不同：

| 子层 | 职责 | 数据形态 |
|------|------|---------|
| `business_db` | 业务数据（湖泊/波段） | 结构化表，SQL 查询 |
| `knowledge` | 领域知识（法规/手册/案例/方法说明） | 待定，见下 |

## 当前状态：骨架占位

`knowledge_store.py` 定义了统一访问接口（`search` / `ingest` / `knowledge_available`），
当前为骨架，`knowledge_available()` 恒返回 `False`，`search` 返回空列表，`ingest` 返回 0。

目的：先建立分层骨架，不引入具体实现与新依赖，后续按需选型填充。

## 后续选型方向（任选其一）

### 方案 A：pgvector 向量库（推荐，复用现有 PostgreSQL）
- 启用 PostgreSQL 的 `pgvector` 扩展
- 建知识片段表：`knowledge_chunk(id, content, embedding vector, metadata jsonb)`
- `ingest`：文本 → embedding → 写入向量列
- `search`：query → embedding → 余弦相似度检索 top_k
- 优点：无需额外服务，与现有 DB 同栈

### 方案 B：结构化 PG 表
- 在业务库建知识表（条款/FAQ/操作手册等结构化知识）
- `search` 用全文检索（`tsvector`）或 LIKE
- 优点：实现简单；缺点：不适合语义检索

### 方案 C：外部向量服务
- 对接 Milvus / Qdrant 等专用向量库
- 优点：高性能；缺点：引入额外服务依赖

## 配置预留

后续接入时，在 `backend/config.py` 补充 `knowledge_*` 配置项（连接串、embedding 模型、表名等）。
