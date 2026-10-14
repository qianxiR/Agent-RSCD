package com.rsiis.business.project;

import jakarta.validation.constraints.NotBlank;

/**
 * 创建项目请求体 (对齐原 Python CreateProjectRequest)。
 *
 * @param name        项目名
 * @param description 描述 (可空)
 * @param folderPath  关联本地文件夹 (可空; 非空时走 UPSERT: 同路径复用已有项目)
 */
public record CreateProjectRequest(
        @NotBlank(message = "项目名不能为空") String name,
        String description,
        String folderPath
) {
}
