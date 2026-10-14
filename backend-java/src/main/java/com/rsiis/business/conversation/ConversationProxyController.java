package com.rsiis.business.conversation;

import java.util.Map;

import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PatchMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import com.rsiis.business.client.PythonInternalClient;

/**
 * 会话管理代理路由 (/api/v1/conversations)。
 *
 * 会话/消息数据归 Python (agent_db), 本服务按定稿方案做纯代理:
 * 路径/参数/响应体原样转发到 Python /internal/conversations, 不改写字段。
 * 前端交互协议与代理前完全一致 (含 fork/regenerate/branches 消息分支)。
 */
@RestController
@RequestMapping("/api/v1/conversations")
public class ConversationProxyController {

    private final PythonInternalClient python;

    public ConversationProxyController(PythonInternalClient python) {
        this.python = python;
    }

    @GetMapping(produces = MediaType.APPLICATION_JSON_VALUE)
    public ResponseEntity<String> list(
            @RequestParam(defaultValue = "study_user") String userId,
            @RequestParam(required = false) String projectId) {
        return PythonInternalClient.toResponse(python.get("/internal/conversations",
                PythonInternalClient.query("user_id", userId, "project_id", projectId)));
    }

    @GetMapping(value = "/{conversationId}/messages", produces = MediaType.APPLICATION_JSON_VALUE)
    public ResponseEntity<String> messages(@PathVariable String conversationId) {
        return PythonInternalClient.toResponse(
                python.get("/internal/conversations/" + conversationId + "/messages", Map.of()));
    }

    @PatchMapping(value = "/{conversationId}", produces = MediaType.APPLICATION_JSON_VALUE)
    public ResponseEntity<String> update(@PathVariable String conversationId,
                                         @RequestBody String body) {
        return PythonInternalClient.toResponse(
                python.patch("/internal/conversations/" + conversationId, Map.of(), body));
    }

    @PutMapping(value = "/{conversationId}/trace", produces = MediaType.APPLICATION_JSON_VALUE)
    public ResponseEntity<String> updateTrace(@PathVariable String conversationId,
                                              @RequestBody String body) {
        return PythonInternalClient.toResponse(
                python.put("/internal/conversations/" + conversationId + "/trace", Map.of(), body));
    }

    /**
     * 删除会话。Python 侧完成 DB 删除 + 沙盒/磁盘回收编排 (fire-and-forget)。
     */
    @DeleteMapping(value = "/{conversationId}", produces = MediaType.APPLICATION_JSON_VALUE)
    public ResponseEntity<String> delete(@PathVariable String conversationId) {
        return PythonInternalClient.toResponse(
                python.delete("/internal/conversations/" + conversationId, Map.of()));
    }

    @PostMapping(value = "/{conversationId}/fork", produces = MediaType.APPLICATION_JSON_VALUE)
    public ResponseEntity<String> fork(@PathVariable String conversationId,
                                       @RequestBody String body) {
        return PythonInternalClient.toResponse(
                python.post("/internal/conversations/" + conversationId + "/fork", Map.of(), body));
    }

    @PostMapping(value = "/{conversationId}/regenerate", produces = MediaType.APPLICATION_JSON_VALUE)
    public ResponseEntity<String> regenerate(@PathVariable String conversationId,
                                             @RequestBody String body) {
        return PythonInternalClient.toResponse(python.post(
                "/internal/conversations/" + conversationId + "/regenerate", Map.of(), body));
    }

    @GetMapping(value = "/{conversationId}/branches", produces = MediaType.APPLICATION_JSON_VALUE)
    public ResponseEntity<String> branches(@PathVariable String conversationId) {
        return PythonInternalClient.toResponse(
                python.get("/internal/conversations/" + conversationId + "/branches", Map.of()));
    }
}
