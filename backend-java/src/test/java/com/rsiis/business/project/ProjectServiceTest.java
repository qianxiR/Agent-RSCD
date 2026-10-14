package com.rsiis.business.project;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyMap;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

import java.util.List;

import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;

import com.baomidou.mybatisplus.core.conditions.Wrapper;
import com.rsiis.business.client.PythonInternalClient;

/**
 * ProjectService 单元测试 (Mock Mapper + Mock Python 客户端):
 * folder_path 复用语义、更新/删除存在性判断、删除时的会话解绑与降级。
 */
class ProjectServiceTest {

    private final ProjectMapper mapper = mock(ProjectMapper.class);
    private final PythonInternalClient python = mock(PythonInternalClient.class);
    private final ProjectService service = new ProjectService(mapper, python);

    @Test
    void createReusesExistingProjectByFolderPath() {
        ProjectEntity existing = new ProjectEntity();
        existing.setId("existing-id");
        existing.setFolderPath("D:\\data");
        when(mapper.selectOne(any())).thenReturn(existing);

        String id = service.create("u1",
                new CreateProjectRequest("新项目", "", "D:\\data"));

        assertThat(id).isEqualTo("existing-id");
        verify(mapper, never()).insert(any(ProjectEntity.class));
    }

    @Test
    void createInsertsNewProjectWithUuid() {
        when(mapper.selectOne(any())).thenReturn(null);

        String id = service.create("u1",
                new CreateProjectRequest("新项目", "描述", null));

        assertThat(id).isNotBlank();
        ArgumentCaptor<ProjectEntity> captor = ArgumentCaptor.forClass(ProjectEntity.class);
        verify(mapper).insert(captor.capture());
        ProjectEntity saved = captor.getValue();
        assertThat(saved.getId()).isEqualTo(id);
        assertThat(saved.getUserId()).isEqualTo("u1");
        assertThat(saved.getName()).isEqualTo("新项目");
        assertThat(saved.getDescription()).isEqualTo("描述");
    }

    @Test
    void updateReturnsFalseWhenProjectMissing() {
        when(mapper.update(any(), any(Wrapper.class))).thenReturn(0);
        assertThat(service.update("no-such",
                new UpdateProjectRequest("新名", null))).isFalse();
    }

    @Test
    void updateReturnsTrueWhenRowAffected() {
        when(mapper.update(any(), any(Wrapper.class))).thenReturn(1);
        assertThat(service.update("p1",
                new UpdateProjectRequest("新名", "新描述"))).isTrue();
    }

    @Test
    void deleteDetachesConversationsThenDeletesRow() {
        when(mapper.delete(any(Wrapper.class))).thenReturn(1);

        assertThat(service.delete("p1")).isTrue();

        // 会话解绑先于删行 (顺序由实现保证: detach 在 delete 调用之前)
        verify(python).post(eq("/internal/conversations/detach-project"),
                anyMap(), anyString());
        verify(mapper).delete(any(Wrapper.class));
    }

    @Test
    void deleteSurvivesPythonUnreachable() {
        when(mapper.delete(any(Wrapper.class))).thenReturn(1);
        when(python.post(anyString(), anyMap(), anyString()))
                .thenThrow(new IllegalStateException("Python AI 服务不可达"));

        // Python 不可达时解绑降级为日志, 删除本身不受阻塞
        assertThat(service.delete("p1")).isTrue();
    }

    @Test
    void listMapsEntityToView() {
        ProjectEntity e = new ProjectEntity();
        e.setId("p1");
        e.setUserId("u1");
        e.setName("项目");
        e.setDescription("描述");
        e.setFolderPath("D:\\data");
        when(mapper.selectList(any(Wrapper.class))).thenReturn(List.of(e));

        List<ProjectView> views = service.list("u1");

        assertThat(views).hasSize(1);
        assertThat(views.get(0).id()).isEqualTo("p1");
        assertThat(views.get(0).description()).isEqualTo("描述");
        assertThat(views.get(0).folderPath()).isEqualTo("D:\\data");
    }

    @Test
    void listMapsNullFolderAndDescriptionToEmptyString() {
        ProjectEntity e = new ProjectEntity();
        e.setId("p1");
        e.setName("项目");
        e.setDescription(null);
        e.setFolderPath(null);
        when(mapper.selectList(any(Wrapper.class))).thenReturn(List.of(e));

        List<ProjectView> views = service.list("u1");
        assertThat(views.get(0).description()).isEqualTo("");
        assertThat(views.get(0).folderPath()).isEqualTo("");
    }
}
