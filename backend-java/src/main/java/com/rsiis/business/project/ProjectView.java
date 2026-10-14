package com.rsiis.business.project;

import java.time.OffsetDateTime;

/**
 * 项目列表视图 (对齐原 Python list_projects 返回字段; 时间由 Jackson 序列化为 ISO-8601)。
 *
 * 与 Python 版差异: 不再返回 conversation_count — 会话库在 Python 侧,
 * 跨库统计不做 (前端未使用该字段), 需要时由前端基于会话列表自行统计。
 */
public record ProjectView(
        String id,
        String name,
        String description,
        OffsetDateTime createdAt,
        OffsetDateTime updatedAt,
        String folderPath
) {
}
