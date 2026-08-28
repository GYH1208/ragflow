# 多部门团队知识库管理 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让系统管理员创建多个部门团队，并把自己拥有的知识库分配给其中一个团队，由已接受邀请的成员维护知识库内容。

**Architecture:** 保留现有 Tenant 作为所有权、模型、存储与索引边界，新增 `Team`、`TeamMember` 和 `Knowledgebase.team_id` 作为独立授权层。所有知识库可见性和内容维护权限集中经过团队权限服务；团队治理和知识库分配要求系统管理员身份。

**Tech Stack:** Python 3.13、Quart、Peewee、Pydantic、pytest、React、TypeScript、React Query、React Hook Form、Zod、Jest/Testing Library。

**Spec:** `docs/superpowers/specs/2026-08-27-multi-kb-teams-design.md`

## Global Constraints

- 一个知识库最多分配给一个团队。
- `Knowledgebase.tenant_id` 始终保留为知识库所有者、模型、存储和索引租户。
- `permission="me"` 必须对应 `team_id=NULL`；`permission="team"` 必须对应同一 Tenant 下的有效团队。
- 只有 `User.is_superuser=true` 的用户可以创建、重命名、删除团队，邀请或移除成员，以及分配知识库。
- 管理员只能管理 `Team.tenant_id == current_user.id` 的团队，只能分配 `Knowledgebase.tenant_id == current_user.id` 的知识库。
- 不设置团队管理员，不支持一库多团队，不扩展其他资源的团队权限。
- 团队成员只获得知识库内容维护权限，不得修改核心配置、团队分配或删除知识库。
- 迁移不得移动文档、切片、向量索引、文件、模型配置、额度或 API Token。
- 所有后端变更先写失败测试；每个任务独立提交，提交信息使用中文。
- 当前环境缺少 Node/npm；前端测试命令必须记录并在具备 Node/npm 的环境执行，不能把“未执行”报告成通过。

---

## File Structure

### Backend

- `api/db/__init__.py`：新增团队成员状态枚举。
- `api/db/db_models.py`：定义 `Team`、`TeamMember`，为 `Knowledgebase` 增加 `team_id`，挂接字段迁移。
- `api/db/team_migration.py`：实现旧 `UserTenant` 与共享知识库到默认团队的幂等回填。
- `api/db/services/team_service.py`：团队查询、成员关系和集中授权判断。
- `api/apps/services/team_api_service.py`：团队治理、邀请、接受/拒绝/退出和事务删除用例。
- `api/apps/restful_apis/team_api.py`：`/api/v1/teams` HTTP 路由。
- `api/utils/validation_utils.py`：团队及知识库 `team_id` 请求校验。
- `api/utils/email_templates.py`、`api/utils/web_utils.py`：部门团队邀请邮件。
- `api/db/services/knowledgebase_service.py`：按团队 ID 过滤知识库可见性。
- `api/db/services/knowledgebase_category_service.py`：分类统计只计算当前用户可见的知识库。
- `api/apps/services/dataset_api_service.py`：知识库创建/更新时校验团队分配并返回团队名称。
- `api/common/check_team_permission.py`：内容接口统一改用新团队成员关系。

### Frontend

- `web/src/interfaces/database/user-setting.ts`：团队、成员和邀请类型。
- `web/src/interfaces/database/dataset.ts`：知识库 `team_id`、`team_name` 字段。
- `web/src/utils/api.ts`、`web/src/services/team-service.ts`：团队 API 客户端。
- `web/src/hooks/use-team-request.ts`：团队 React Query 查询与 mutation。
- `web/src/pages/user-setting/setting-team/*`：多团队管理和邀请 UI。
- `web/src/pages/dataset/dataset-setting/permission-form-field.tsx`：权限与团队联动选择。
- `web/src/pages/dataset/dataset-setting/form-schema.ts`、`saving-button.tsx`、`index.tsx`：表单校验与提交 `team_id`。
- `web/src/pages/datasets/dataset-creating-dialog.tsx`：创建知识库时选择团队。
- `web/src/pages/datasets/dataset-card.tsx`：展示团队标签。
- `web/src/locales/en.ts`、`web/src/locales/zh.ts`：新增界面文案。

---

### Task 1: 数据模型与幂等兼容迁移

**Files:**
- Modify: `api/db/__init__.py`
- Modify: `api/db/db_models.py`
- Create: `api/db/team_migration.py`
- Create: `test/unit_test/api/db/test_team_models.py`
- Create: `test/unit_test/api/db/test_team_migration.py`

**Interfaces:**
- Produces: `TeamMemberState.INVITED`, `TeamMemberState.ACTIVE`。
- Produces: `Team(id, tenant_id, name, created_by, status)`。
- Produces: `TeamMember(id, team_id, user_id, state, invited_by, status)`。
- Produces: `backfill_default_teams() -> dict[str, int]`，返回 `teams_created`、`members_created`、`datasets_assigned`。
- Produces: `Knowledgebase.team_id: str | None`。

- [ ] **Step 1: 写模型元数据失败测试**

```python
from api.db import TeamMemberState
from api.db.db_models import Knowledgebase, Team, TeamMember


def test_team_schema_constraints():
    assert TeamMemberState.INVITED.value == "invited"
    assert TeamMemberState.ACTIVE.value == "active"
    assert (("tenant_id", "name"), True) in Team._meta.indexes
    assert (("team_id", "user_id"), True) in TeamMember._meta.indexes
    assert Knowledgebase.team_id.null is True
    assert Knowledgebase.team_id.index is True
```

- [ ] **Step 2: 运行模型测试并确认失败**

Run: `POLARS_SKIP_CPU_CHECK=1 PYTHONPATH=$(pwd) /home/qaadmin/ragflow/.venv/bin/pytest -q test/unit_test/api/db/test_team_models.py`

Expected: FAIL，原因是 `TeamMemberState`、`Team`、`TeamMember` 或 `Knowledgebase.team_id` 尚不存在。

- [ ] **Step 3: 实现枚举和 Peewee 模型**

```python
class TeamMemberState(StrEnum):
    INVITED = "invited"
    ACTIVE = "active"


class Team(DataBaseModel):
    id = CharField(max_length=32, primary_key=True)
    tenant_id = CharField(max_length=32, null=False, index=True)
    name = CharField(max_length=100, null=False, index=True)
    created_by = CharField(max_length=32, null=False, index=True)
    status = CharField(max_length=1, null=False, default="1", index=True)

    class Meta:
        db_table = "team"
        indexes = ((("tenant_id", "name"), True),)


class TeamMember(DataBaseModel):
    id = CharField(max_length=32, primary_key=True)
    team_id = CharField(max_length=32, null=False, index=True)
    user_id = CharField(max_length=32, null=False, index=True)
    state = CharField(max_length=16, null=False, index=True)
    invited_by = CharField(max_length=32, null=False, index=True)
    status = CharField(max_length=1, null=False, default="1", index=True)

    class Meta:
        db_table = "team_member"
        indexes = ((("team_id", "user_id"), True),)
```

在 `Knowledgebase` 增加 `team_id`，并在 `migrate_db()` 中调用：

```python
alter_db_add_column(
    migrator,
    "knowledgebase",
    "team_id",
    CharField(max_length=32, null=True, index=True),
)
```

- [ ] **Step 4: 写迁移失败测试**

```python
def test_backfill_default_teams_is_idempotent(fake_team_db):
    first = backfill_default_teams()
    second = backfill_default_teams()

    assert first == {
        "teams_created": 1,
        "members_created": 2,
        "datasets_assigned": 1,
    }
    assert second == {
        "teams_created": 0,
        "members_created": 0,
        "datasets_assigned": 0,
    }
    assert fake_team_db.team_names == ["默认团队"]
    assert fake_team_db.member_states == ["active", "invited"]
    assert fake_team_db.dataset_team_id == fake_team_db.default_team_id
```

- [ ] **Step 5: 实现幂等回填**

`backfill_default_teams()` 必须在 `DB.atomic()` 中：

1. 查询拥有非 OWNER `UserTenant` 或未分配团队共享知识库的 Tenant ID；
2. 使用 `(tenant_id, "默认团队")` 查找或创建团队；
3. 把 `NORMAL` 映射到 `active`，把 `INVITE` 映射到 `invited`；
4. 仅更新 `permission="team" AND team_id IS NULL` 的知识库；
5. 捕获唯一键竞争后重新读取已存在记录；
6. 返回实际新增/更新计数。

`migrate_db()` 在新增 `knowledgebase.team_id` 后导入并调用 `backfill_default_teams()`。回填异常必须记录并重新抛出，不能在字段已创建但数据未回填时静默启动。

- [ ] **Step 6: 运行模型和迁移测试**

Run: `POLARS_SKIP_CPU_CHECK=1 PYTHONPATH=$(pwd) /home/qaadmin/ragflow/.venv/bin/pytest -q test/unit_test/api/db/test_team_models.py test/unit_test/api/db/test_team_migration.py`

Expected: PASS。

- [ ] **Step 7: 提交**

```bash
git add api/db/__init__.py api/db/db_models.py api/db/team_migration.py test/unit_test/api/db/test_team_models.py test/unit_test/api/db/test_team_migration.py
git commit -m "新增部门团队数据模型与兼容迁移"
```

---

### Task 2: 团队查询与集中授权服务

**Files:**
- Create: `api/db/services/team_service.py`
- Create: `test/unit_test/api/db/services/test_team_service.py`

**Interfaces:**
- Consumes: `Team`、`TeamMember`、`TeamMemberState`。
- Produces: `TeamService.normalize_name(name: str) -> str`。
- Produces: `TeamService.get_owned_team(team_id: str, admin_id: str) -> Team | None`。
- Produces: `TeamService.list_owned(admin_id: str) -> list[dict]`。
- Produces: `TeamMemberService.active_team_ids(user_id: str) -> list[str]`。
- Produces: `TeamMemberService.visible_owner_ids(user_id: str) -> list[str]`。
- Produces: `TeamAuthorizationService.can_manage_team(user_id: str, team_id: str) -> bool`。
- Produces: `TeamAuthorizationService.can_access_kb(user_id: str, kb: Knowledgebase) -> bool`。
- Produces: `TeamAuthorizationService.validate_assignment(user_id: str, kb: Knowledgebase, permission: str, team_id: str | None) -> tuple[bool, str | None]`。

- [ ] **Step 1: 写授权矩阵失败测试**

```python
@pytest.mark.parametrize(
    ("permission", "team_id", "active_team_ids", "expected"),
    [
        ("me", None, ["team-1"], False),
        ("team", "team-1", [], False),
        ("team", "team-1", ["team-1"], True),
    ],
)
def test_member_kb_access(permission, team_id, active_team_ids, expected, monkeypatch):
    kb = SimpleNamespace(
        tenant_id="owner-1",
        permission=permission,
        team_id=team_id,
        status=StatusEnum.VALID.value,
    )
    monkeypatch.setattr(TeamMemberService, "active_team_ids", lambda _user_id: active_team_ids)
    assert TeamAuthorizationService.can_access_kb("member-1", kb) is expected


def test_owner_always_accesses_own_kb():
    kb = SimpleNamespace(
        tenant_id="owner-1",
        permission="me",
        team_id=None,
        status=StatusEnum.VALID.value,
    )
    assert TeamAuthorizationService.can_access_kb("owner-1", kb) is True
```

- [ ] **Step 2: 写分配校验失败测试**

覆盖以下精确结果：

- 普通用户返回 `"System administrator permission is required."`；
- 管理员分配他人知识库返回 `"No authorization to assign this dataset."`；
- `permission="me"` 携带 `team_id` 返回 `"team_id must be empty when permission is me."`；
- `permission="team"` 缺少 `team_id` 返回 `"team_id is required when permission is team."`；
- 团队与知识库 Tenant 不一致返回 `"The team and dataset must have the same owner."`。

- [ ] **Step 3: 运行服务测试并确认失败**

Run: `POLARS_SKIP_CPU_CHECK=1 PYTHONPATH=$(pwd) /home/qaadmin/ragflow/.venv/bin/pytest -q test/unit_test/api/db/services/test_team_service.py`

Expected: FAIL，原因是团队服务尚不存在。

- [ ] **Step 4: 实现集中授权服务**

核心实现保持纯判断接口：

```python
class TeamAuthorizationService:
    @classmethod
    def can_access_kb(cls, user_id: str, kb: Knowledgebase) -> bool:
        if kb.status != StatusEnum.VALID.value:
            return False
        if kb.tenant_id == user_id:
            return True
        if kb.permission != TenantPermission.TEAM.value or not kb.team_id:
            return False
        return kb.team_id in set(TeamMemberService.active_team_ids(user_id))
```

`can_manage_team` 必须同时调用 `UserService.is_admin(user_id)` 并验证团队属于 `user_id`。`visible_owner_ids` 通过 active TeamMember 关联 Team，去重返回团队的 `tenant_id`，且不把邀请状态算入。

- [ ] **Step 5: 运行团队服务测试**

Run: `POLARS_SKIP_CPU_CHECK=1 PYTHONPATH=$(pwd) /home/qaadmin/ragflow/.venv/bin/pytest -q test/unit_test/api/db/services/test_team_service.py`

Expected: PASS。

- [ ] **Step 6: 提交**

```bash
git add api/db/services/team_service.py test/unit_test/api/db/services/test_team_service.py
git commit -m "实现部门团队授权服务"
```

---

### Task 3: 团队管理、邀请和成员 REST API

**Files:**
- Modify: `api/utils/validation_utils.py`
- Modify: `api/utils/email_templates.py`
- Modify: `api/utils/web_utils.py`
- Create: `api/apps/services/team_api_service.py`
- Create: `api/apps/restful_apis/team_api.py`
- Create: `test/unit_test/api/apps/services/test_team_api_service.py`
- Create: `test/unit_test/api/apps/restful_apis/test_team_api.py`

**Interfaces:**
- Consumes: Task 2 团队服务。
- Produces: `CreateTeamReq{name}`、`UpdateTeamReq{name}`、`InviteTeamMemberReq{email}`、`UpdateTeamInvitationReq{action}`。
- Produces: `create_team(admin_id, name)`、`rename_team(admin_id, team_id, name)`、`delete_team(admin_id, team_id)`。
- Produces: `invite_member(admin_id, team_id, email)`、`remove_or_leave_member(actor_id, team_id, user_id)`、`update_invitation(user_id, team_id, action)`。
- Produces: `/api/v1/teams`、`/api/v1/teams/{team_id}`、成员和邀请路由。

- [ ] **Step 1: 写应用服务失败测试**

```python
def test_delete_team_unassigns_datasets_atomically(monkeypatch):
    updates = []
    deletions = []
    monkeypatch.setattr(TeamAuthorizationService, "can_manage_team", lambda *_args: True)
    monkeypatch.setattr(
        KnowledgebaseService,
        "filter_update",
        lambda conditions, values: updates.append(values) or 2,
    )
    monkeypatch.setattr(TeamMemberService, "deactivate_by_team", lambda team_id: deletions.append(("members", team_id)))
    monkeypatch.setattr(TeamService, "deactivate", lambda team_id: deletions.append(("team", team_id)))

    ok, result = delete_team("admin-1", "team-1")

    assert ok is True
    assert result == {"unassigned_dataset_count": 2}
    assert updates == [{"permission": "me", "team_id": None}]
    assert deletions == [("members", "team-1"), ("team", "team-1")]
```

同时覆盖：非管理员、跨管理员团队、重复名称、未注册用户、重复邀请、接受/拒绝仅作用于当前用户、自助退出和管理员移除。

- [ ] **Step 2: 写路由失败测试**

通过与现有 `test_tenant_app_unit.py` 相同的动态模块加载方式测试：

```python
def test_create_team_rejects_non_admin(team_api_module, monkeypatch):
    monkeypatch.setattr(team_api_module.UserService, "is_admin", lambda _user_id: False)
    result = _run(team_api_module.create_team())
    assert result["code"] == team_api_module.RetCode.AUTHENTICATION_ERROR


def test_accept_invitation_uses_current_user(team_api_module, monkeypatch):
    seen = []
    monkeypatch.setattr(
        team_api_module.team_api_service,
        "update_invitation",
        lambda user_id, team_id, action: seen.append((user_id, team_id, action)) or (True, True),
    )
    result = _run(team_api_module.update_invitation("team-1"))
    assert result["code"] == 0
    assert seen == [("member-1", "team-1", "accept")]
```

- [ ] **Step 3: 运行团队 API 测试并确认失败**

Run: `POLARS_SKIP_CPU_CHECK=1 PYTHONPATH=$(pwd) /home/qaadmin/ragflow/.venv/bin/pytest -q test/unit_test/api/apps/services/test_team_api_service.py test/unit_test/api/apps/restful_apis/test_team_api.py`

Expected: FAIL，原因是应用服务和路由尚不存在。

- [ ] **Step 4: 实现请求模型和 REST 路由**

请求模型使用严格字面值：

```python
class CreateTeamReq(Base):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100), Field(...)]


class UpdateTeamReq(CreateTeamReq):
    pass


class InviteTeamMemberReq(Base):
    email: Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=255), Field(...)]


class UpdateTeamInvitationReq(Base):
    action: Literal["accept", "reject"]
```

所有团队治理路由从 `current_user.id` 取 actor，不接受客户端提交管理员 ID。错误响应统一使用 `No authorization.`，参数错误使用 validation 层的标准响应。

- [ ] **Step 5: 实现事务删除、邀请与邮件**

`delete_team()` 使用 `DB.atomic()`，严格按“解除知识库 → 失效成员 → 失效团队”执行。新增 `send_team_invite_email(to_email, invite_url, team_id, team_name, inviter)`，邮件正文显示团队名称和团队 ID，不再把部门团队称为 Tenant。

- [ ] **Step 6: 运行团队 API 测试**

Run: `POLARS_SKIP_CPU_CHECK=1 PYTHONPATH=$(pwd) /home/qaadmin/ragflow/.venv/bin/pytest -q test/unit_test/api/apps/services/test_team_api_service.py test/unit_test/api/apps/restful_apis/test_team_api.py`

Expected: PASS。

- [ ] **Step 7: 提交**

```bash
git add api/utils/validation_utils.py api/utils/email_templates.py api/utils/web_utils.py api/apps/services/team_api_service.py api/apps/restful_apis/team_api.py test/unit_test/api/apps/services/test_team_api_service.py test/unit_test/api/apps/restful_apis/test_team_api.py
git commit -m "新增部门团队管理接口"
```

---

### Task 4: 知识库团队分配与可见性

**Files:**
- Modify: `api/utils/validation_utils.py`
- Modify: `api/db/services/knowledgebase_service.py`
- Modify: `api/db/services/knowledgebase_category_service.py`
- Modify: `api/apps/services/dataset_api_service.py`
- Modify: `api/common/check_team_permission.py`
- Modify: `test/unit_test/api/db/services/test_dataset_access_permissions.py`
- Create: `test/unit_test/api/apps/services/test_dataset_team_assignment.py`

**Interfaces:**
- Consumes: `TeamAuthorizationService`、`TeamMemberService.active_team_ids()`。
- Changes: `KnowledgebaseService._visibility_and_status_filter(team_ids, user_id)`。
- Changes: `KnowledgebaseService.accessible(kb_id, user_id)`。
- Produces: `validate_team_assignment(user_id, kb, permission, team_id)` 在 create/update 时被调用。
- Extends: `CreateDatasetReq.team_id: str | None`、`IDataset` 响应中的 `team_id` 与 `team_name`。

- [ ] **Step 1: 把现有知识库权限测试改写成新团队语义**

```python
def test_team_dataset_is_accessible_to_active_team_member(monkeypatch):
    kb = SimpleNamespace(
        id="kb-team",
        tenant_id="owner-1",
        permission=TenantPermission.TEAM.value,
        team_id="team-1",
        status=StatusEnum.VALID.value,
    )
    monkeypatch.setattr(KnowledgebaseService, "get_by_id", classmethod(lambda cls, _kb_id: (True, kb)))
    monkeypatch.setattr(TeamMemberService, "active_team_ids", lambda _user_id: ["team-1"])
    assert _unwrapped_kb_accessible()(KnowledgebaseService, "kb-team", "member-2") is True


def test_team_dataset_rejects_member_of_different_team(monkeypatch):
    kb = SimpleNamespace(
        id="kb-team",
        tenant_id="owner-1",
        permission="team",
        team_id="team-1",
        status=StatusEnum.VALID.value,
    )
    monkeypatch.setattr(KnowledgebaseService, "get_by_id", classmethod(lambda cls, _kb_id: (True, kb)))
    monkeypatch.setattr(TeamMemberService, "active_team_ids", lambda _user_id: ["team-2"])
    assert _unwrapped_kb_accessible()(KnowledgebaseService, "kb-team", "member-2") is False
```

- [ ] **Step 2: 写创建/更新分配失败测试**

覆盖以下场景：

- 管理员创建 `permission=team` 且团队属于自己：成功并保存 `team_id`；
- 普通用户创建或更新 `permission=team`：拒绝；
- 管理员更新他人知识库：拒绝；
- 切换到 `permission=me`：保存 `team_id=None`；
- 切换团队：旧团队成员失去访问，新团队成员获得访问；
- 响应包含 `team_name`。

- [ ] **Step 3: 运行权限与分配测试并确认失败**

Run: `POLARS_SKIP_CPU_CHECK=1 PYTHONPATH=$(pwd) /home/qaadmin/ragflow/.venv/bin/pytest -q test/unit_test/api/db/services/test_dataset_access_permissions.py test/unit_test/api/apps/services/test_dataset_team_assignment.py`

Expected: FAIL，现有代码仍按 joined Tenant 判断权限且不认识 `team_id`。

- [ ] **Step 4: 实现团队 ID 可见性过滤**

```python
@classmethod
def _visibility_and_status_filter(cls, active_team_ids, user_id):
    return (
        (
            (
                cls.model.team_id.in_(active_team_ids)
                & (cls.model.permission == TenantPermission.TEAM.value)
            )
            | (cls.model.tenant_id == user_id)
        )
        & (cls.model.status == StatusEnum.VALID.value)
    )
```

`accessible()` 和 `check_kb_team_permission()` 委托 `TeamAuthorizationService.can_access_kb()`。`dataset_api_service.list_datasets()` 使用 `TeamMemberService.active_team_ids(tenant_id)`，不再用 joined Tenant ID 作为知识库可见性条件。

- [ ] **Step 5: 实现知识库分配校验和响应字段**

`CreateDatasetReq` 新增字段及 UUID1 规范化校验：

```python
team_id: Annotated[str | None, Field(default=None)]

@field_validator("team_id", mode="before")
@classmethod
def validate_team_id(cls, value: Any) -> str | None:
    if value is None:
        return None
    return validate_uuid1_hex(value)
```

在 `create_dataset()` 和 `update_dataset()` 写库前调用集中授权服务。更新为 `permission="me"` 时必须显式写入 `team_id=None`。列表和详情批量读取有效 Team 名称，并返回 `team_name`；无团队时返回 `team_name=None`。

- [ ] **Step 6: 调整知识库分类统计**

`KnowledgebaseCategoryService` 用 active team 对应的 owner Tenant 作为可展示分类范围，并用新的 `_visibility_and_status_filter(active_team_ids, user_id)` 统计数量，确保分类数量只包含当前用户可见知识库。

- [ ] **Step 7: 运行知识库权限测试**

Run: `POLARS_SKIP_CPU_CHECK=1 PYTHONPATH=$(pwd) /home/qaadmin/ragflow/.venv/bin/pytest -q test/unit_test/api/db/services/test_dataset_access_permissions.py test/unit_test/api/apps/services/test_dataset_team_assignment.py test/unit_test/api/db/services/test_knowledgebase_category_service.py`

Expected: PASS。

- [ ] **Step 8: 提交**

```bash
git add api/utils/validation_utils.py api/db/services/knowledgebase_service.py api/db/services/knowledgebase_category_service.py api/apps/services/dataset_api_service.py api/common/check_team_permission.py test/unit_test/api/db/services/test_dataset_access_permissions.py test/unit_test/api/apps/services/test_dataset_team_assignment.py
git commit -m "按部门团队控制知识库访问与分配"
```

---

### Task 5: 内容维护接口与租户上下文回归

**Files:**
- Modify: `test/unit_test/api/apps/restful_apis/test_parse_tenant_context.py`
- Create: `test/unit_test/api/apps/restful_apis/test_team_kb_content_permissions.py`
- Modify: `api/apps/restful_apis/document_api.py`
- Modify: `api/apps/restful_apis/chunk_api.py`
- Modify: `api/apps/restful_apis/knowledge_file_api.py`
- Modify: `api/apps/restful_apis/file2document_api.py`
- Modify: `api/db/services/file_service.py`
- Modify: `api/apps/services/knowledge_file_service.py`

**Interfaces:**
- Consumes: `check_kb_team_permission()` 与 `KnowledgebaseService.accessible()` 的新语义。
- Guarantees: 团队成员内容操作使用 `Knowledgebase.tenant_id` 作为存储和索引租户。
- Guarantees: 审计字段使用当前操作者 ID，不把操作者误当成存储租户。
- Guarantees: `KnowledgebaseService.accessible4deletion()` 和 owner-only update 路径不放宽。

- [ ] **Step 1: 写内容权限矩阵测试**

使用一个 owner、两个团队、active/invited/removed 四类成员，覆盖：

```python
@pytest.mark.parametrize(
    "operation",
    ["upload", "delete_document", "parse", "stop_parse", "edit_chunk", "delete_chunk"],
)
def test_active_member_can_maintain_assigned_team_dataset(operation, permission_harness):
    result = permission_harness.call(operation, user_id="member-1", kb_id="kb-hr")
    assert result["code"] == 0


@pytest.mark.parametrize("state", ["invited", "removed", "different-team"])
def test_non_active_or_other_team_member_is_rejected(state, permission_harness):
    result = permission_harness.call("upload", user_id=state, kb_id="kb-hr")
    assert result["code"] != 0
```

另写 owner-only 用例，确认团队成员不能更新知识库核心配置或删除知识库。

- [ ] **Step 2: 扩展解析租户上下文测试**

现有四项解析测试增加 `team_id="team-hr"` 和 active membership 模拟，并继续断言：

```python
assert queued_tenants == ["owner-1"]
assert deleted == [({"doc_id": "doc-1"}, "idx-owner-1", "kb-1")]
```

- [ ] **Step 3: 运行内容权限测试并观察真实缺口**

Run: `POLARS_SKIP_CPU_CHECK=1 PYTHONPATH=$(pwd) /home/qaadmin/ragflow/.venv/bin/pytest -q test/unit_test/api/apps/restful_apis/test_team_kb_content_permissions.py test/unit_test/api/apps/restful_apis/test_parse_tenant_context.py`

Expected: FAIL；现有上传、目录或删除路径仍把当前操作者 ID 传给所有者租户参数，团队成员场景会访问错误的根目录、配额或文件空间。

- [ ] **Step 4: 分离操作者身份和知识库所有者租户**

所有内容路由先取得知识库并统一授权：

```python
ok, kb = KnowledgebaseService.get_by_id(dataset_id)
if not ok or not check_kb_team_permission(kb, current_user.id):
    return get_json_result(data=False, message="No authorization.", code=RetCode.AUTHENTICATION_ERROR)
owner_tenant_id = kb.tenant_id
actor_id = current_user.id
```

然后执行以下确定性修改：

1. `document_api.py` 的上传、删除、解析、停止解析和重新解析，把根目录、文件空间、配额、模型和索引调用的租户参数统一改为 `owner_tenant_id`；`Document.created_by` 继续写 `actor_id`。
2. `file_service.py` 把 `upload_document(kb, file_objs, user_id, ...)` 拆成 `upload_document(kb, file_objs, owner_tenant_id, *, created_by, ...)`；根目录、健康检查、文件记录和存储使用 `owner_tenant_id`，文档审计字段使用 `created_by`。
3. `knowledge_file_api.py` 授权后把 `kb.tenant_id` 传给 `KnowledgeFileService`；`knowledge_file_service.py` 将参数改名为 `owner_tenant_id` 并禁止用调用者 ID 查询根目录或校验文件所属租户。
4. `chunk_api.py` 复用一个返回 `kb` 与 `owner_tenant_id` 的授权 helper，所有向量索引和模型调用只使用 `owner_tenant_id`。
5. `file2document_api.py` 分别校验源文件权限和目标知识库权限；目标文档的知识库资源上下文取目标 `kb.tenant_id`，`created_by` 记录 `actor_id`。

删除整个知识库和核心配置更新继续使用 owner-only 查询，不得改成 `accessible()`。

- [ ] **Step 5: 运行内容和解析回归测试**

Run: `POLARS_SKIP_CPU_CHECK=1 PYTHONPATH=$(pwd) /home/qaadmin/ragflow/.venv/bin/pytest -q test/unit_test/api/apps/restful_apis/test_team_kb_content_permissions.py test/unit_test/api/apps/restful_apis/test_parse_tenant_context.py test/unit_test/api/apps/restful_apis/test_document_upload_parent.py`

Expected: PASS。

- [ ] **Step 6: 提交**

```bash
git add api/apps/restful_apis/document_api.py api/apps/restful_apis/chunk_api.py api/apps/restful_apis/knowledge_file_api.py api/apps/restful_apis/file2document_api.py api/db/services/file_service.py api/apps/services/knowledge_file_service.py test/unit_test/api/apps/restful_apis/test_team_kb_content_permissions.py test/unit_test/api/apps/restful_apis/test_parse_tenant_context.py
git commit -m "统一团队成员知识库内容维护权限"
```

---

### Task 6: 前端团队 API、类型和查询状态

**Files:**
- Modify: `web/src/interfaces/database/user-setting.ts`
- Modify: `web/src/utils/api.ts`
- Create: `web/src/services/team-service.ts`
- Create: `web/src/hooks/use-team-request.ts`
- Create: `web/src/hooks/__tests__/use-team-request.test.tsx`

**Interfaces:**
- Produces: `ITeam`、`ITeamMember`、`ITeamListResponse`。
- Produces: `listTeams()`、`createTeam(name)`、`renameTeam(teamId, name)`、`deleteTeam(teamId)`。
- Produces: `listTeamMembers(teamId)`、`inviteTeamMember(teamId, email)`、`removeTeamMember(teamId, userId)`、`updateTeamInvitation(teamId, action)`。
- Produces: `useTeams()`、`useTeamMembers(teamId)` 和对应 mutations。

- [ ] **Step 1: 写 React Query 失败测试**

```tsx
it('invalidates teams after creating a team', async () => {
  mockCreateTeam.mockResolvedValue({ data: { code: 0, data: { id: 'team-1' } } });
  const { result } = renderHook(() => useCreateTeam(), { wrapper });

  await act(() => result.current.createTeam('HR 团队'));

  expect(invalidateQueries).toHaveBeenCalledWith({ queryKey: ['teams'] });
});

it('invalidates members and teams after invitation acceptance', async () => {
  const { result } = renderHook(() => useUpdateTeamInvitation(), { wrapper });
  await act(() => result.current.updateInvitation({ teamId: 'team-1', action: 'accept' }));
  expect(invalidateQueries).toHaveBeenCalledWith({ queryKey: ['teams'] });
});
```

- [ ] **Step 2: 运行前端 hook 测试并确认失败**

Run in a Node-enabled environment: `cd web && npm test -- --runInBand src/hooks/__tests__/use-team-request.test.tsx`

Expected: FAIL，团队客户端和 hooks 尚不存在。当前服务器若仍无 Node/npm，记录 `BLOCKED: npx/npm not found` 并继续静态实现，不伪造结果。

- [ ] **Step 3: 实现类型和 API 客户端**

```typescript
export type TeamMemberState = 'invited' | 'active';

export interface ITeam {
  id: string;
  tenant_id: string;
  name: string;
  member_count: number;
  dataset_count: number;
  created_by: string;
}

export interface ITeamMember {
  team_id: string;
  user_id: string;
  state: TeamMemberState;
  email: string;
  nickname: string;
  avatar?: string;
}
```

`team-service.ts` 使用 `request.get/post/patch/delete`，URL 只从 `api.ts` 生成，不在组件内拼接。

- [ ] **Step 4: 实现查询键和 mutation 失效策略**

统一键：

```typescript
export const teamKeys = {
  all: ['teams'] as const,
  members: (teamId: string) => ['teams', teamId, 'members'] as const,
};
```

创建、重命名、删除、接受、拒绝、退出后失效 `teamKeys.all`；邀请和移除后同时失效 `teamKeys.all` 与对应成员键。

- [ ] **Step 5: 运行可用的前端检查**

Run in a Node-enabled environment: `cd web && npm test -- --runInBand src/hooks/__tests__/use-team-request.test.tsx`

Expected: PASS。若当前环境无 Node/npm，执行 `git diff --check` 和人工 TypeScript 接口核对，并把完整前端测试留到 Task 9。

- [ ] **Step 6: 提交**

```bash
git add web/src/interfaces/database/user-setting.ts web/src/utils/api.ts web/src/services/team-service.ts web/src/hooks/use-team-request.ts web/src/hooks/__tests__/use-team-request.test.tsx
git commit -m "新增前端部门团队数据层"
```

---

### Task 7: 多团队管理页面

**Files:**
- Modify: `web/src/pages/user-setting/setting-team/index.tsx`
- Replace: `web/src/pages/user-setting/setting-team/user-table.tsx`
- Replace: `web/src/pages/user-setting/setting-team/tenant-table.tsx`
- Modify: `web/src/pages/user-setting/setting-team/add-user-modal.tsx`
- Modify: `web/src/pages/user-setting/setting-team/hooks.ts`
- Create: `web/src/pages/user-setting/setting-team/team-list.tsx`
- Create: `web/src/pages/user-setting/setting-team/team-dialog.tsx`
- Create: `web/src/pages/user-setting/setting-team/__tests__/team-page.test.tsx`
- Modify: `web/src/locales/en.ts`
- Modify: `web/src/locales/zh.ts`

**Interfaces:**
- Consumes: Task 6 hooks 与 `IUserInfo.is_superuser`。
- Produces: 管理员团队列表、选中团队成员表、创建/重命名/删除、邀请/移除 UI。
- Produces: 普通用户加入团队、邀请接受/拒绝、自助退出 UI。

- [ ] **Step 1: 写页面权限失败测试**

```tsx
it('shows team governance controls only to superusers', () => {
  mockUser.mockReturnValue({ is_superuser: true });
  render(<UserSettingTeam />);
  expect(screen.getByRole('button', { name: '创建团队' })).toBeInTheDocument();

  cleanup();
  mockUser.mockReturnValue({ is_superuser: false });
  render(<UserSettingTeam />);
  expect(screen.queryByRole('button', { name: '创建团队' })).not.toBeInTheDocument();
});

it('loads members for the selected owned team', async () => {
  render(<UserSettingTeam />);
  await userEvent.click(screen.getByText('HR 团队'));
  expect(mockUseTeamMembers).toHaveBeenCalledWith('team-hr');
});
```

- [ ] **Step 2: 运行页面测试并确认失败**

Run in a Node-enabled environment: `cd web && npm test -- --runInBand src/pages/user-setting/setting-team/__tests__/team-page.test.tsx`

Expected: FAIL，旧页面仍绑定 Tenant hooks。

- [ ] **Step 3: 实现管理员团队管理视图**

页面状态只保存 `selectedTeamId` 和对话框状态；服务数据来自 React Query。管理员区包含：

- 团队名称；
- `member_count`；
- `dataset_count`；
- 重命名和删除操作；
- 选中团队后的邀请按钮与成员表。

删除确认文案明确说明“知识库不会删除，将自动变为只有我”。

- [ ] **Step 4: 实现加入团队和邀请视图**

按 `state` 渲染：

- `invited`：接受、拒绝；
- `active`：退出；
- 不显示团队管理操作。

成员表移除旧的 `console.log('sortedData', ...)`。

- [ ] **Step 5: 补充中英文文案**

新增键必须包括：`createTeam`、`renameTeam`、`deleteTeam`、`ownedTeams`、`joinedTeams`、`teamName`、`memberCount`、`datasetCount`、`deleteTeamWarning`、`specifiedTeam`、`selectTeam`。

- [ ] **Step 6: 运行页面测试**

Run in a Node-enabled environment: `cd web && npm test -- --runInBand src/pages/user-setting/setting-team/__tests__/team-page.test.tsx`

Expected: PASS。

- [ ] **Step 7: 提交**

```bash
git add web/src/pages/user-setting/setting-team web/src/locales/en.ts web/src/locales/zh.ts
git commit -m "实现多部门团队管理页面"
```

---

### Task 8: 知识库指定团队 UI 与卡片标签

**Files:**
- Modify: `web/src/interfaces/database/dataset.ts`
- Modify: `web/src/hooks/use-knowledge-request.ts`
- Modify: `web/src/pages/dataset/dataset-setting/permission-form-field.tsx`
- Modify: `web/src/pages/dataset/dataset-setting/form-schema.ts`
- Modify: `web/src/pages/dataset/dataset-setting/saving-button.tsx`
- Modify: `web/src/pages/dataset/dataset-setting/index.tsx`
- Modify: `web/src/pages/datasets/dataset-creating-dialog.tsx`
- Modify: `web/src/pages/datasets/dataset-card.tsx`
- Create: `web/src/pages/dataset/dataset-setting/__tests__/permission-form-field.test.tsx`
- Modify: `web/src/pages/datasets/__tests__/dataset-card.test.tsx`

**Interfaces:**
- Consumes: `useTeams()` 的管理员 owned teams。
- Extends: `IDataset.team_id?: string | null`、`IDataset.team_name?: string | null`。
- Changes: 知识库 create/update payload 包含规范化的 `permission` 与 `team_id`。

- [ ] **Step 1: 写权限表单失败测试**

```tsx
it('requires a team when permission is team', async () => {
  render(<PermissionFormHarness defaultValues={{ permission: 'team', team_id: null }} />);
  await userEvent.click(screen.getByRole('button', { name: '保存' }));
  expect(await screen.findByText('请选择团队')).toBeInTheDocument();
});

it('clears team_id when switching to me', async () => {
  const form = renderPermissionForm({ permission: 'team', team_id: 'team-hr' });
  await form.selectPermission('me');
  expect(form.getValues()).toMatchObject({ permission: 'me', team_id: null });
});
```

- [ ] **Step 2: 扩展卡片失败测试**

```tsx
it('shows the assigned team badge', () => {
  render(<DatasetCard dataset={{ id: 'kb-1', document_count: 2, team_name: 'HR 团队' } as IDataset} showDatasetRenameModal={jest.fn()} />);
  expect(screen.getByText('HR 团队')).toBeInTheDocument();
});
```

- [ ] **Step 3: 运行 UI 测试并确认失败**

Run in a Node-enabled environment: `cd web && npm test -- --runInBand src/pages/dataset/dataset-setting/__tests__/permission-form-field.test.tsx src/pages/datasets/__tests__/dataset-card.test.tsx`

Expected: FAIL，表单和类型尚无 `team_id`。

- [ ] **Step 4: 实现联动表单和 Zod 不变量**

```typescript
export const permissionSchema = z
  .object({
    permission: z.enum(['me', 'team']),
    team_id: z.string().nullable().optional(),
  })
  .superRefine((value, ctx) => {
    if (value.permission === 'team' && !value.team_id) {
      ctx.addIssue({ code: z.ZodIssueCode.custom, path: ['team_id'], message: t('knowledgeConfiguration.selectTeam') });
    }
  });
```

`PermissionFormField` 观察 `permission`：选择 team 时渲染 owned team 下拉框，选择 me 时执行 `setValue('team_id', null, { shouldDirty: true })`。普通用户不显示可执行团队分配的选项。

- [ ] **Step 5: 让创建和更新提交 team_id**

`GeneralSavingButton` 与完整保存按钮都从表单读取 `team_id`。`dataset-creating-dialog.tsx` 增加权限和团队字段，并复用相同规范化函数：

```typescript
const normalizeTeamPermission = (permission: 'me' | 'team', teamId?: string | null) => ({
  permission,
  team_id: permission === 'team' ? teamId : null,
});
```

- [ ] **Step 6: 展示团队标签**

`DatasetCard` 在 `team_name` 非空时渲染团队 badge；原 owner badge 保留，用于区分知识库所有者。

- [ ] **Step 7: 运行知识库 UI 测试**

Run in a Node-enabled environment: `cd web && npm test -- --runInBand src/pages/dataset/dataset-setting/__tests__/permission-form-field.test.tsx src/pages/datasets/__tests__/dataset-card.test.tsx`

Expected: PASS。

- [ ] **Step 8: 提交**

```bash
git add web/src/interfaces/database/dataset.ts web/src/hooks/use-knowledge-request.ts web/src/pages/dataset/dataset-setting web/src/pages/datasets/dataset-creating-dialog.tsx web/src/pages/datasets/dataset-card.tsx web/src/pages/datasets/__tests__/dataset-card.test.tsx
git commit -m "支持知识库指定部门团队"
```

---

### Task 9: 全链路验收、静态检查和发布说明

**Files:**
- Create: `test/unit_test/api/apps/restful_apis/test_multi_team_acceptance.py`

**Interfaces:**
- Verifies all prior task interfaces together。
- Produces no new runtime API。

- [ ] **Step 1: 写跨团队验收测试**

测试固定场景：

```python
def test_hr_and_sales_members_only_see_their_assigned_datasets(multi_team_harness):
    multi_team_harness.create_team("team-hr", "HR 团队", owner="admin-1")
    multi_team_harness.create_team("team-sales", "销售团队", owner="admin-1")
    multi_team_harness.add_active_member("team-hr", "alice")
    multi_team_harness.add_active_member("team-sales", "bob")
    multi_team_harness.assign_dataset("kb-hr", "team-hr", owner="admin-1")
    multi_team_harness.assign_dataset("kb-sales", "team-sales", owner="admin-1")

    assert multi_team_harness.visible_dataset_ids("alice") == {"kb-hr"}
    assert multi_team_harness.visible_dataset_ids("bob") == {"kb-sales"}
    assert multi_team_harness.visible_dataset_ids("admin-1") == {"kb-hr", "kb-sales"}
```

同时验证删除 HR 团队后 `kb-hr.permission == "me"`、`kb-hr.team_id is None`、文档数和索引租户不变。

- [ ] **Step 2: 运行后端定向测试集**

Run:

```bash
POLARS_SKIP_CPU_CHECK=1 PYTHONPATH=$(pwd) /home/qaadmin/ragflow/.venv/bin/pytest -q \
  test/unit_test/api/db/test_team_models.py \
  test/unit_test/api/db/test_team_migration.py \
  test/unit_test/api/db/services/test_team_service.py \
  test/unit_test/api/apps/services/test_team_api_service.py \
  test/unit_test/api/apps/restful_apis/test_team_api.py \
  test/unit_test/api/db/services/test_dataset_access_permissions.py \
  test/unit_test/api/apps/services/test_dataset_team_assignment.py \
  test/unit_test/api/apps/restful_apis/test_team_kb_content_permissions.py \
  test/unit_test/api/apps/restful_apis/test_parse_tenant_context.py \
  test/unit_test/api/apps/restful_apis/test_multi_team_acceptance.py
```

Expected: PASS。

- [ ] **Step 3: 运行后端静态检查**

Run:

```bash
uvx ruff check --select F \
  api/db/__init__.py api/db/db_models.py api/db/team_migration.py \
  api/db/services/team_service.py api/apps/services/team_api_service.py \
  api/apps/restful_apis/team_api.py api/db/services/knowledgebase_service.py \
  api/db/services/knowledgebase_category_service.py api/apps/services/dataset_api_service.py \
  api/common/check_team_permission.py
```

Expected: `All checks passed!`

Run: `git diff --check`

Expected: 无输出，退出码 0。

- [ ] **Step 4: 运行完整后端单元测试**

Run: `POLARS_SKIP_CPU_CHECK=1 PYTHONPATH=$(pwd) /home/qaadmin/ragflow/.venv/bin/pytest -q test/unit_test --tb=short`

Expected: 0 failed。基线为 2081 passed、27 skipped；新增测试会提高 collected/passed 数量。

- [ ] **Step 5: 运行前端测试与 lint**

Run in a Node-enabled environment:

```bash
cd web
npm test -- --runInBand \
  src/hooks/__tests__/use-team-request.test.tsx \
  src/pages/user-setting/setting-team/__tests__/team-page.test.tsx \
  src/pages/dataset/dataset-setting/__tests__/permission-form-field.test.tsx \
  src/pages/datasets/__tests__/dataset-card.test.tsx
npm run lint
```

Expected: 所有测试和 lint 通过。当前服务器若仍无 Node/npm，最终报告必须列出未执行项，并在部署前移交到 Node-enabled CI。

- [ ] **Step 6: 检查提交与主分支隔离**

Run:

```bash
git status --short
git log --oneline main..feature/multi-kb-teams
git -C /home/qaadmin/ragflow status --short
```

Expected: 功能工作树干净；所有功能提交只存在于 `feature/multi-kb-teams`；主工作区 `main` 无未提交改动。

- [ ] **Step 7: 提交验收测试**

```bash
git add test/unit_test/api/apps/restful_apis/test_multi_team_acceptance.py
git commit -m "补充多部门团队全链路验收"
```

- [ ] **Step 8: 发布前人工验收**

在测试环境执行数据库备份和迁移后：

1. 使用 `admin@example.com` 创建 HR、销售、医学团队；
2. 邀请三个已注册测试账号并分别接受邀请；
3. 把管理员自己的三个测试知识库分别分配给三个团队；
4. 验证每个成员只看到自己的团队知识库；
5. 验证成员可上传、删除、解析文档和维护切片；
6. 验证成员不能修改核心配置、团队分配或删除知识库；
7. 删除医学团队，确认医学知识库保留并变为“只有我”；
8. 调用 `/api/v1/system/healthz`，确认 HTTP 200 且所有依赖状态为 `ok`。
