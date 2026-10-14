package com.rsiis.business.workspace;

import static org.assertj.core.api.Assertions.assertThat;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import java.util.Map;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

/**
 * WorkspaceService 单元测试: 安全校验 (系统目录/路径穿越/不存在) 与目录树构建。
 */
class WorkspaceServiceTest {

    private final WorkspaceService service = new WorkspaceService();

    @Test
    void rejectsSystemDirectory() {
        List<Map<String, Object>> roots = service.browse(List.of("C:\\Windows\\System32"), 2);
        assertThat(roots).hasSize(1);
        assertThat(roots.get(0).get("error")).isEqualTo("系统目录，拒绝访问");
    }

    @Test
    void rejectsSystemDirectoryWithForwardSlashes() {
        // 正斜杠写法在规范化后同样命中黑名单 (不可绕过)
        List<Map<String, Object>> roots = service.browse(List.of("C:/Windows"), 2);
        assertThat(roots.get(0).get("error")).isEqualTo("系统目录，拒绝访问");
    }

    @Test
    void rejectsPathTraversal() {
        List<Map<String, Object>> roots = service.browse(List.of("D:\\data\\..\\..\\etc"), 2);
        assertThat(roots.get(0).get("error")).isEqualTo("路径包含 ..，拒绝访问");
    }

    @Test
    void missingDirectoryReportsError() {
        List<Map<String, Object>> roots = service.browse(List.of("D:\\definitely-not-exists-xyz"), 2);
        assertThat(roots.get(0).get("error")).isEqualTo("目录不存在或不可读");
    }

    @Test
    @SuppressWarnings("unchecked")
    void buildsTreeWithDepthLimitAndSkipsHidden(@TempDir Path temp) throws IOException {
        // 结构: temp/{a/{a1}, b, .hidden}
        Files.createDirectories(temp.resolve("a").resolve("a1"));
        Files.createDirectories(temp.resolve("b"));
        Files.createDirectories(temp.resolve(".hidden"));

        List<Map<String, Object>> roots = service.browse(List.of(temp.toString()), 1);
        Map<String, Object> root = roots.get(0);
        assertThat(root.get("error")).isNull();

        // 深度 1: 只列 temp 直接子目录, 隐藏目录被过滤
        List<Map<String, Object>> children = (List<Map<String, Object>>) root.get("children");
        assertThat(children).extracting(c -> c.get("name")).containsExactly("a", "b");

        // 深度 1 时不递归下一层
        Map<String, Object> nodeA = children.get(0);
        assertThat(nodeA).doesNotContainKey("children");

        // 深度 2: a 下出现 a1
        List<Map<String, Object>> deep = (List<Map<String, Object>>)
                service.browse(List.of(temp.toString()), 2).get(0).get("children");
        Map<String, Object> deepA = deep.stream()
                .filter(c -> c.get("name").equals("a")).findFirst().orElseThrow();
        assertThat((List<Map<String, Object>>) deepA.get("children"))
                .extracting(c -> c.get("name")).containsExactly("a1");
    }

    @Test
    void depthIsBoundedToFive() {
        // 超界深度收敛为 5, 不抛异常即可 (深度上限保护逻辑)
        List<Map<String, Object>> roots = service.browse(List.of("D:\\"), 99);
        assertThat(roots).hasSize(1);
    }
}
