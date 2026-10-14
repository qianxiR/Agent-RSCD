package com.rsiis.business.project;

/**
 * 更新项目请求体 (对齐原 Python UpdateProjectRequest, 至少传一个字段)。
 *
 * @param name        新项目名 (null 表示不修改)
 * @param description 新描述 (null 表示不修改)
 */
public record UpdateProjectRequest(String name, String description) {
}
