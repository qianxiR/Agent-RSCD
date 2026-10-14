package com.rsiis.business.workspace;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

/**
 * 工作区文件夹浏览 (自 Python backend/api/workspace.py 迁入)。
 *
 * 入参: 逗号分隔的根目录列表 + 递归深度
 * 处理: 安全校验 (拒绝 .. 穿越 / 拒绝 Windows 系统目录) 后按深度递归列子目录树
 * 出参: 树状节点列表 [{path, name, children?, error?}] (字段名与原接口一致)
 * 边界: 目录不可读返回空 children (原实现同); 单个根目录异常记入 error 字段不影响其他根
 */
@Service
public class WorkspaceService {

    private static final Logger log = LoggerFactory.getLogger(WorkspaceService.class);

    /** 系统目录黑名单 (Windows), 大小写不敏感前缀匹配。 */
    private static final List<String> SYSTEM_PREFIXES = List.of(
            "C:\\Windows", "C:\\Program Files", "C:\\Program Files (x86)",
            "C:\\ProgramData", "C:\\System Volume Information", "C:\\$Recycle.Bin"
    );

    private static final int MAX_DEPTH = 5;

    /**
     * 浏览入口: 校验每个根目录并构建子目录树。
     */
    public List<Map<String, Object>> browse(List<String> roots, int depth) {
        int bounded = Math.max(1, Math.min(depth, MAX_DEPTH));
        List<Map<String, Object>> result = new ArrayList<>();
        for (String raw : roots) {
            String root = raw == null ? "" : raw.trim();
            Map<String, Object> entry = new LinkedHashMap<>();
            entry.put("path", root);
            entry.put("name", fileNameOf(root));
            entry.put("children", new ArrayList<>());
            entry.put("error", null);
            if (root.isEmpty()) {
                continue;
            }
            try {
                String error = validate(root);
                if (error == null) {
                    Path abs = Path.of(root).toAbsolutePath().normalize();
                    entry.put("path", abs.toString());
                    entry.put("name", fileNameOf(abs.toString()));
                    entry.put("children", listChildren(abs, 0, bounded));
                } else {
                    entry.put("error", error);
                }
            } catch (Exception e) {
                entry.put("error", e.getMessage() == null ? "未知错误" : e.getMessage());
            }
            result.add(entry);
        }
        return result;
    }

    // ==================== 内部工具 ====================

    /**
     * 根目录安全校验。返回 null 表示通过, 否则返回错误消息。
     * ★ 黑名单比较在规范化之后进行: C:/Windows (正斜杠) 必须与 C:\Windows 同样被拒。
     */
    private String validate(String root) {
        if (root.contains("..")) {
            return "路径包含 ..，拒绝访问";
        }
        Path abs = Path.of(root).toAbsolutePath().normalize();
        String lower = abs.toString().toLowerCase(Locale.ROOT);
        for (String prefix : SYSTEM_PREFIXES) {
            if (lower.startsWith(prefix.toLowerCase(Locale.ROOT))) {
                return "系统目录，拒绝访问";
            }
        }
        if (!Files.isDirectory(abs)) {
            return "目录不存在或不可读";
        }
        return null;
    }

    /**
     * 递归列出子目录 (仅目录, 跳过 . 开头条目, 名称不区分大小写排序)。
     * currentDepth 达到 maxDepth 时返回空列表。
     */
    private List<Map<String, Object>> listChildren(Path dir, int currentDepth, int maxDepth) {
        if (currentDepth >= maxDepth) {
            return new ArrayList<>();
        }
        List<Map<String, Object>> children = new ArrayList<>();
        try (var stream = Files.list(dir)) {
            List<Path> dirs = new ArrayList<>(stream
                    .filter(Files::isDirectory)
                    .filter(p -> !p.getFileName().toString().startsWith("."))
                    .toList());
            dirs.sort(Comparator.comparing(p -> p.getFileName().toString()
                    .toLowerCase(Locale.ROOT)));
            for (Path d : dirs) {
                Map<String, Object> node = new LinkedHashMap<>();
                node.put("name", d.getFileName().toString());
                node.put("path", d.toString());
                List<Map<String, Object>> sub = listChildren(d, currentDepth + 1, maxDepth);
                if (!sub.isEmpty()) {
                    node.put("children", sub);
                }
                children.add(node);
            }
        } catch (IOException e) {
            // 目录不可读: 返回空列表, 与原实现一致
            log.debug("[Workspace] 目录不可读: {} ({})", dir, e.getMessage());
        }
        return children;
    }

    private String fileNameOf(String path) {
        String name = Path.of(path).getFileName() == null ? path
                : Path.of(path).getFileName().toString();
        return name.isEmpty() ? path : name;
    }
}
