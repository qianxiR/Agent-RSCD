package com.rsiis.business.workspace;

import java.util.Arrays;
import java.util.List;
import java.util.Map;

import org.springframework.http.MediaType;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

/**
 * 工作区文件夹浏览路由 (/api/v1/workspace/list), 参数与响应格式对齐原 Python 实现。
 */
@RestController
public class WorkspaceController {

    private final WorkspaceService workspaceService;

    public WorkspaceController(WorkspaceService workspaceService) {
        this.workspaceService = workspaceService;
    }

    /**
     * 列出根目录下的子文件夹树。
     *
     * @param roots 逗号分隔的绝对路径列表
     * @param depth 递归深度 (1-5, 默认 3)
     */
    @GetMapping(value = "/api/v1/workspace/list", produces = MediaType.APPLICATION_JSON_VALUE)
    public Map<String, Object> list(@RequestParam(defaultValue = "") String roots,
                                    @RequestParam(defaultValue = "3") int depth) {
        List<String> rootList = roots.isEmpty() ? List.of()
                : Arrays.stream(roots.split(",")).map(String::trim)
                        .filter(s -> !s.isEmpty()).toList();
        return Map.of("roots", workspaceService.browse(rootList, depth));
    }
}
