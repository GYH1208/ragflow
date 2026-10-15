# WorkBuddy 员工账号与中心授权 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** 本企业员工用 WorkBuddy 自建应用免授权连接 MCP，自动创建独立来源账号，按主部门所在中心检索超管授予的知识库。

**Architecture:** MCP 原样转发个人 OneID 票据，Quart 后端完成在线身份验证、企业准入、事务建号及中心授权。账号身份与知识库原有数据租户分开，现有本地用户及 API Key 分支保留。账号、组织和授权共享一致的数据契约，按下列依赖顺序作为一个集成计划实施。

**Tech Stack:** Python 3.13、Quart/Flask、Peewee/MySQL 或 PostgreSQL、httpx、MCP Streamable HTTP、React/TypeScript/Vite、pytest、Jest。

**Spec:** `docs/superpowers/specs/2026-10-15-workbuddy-employee-center-access-design.md`（已确认）。

## Global Constraints

- 仅配置企业员工可认证建号；其他企业不能建号或检索。
- 生产 MCP 9382 仍使用原来的 API Key 机制；9395 是独立诊断入口。
- WorkBuddy 自动建号不获得超管权限，不创建可用密码，不发放网页登录凭据。
- 身份唯一键 `(enterprise_id, union_id)`；不按姓名或邮箱合并本地账号。
- 首期按员工主部门所属中心计算权限，不合并多个所属中心的权限。
- 授权范围仅为 `knowledge:retrieve`；清空清单表示撤销全部授权。
- 后勤服务中心 `1494587270954266260` 仅初始化中心定义，不自动授予任何库。
- 企业 `1494560257098951656`；目标应用 `i-f17b2d9e2f7447daaa380b5670b59aed`；请求客户端 allowlist 当前仅 `codebuddy`。
- `client_id=codebuddy` 与目标 `aud` 分别验证，不要求相等；不得放宽目标 aud、issuer、tid 或时间校验。
- 账号正常启用兼容 `Enable` 和 `Enabled`，不把这个状态宣称为独立的人事在职证明。
- 原始令牌、应用 Secret、完整原始 UserInfo/introspection 响应均不入库、不入日志。
- 外部请求超时 5 秒、响应上限 256 KiB、不跟随重定向；Discovery 缓存最多 10 分钟，应用 token 缓存最多 5 分钟且提前 30 秒失效。
- 首期 WorkBuddy 模式不缓存允许库清单、员工状态或内容元数据。
- 新管理入口不挂到 `IS_ENTERPRISE` 商业版开关下。
- 第一次真实检索只用确认唯一 ID 后的“研发结构案例库”。

## Review Focus

1. 重复票据头及错误 OneID 票据混入有效 Cookie/旧 Key：必须拒绝，不能通过旧身份绕过（Task 6、9）。
2. 同名部门、缺失父节点、根节点省略 parent、重复主部门：保留 ID 层级，无法完整确定唯一中心就无授权（Task 2、4）。
3. 并发首登或管理员停用恰逢资料同步：只保留一个员工账号，本地停用不能被恢复（Task 3、6）。
4. 授权不同所有者的库及跨库拼接文档/chunk/图片：只读取获准对象，索引和模型取原 KB 租户（Task 7、8）。
5. 撤权或调部门后携带相同票据再次调用：下一次请求按新状态授权，不能由 MCP 缓存返回旧资料（Task 6、9、11）。

## 文件责任与公共契约

| 文件 | 责任 |
| --- | --- |
| `api/db/db_models.py`、新增 `api/db/workbuddy_migration.py` | 来源字段、三个关系模型、幂等迁移与中心初始化 |
| 新增 `api/auth/workbuddy/{__init__,types,config,oneid_client}.py` | 规范化身份、配置和固定端点 OneID 访问；不依赖 MCP 原型目录 |
| 新增 `api/db/services/workbuddy_employee_service.py` | JIT 建号、资料同步、员工列表与本地状态管理 |
| 新增 `api/db/services/center_dataset_service.py` | 组织同步、中心定义、完整授权清单和审计字段 |
| 新增 `api/apps/workbuddy_auth.py` | 组合身份、账号和授权，生成每请求访问上下文 |
| 新增 `api/apps/services/workbuddy_read_service.py` | 授权对象及真实数据所有者解析 |
| `api/apps/__init__.py`、`api_key_auth.py` | 显式只读身份分支、统一库范围检查 |
| `api/apps/restful_apis/{dataset,document,chunk}_api.py`、`api/apps/services/dataset_api_service.py` | 只读路由接入，原 API Key/Web 行为回归 |
| 新增 `admin/server/workbuddy_service.py`、`admin/server/routes.py`、`services.py` | 超管管理 API、旧操作的员工来源保护 |
| `mcp/server/server.py` | 独立 WorkBuddy 凭据模式、四工具转发和禁用缓存 |
| `web/src/pages/admin/{users,user-detail}.tsx`、新增 `workbuddy-employees.tsx`、`center-access.tsx` | 员工页签、详情、中心授权页面 |
| `web/src/services/{admin-service.ts,admin.service.d.ts}`、`web/src/routes.tsx`、admin 导航、`locales/{zh,en}.ts` | 类型、请求、路由和文案 |
| 新增 `docs/guides/workbuddy-center-access.md` | 配置、迁移、真实验收与回滚操作说明 |

以下类型在 Task 2 定义，后续保持同名同义：

```python
DepartmentNode(id: str, name: str, parent_id: str | None)
DepartmentPath(department_id: str, is_main: bool, complete: bool,
               hierarchy: tuple[DepartmentNode, ...])  # 根到叶，保留相同名称
VerifiedEmployee(enterprise_id: str, union_id: str, issuer: str,
                 application_id: str, alias_id: str | None, name: str,
                 directory_status: str, paths: tuple[DepartmentPath, ...])
WorkBuddyAccessContext(actor_user_id: str, enterprise_id: str,
                      center_id: str | None, allowed_dataset_ids: frozenset[str],
                      scope: str = "knowledge:retrieve")
```

均为不可变 dataclass；不包含原始 token 或 Secret。中心上下文必须由当前数据库中心定义计算，不能采用身份记录里的展示快照作为授权依据。

## 执行约定

- 实施开始读取 `superpowers:using-git-worktrees`，创建隔离工作区；保留当前未提交文档，不移动或覆盖用户改动。
- 每个任务先写失败测试，再实现、验证、单独提交。测试调用真实服务或请求边界，外部 OneID/模型/存储用受控替身；不能只断言源码包含某字符串。
- 下列 Python 命令在隔离仓库根目录运行，`PYTHONPATH=.`，使用本项目 `.venv/bin/python -m pytest`；新建 worktree 时连接可用虚拟环境，不向生产运行目录安装依赖。
- 先核对 Python、数据库类型及 Node 工具链。当前 shell 无 `node/npx`，提交钩子需要 `npx lint-staged`；恢复可用工具链后再提交，不禁用钩子。Vite 7 使用满足其 engine 的 Node 22.12+。
- 每步 Run 命令写在对应任务；红灯需是新增行为失败，不能把缺少环境依赖当作完成了红灯验证。已有基线失败单独记录，遇到影响本任务的失败再修。

### Task 1: 账号来源、关系模型与幂等迁移

**Files:** 修改 `api/db/db_models.py`；新增 `api/db/workbuddy_migration.py`、`test/unit_test/api/db/test_workbuddy_migration.py`、`test/unit_test/api/db/test_workbuddy_models.py`。

**Interfaces:** 产出 `migrate_workbuddy_schema(migrator) -> None`，由 `migrate_db()` 调用；产出 User.account_source、WorkBuddyEmployeeIdentity、EnterpriseDepartment、CenterDatasetGrant。

- [x] **1. 写失败测试。** 用临时 Peewee 数据库创建旧结构及旧 User/KB 记录；`test_migration_is_idempotent_and_preserves_existing_data` 连续执行两次，断言来源均为 local、KB tenant_id/content/permission/team_id 未变，默认 grant 数量为 0。`test_external_identity_and_grant_keys_are_unique` 断言重复身份、重复 user_id 映射、重复中心库组合抛 IntegrityError。
- [x] **2. 验证红灯。** Run: `.venv/bin/python -m pytest test/unit_test/api/db/test_workbuddy_migration.py test/unit_test/api/db/test_workbuddy_models.py -q`；Expected: 新增模型/迁移尚不存在而失败。
- [x] **3. 实现模型和迁移。** User.account_source 默认 local、非空；身份记录存规范化资料和同步时间；部门唯一 enterprise_id+department_id；grant 唯一 enterprise_id+center_department_id+dataset_id，固定 scope、enabled、created_by/updated_by 与时间。部门中心标记、本地启用变更同样记录操作者和时间。新增表沿用 init_database_tables 模型发现机制，加字段用实际数据库 migrator；不要捕获并忽略真实建表/唯一索引错误。只为配置企业初始化核实的中心，无 grant。仅增量迁移，不执行原型 SQLite 导入。
- [x] **4. 验证绿灯。** 同一测试命令全部通过；增加旧结构第二次运行及数据库异常不能被吞的断言。生产数据库方言迁移演练放 Task 11，SQLite 不能替代这一验收。
- [x] **5. 提交。** 仅暂存本任务文件；`git commit -m "feat: add WorkBuddy employee and center access models"`。

### Task 2: 固定企业的 OneID 验票与组织查询适配器

**Files:** 新增 `api/auth/__init__.py`（若尚无）、`api/auth/workbuddy/{__init__,types,config,oneid_client}.py`；新增 `test/unit_test/api/auth/workbuddy/test_{config,oneid_client}.py`。

**Interfaces:** `WorkBuddyConfig.from_env() -> WorkBuddyConfig`；`OneIDClient.verify_employee(token: str) -> VerifiedEmployee` 为 async，身份失败抛 `WorkBuddyAuthenticationError`，平台不可用抛 `WorkBuddyProviderUnavailable`；路径不完整仍可返回 complete=False。`close() -> None` 负责 async client 生命周期。

- [x] **1. 写失败测试。** `test_requester_and_audience_are_distinct` 使用 introspection 的 codebuddy 和目标应用 aud，断言验证成功且仅产生规范化 VerifiedEmployee。参数化错误企业/issuer/aud/requester、active 为字符串、过期、用户 ID 不一致、禁用用户，均断言失败；不得调用建号服务。`test_root_must_be_confirmed_and_equal_names_preserved` 验证 roots 确认省略 parent 的根，保留两个同名不同 ID 节点；循环、超深 24 级、未知根返回不完整路径。`test_credentials_are_never_logged_or_redirected` 验证 302 不跟随、超时和超限失败、日志无票据和 Secret。
- [x] **2. 验证红灯。** Run: `.venv/bin/python -m pytest test/unit_test/api/auth/workbuddy -q`；Expected: 新模块/行为缺失而失败。
- [x] **3. 实现契约。** 整理 `/home/qaadmin/workbuddy-oauth-probe/ticket_{introspection,directory,verifier}.py` 已验证流程；生产模块不 import 原型。使用 httpx.AsyncClient，固定配置 issuer 的 Discovery、introspect、userinfo、token，通讯录固定 `https://api.account.tencent.com/openapi/v3/contacts/`；执行 Global Constraints 全部验证。部门路径最多 16 条；不用令牌里的 URL 决定请求目标。token 的 exp 必须有效整数，拒绝 bool；iat/nbf 如存在同样严格验证；sub 不可为应用身份。缓存仅 Discovery 和应用 token，账号状态逐次查。
- [x] **4. 配置并验证绿灯。** 环境变量采用 `RAGFLOW_WORKBUDDY_ENABLED`（默认 false）、`RAGFLOW_WORKBUDDY_ISSUER`、`RAGFLOW_WORKBUDDY_TENANT_ID`、`RAGFLOW_WORKBUDDY_APPLICATION_ID`、`RAGFLOW_WORKBUDDY_CLIENT_SECRET`、`RAGFLOW_WORKBUDDY_REQUESTING_CLIENTS`；启用却缺配置直接失败，默认禁用时不影响旧启动。重复测试命令全部通过；复用原型测试场景而不复制真实配置/用户/票据。
- [x] **5. 提交。** `git commit -m "feat: verify WorkBuddy employees through trusted OneID endpoints"`。

### Task 3: 事务建号、资料同步与本地停用

**Files:** 新增 `api/db/services/workbuddy_employee_service.py`、`test/unit_test/api/db/services/test_workbuddy_employee_service.py`。

**Interfaces:** `WorkBuddyEmployeeService.sync_employee(profile: VerifiedEmployee) -> User`；`set_enabled(user_id: str, enabled: bool, actor_id: str) -> None`；列表/详情由 Task 5 包装。只接收 Task 2 已验证资料。

- [x] **1. 写失败测试。** `test_jit_creates_mcp_only_account` 断言来源 workbuddy、password/access_token 为 None、is_superuser 为 False、email=`wb-<id>@workbuddy.invalid`，无新增 Tenant/UserTenant/TeamMember/File。`test_existing_local_user_is_not_merged` 设置同名同邮箱本地用户，断言未复用。`test_concurrent_first_login_reuses_one_user` 模拟唯一键争用并断言一对映射且无孤儿 User。`test_profile_sync_does_not_reactivate_disabled_user` 先停用再同步，断言仍停用。
- [x] **2. 验证红灯。** Run: `.venv/bin/python -m pytest test/unit_test/api/db/services/test_workbuddy_employee_service.py -q`；Expected: 服务不存在/新增契约失败。
- [x] **3. 实现。** 一个事务插入 User 和身份记录；竞争失败回滚该事务再查询已提交唯一身份，禁止留下孤儿账号。省略 password 参数，不调用 create_new_user。规范化资料改变才更新；路径不完整保存待同步状态。同步只更新资料不改 is_active。状态管理只接受 workbuddy 来源；禁用与资料写入字段分开更新避免竞态。
- [x] **4. 验证绿灯。** 同命令全部通过；断言无授权时可建号但没有附加可读库。真实数据库并发事务测试归 Task 11。
- [x] **5. 提交。** `git commit -m "feat: provision isolated WorkBuddy employee accounts"`。

### Task 4: 组织中心定义与原子授权清单

**Files:** 新增 `api/db/services/center_dataset_service.py`、`test/unit_test/api/db/services/test_center_dataset_service.py`。

**Interfaces:** `sync_departments(enterprise_id: str, paths: tuple[DepartmentPath, ...]) -> None`；`set_center(enterprise_id: str, department_id: str, enabled: bool, actor_id: str) -> None`；`resolve_center(enterprise_id: str, paths: tuple[DepartmentPath, ...]) -> str | None`；`replace_grants(enterprise_id: str, center_id: str, dataset_ids: list[str], actor_id: str) -> frozenset[str]`；`allowed_datasets(enterprise_id: str, center_id: str | None) -> frozenset[str]`，均为 CenterDatasetService 方法。

- [x] **1. 写失败测试。** `test_center_resolution_uses_unique_primary_path_and_ids` 断言后勤路径命中已标记 ID，改名不改授权；0/2 主部门、路径不完整、0/2 中心命中皆 None。`test_replacing_grants_is_atomic_and_empty_revokes_all` 先保存 kb-a，再提交 kb-b+不存在 ID，断言原清单仍 kb-a；提交 [] 后集合为空。`test_inactive_or_foreign_center_cannot_be_granted` 断言未标记/其他企业中心拒绝，已删除 KB 不进入有效范围。`test_department_sync_preserves_admin_center_flags` 断言资料同步不覆盖标记。
- [x] **2. 验证红灯。** Run: `.venv/bin/python -m pytest test/unit_test/api/db/services/test_center_dataset_service.py -q`；Expected: 服务/行为缺失。
- [x] **3. 实现。** 根据当前数据库中心标记计算唯一主路径中心；禁止标记祖先与后代为两个中心。replace_grants 先验证所有 ID 后事务更新完整清单，撤销记录 enabled=False 以保留操作者/时间；禁止将空列表解释为无限制。同步父链保留相同名称不同 ID；实时查询 active KB，不能从员工展示快照获得授权。
- [x] **4. 验证绿灯。** 同命令全部通过；相同库可授权多个中心，两企业相同部门 ID 的数据不能串联。
- [x] **5. 提交。** `git commit -m "feat: manage center definitions and read only dataset grants"`。

### Task 5: 超管员工与中心管理 API

**Files:** 新增 `admin/server/workbuddy_service.py`、`test/unit_test/admin/server/test_workbuddy_admin.py`；修改 `admin/server/{routes,services}.py`。

**Interfaces:** `WorkBuddyAdminService` 组合 Task 3/4；API 沿用 success_response/error_response 包装。以下路径均在 `/api/v1/admin/workbuddy` 下，enterprise_id 取服务器配置而非客户端任意输入：

| 方法与路径 | 请求 / 返回 data |
| --- | --- |
| GET `/employees` | q、center_id、enabled、page、page_size；返回 `{items,total}` |
| GET `/employees/<user_id>` | 返回员工资料、路径和当前 allowed_dataset_ids |
| PUT `/employees/<user_id>/enabled` | `{enabled: boolean}` |
| GET `/departments`、GET `/centers` | 返回已同步组织及中心，不伪称已同步全公司 |
| PUT `/departments/<department_id>/center` | `{enabled: boolean}` |
| GET `/datasets` | q、page、page_size；返回有效候选库 `{items,total}`，每项含 id/name/owner |
| GET、PUT `/centers/<center_id>/datasets` | GET 返回 `{dataset_ids}`；PUT 接收 `{dataset_ids: string[]}`，整批替换 |

- [x] **1. 写失败测试。** `test_all_workbuddy_admin_routes_require_superadmin` 对每条路由直调断言普通用户失败且无写入。`test_employee_filters_and_safe_fields` 验证分页/搜索/中心/状态、无密码票据、姓名/路径/同步状态。`test_legacy_password_promotion_delete_and_key_creation_reject_employees` 对旧后台操作和 API Key 创建入口直调，断言不可把员工变为可登录或持 Key 的管理员。`test_bad_replacement_does_not_partially_update` 通过真实管理路由验证事务语义。
- [x] **2. 验证红灯。** Run: `.venv/bin/python -m pytest test/unit_test/admin/server/test_workbuddy_admin.py -q`；Expected: 新路由不存在或缺保护。
- [x] **3. 实现。** 每条管理路由采用现有 login_required+check_admin_auth；状态值严格 boolean，分页范围沿现有管理风格限制，拒绝无效筛选。旧 UserMgr 列表新增来源筛选，缺省保留历史兼容；UI 本地页显式传 local。员工通过 user_id 定位；旧按邮箱的写操作增加来源保护。变更记录可信操作者 ID 和时间，返回字段白名单。
- [x] **4. 验证绿灯。** 同命令及 `test/unit_test/admin/server/test_user_service_mgr.py` 通过；确认当前项目商业版开关关闭仍可调用新超管 API。
- [x] **5. 提交。** `git commit -m "feat: add superadmin WorkBuddy employee and center APIs"`。

### Task 6: Quart 的员工身份上下文及逐请求授权

**Files:** 新增 `api/apps/workbuddy_auth.py`、`test/unit_test/api/apps/test_workbuddy_auth.py`；修改 `api/apps/{__init__,api_key_auth}.py`。

**Interfaces:** `async authenticate_workbuddy(token: str) -> tuple[User, WorkBuddyAccessContext]`；`AUTH_WORKBUDDY="WORKBUDDY"`；`g.workbuddy_context`；`allowed_dataset_ids() -> frozenset[str] | None`、`require_dataset_access(dataset_ids) -> list[str]` 兼容原签名，员工永不返回代表无限制的 None。

- [x] **1. 写失败测试。** 用真实 Quart 测试请求上下文和测试路由；`test_foreign_enterprise_never_provisions` 断言其他 tid 身份 401 且 User 不增。`test_invalid_or_duplicate_oneid_header_never_falls_back` 带有效旧 Key/Cookie/g.user 但错误/重复/逗号合并票据，断言拒绝。`test_employee_requires_explicit_route_and_scope` 断言默认认证及写路由拒绝，显式只读路由允许。`test_local_disable_grant_revocation_and_move_apply_next_request` 复用票据，修改状态/授权/上游路径，断言后续请求立刻使用新范围且无授权不返回 None。
- [x] **2. 验证红灯。** Run: `.venv/bin/python -m pytest test/unit_test/api/apps/test_workbuddy_auth.py -q`；Expected: 新分支/限制不存在。
- [x] **3. 实现。** login_required async 包装器在任何 g.user/旧凭据缓存前处理 OneID 头；不受支持路由看到该头直接拒绝，不回退旧认证。校验 OneID 后同步组织和账号，检查本地状态，再用最新中心定义和清单建立上下文。同步 Peewee 操作在独立线程内打开/关闭数据库连接；不要跨线程共享 request/g 或事务。_load_user 保持同步，只消费已建立的 WorkBuddy g.user，不执行网络 I/O。初始化/关闭 OneIDClient 纳入 Quart 生命周期。
- [x] **4. 验证绿灯。** 同命令和 `test/unit_test/api/apps/test_api_key_auth.py` 通过；验证平台不可用返回可识别 503、身份无效 401、本地停用/未授权资源 403。结构化访问事件仅记录 request_id、actor_user_id、enterprise_id、center_id、请求库 ID、scope、结果和时间，不记录内容或票据。
- [x] **5. 提交。** `git commit -m "feat: authenticate WorkBuddy readonly API requests"`。

### Task 7: 库列表和文档读取的授权及数据归属

**Files:** 新增 `api/apps/services/workbuddy_read_service.py`、`test/unit_test/api/apps/restful_apis/test_workbuddy_dataset_documents.py`；修改 `api/apps/restful_apis/{dataset,document}_api.py`、`api/apps/services/dataset_api_service.py`。

**Interfaces:** `require_workbuddy_datasets(context: WorkBuddyAccessContext, dataset_ids: list[str]) -> dict[str, Knowledgebase]`；`require_workbuddy_document(context, dataset_id: str, document_id: str) -> Document`。只有 WorkBuddy 分支调用，原 owner/team 服务不改变全局语义。

- [x] **1. 写失败测试。** 真实路由测试 `test_employee_sees_only_granted_dataset_names_and_counts`，A 授权 kb-a，列表/名称过滤/分页 total 仅 A；[] 授权成功返回空列表。`test_document_scope_checks_dataset_owner_and_document_binding`，授权不同所有者 kb-a 后可列 docs，但 kb-b 或 kb-a 路径拼 kb-b 文档均拒绝且无元信息；显式未授权 dataset 查询 403。
- [x] **2. 验证红灯。** Run: `.venv/bin/python -m pytest test/unit_test/api/apps/restful_apis/test_workbuddy_dataset_documents.py -q`；Expected: 只读身份未接通/范围错误。
- [x] **3. 实现。** 仅 GET datasets、GET datasets/<id>、GET datasets/<id>/documents 接受显式 AUTH_WORKBUDDY 与 knowledge:retrieve；列表在 SQL 查询过滤允许集合后分页/count，不能先分页再过滤。WorkBuddy 分支授权后取真实 KB tenant_id 查询内容和统计，不把员工 ID 写入数据 owner。原文件下载、缩略图、写入、解析等路由保持不接受员工身份。
- [x] **4. 验证绿灯。** 同命令以及现有 `test/unit_test/api/apps/restful_apis/test_team_kb_content_permissions.py` 通过；验证通过修改 query 参数/ID 不能跳过 allowlist。
- [x] **5. 提交。** `git commit -m "feat: scope WorkBuddy dataset and document reads by center"`。

### Task 8: 检索、chunk 和图片四工具后端边界

**Files:** 修改 `api/apps/restful_apis/chunk_api.py`、必要的 Task 7 只读服务；新增 `test/unit_test/api/apps/restful_apis/test_workbuddy_retrieval_chunks.py`。

**Interfaces:** 复用 Task 7 的 KB/document 授权对象；新增 `require_workbuddy_chunk(context, dataset_id: str, document_id: str, chunk_id: str) -> dict`，从授权 KB 的真实索引取 chunk 并验证 kb/doc 归属。

- [x] **1. 写失败测试。** `test_retrieval_defaults_to_exact_allowlist` 验证省略 dataset_ids 仅用允许集合，空集合提示“暂未配置可访问知识库”。`test_mixed_allowed_and_forbidden_ids_fail_before_search` 断言混合 ID 整次失败且搜索/模型未调用。`test_cross_owner_uses_kb_tenant_not_employee` 捕获索引和模型租户，断言只等于获准 KB tenant_id。`test_chunk_and_image_cannot_cross_dataset_or_document` 拼接外库 doc/chunk/image，断言在读取图片存储前失败。`test_incompatible_embedding_models_keep_existing_error` 保留现有异构模型报错。
- [x] **2. 验证红灯。** Run: `.venv/bin/python -m pytest test/unit_test/api/apps/restful_apis/test_workbuddy_retrieval_chunks.py -q`；Expected: 员工分支未实现/旧 owner 判断不适配。
- [x] **3. 实现。** 显式开放 POST retrieval、GET 文档 chunks、GET 单 chunk、GET chunk/image；每个请求先完成全部库授权，再实例化模型/搜索。文档状态、dataset 归属、chunk 的 kb_id/doc_id 和图片引用逐层核对；图片位置只能来自已验证 chunk，不能直接采用客户端 storage key。原局部 API Key 配额/计数仍按原分支执行，不伪造 employee 的 APIKeyContext。不修改全局 KnowledgebaseService.accessible 或 Team 编辑权限。
- [x] **4. 验证绿灯。** 同命令、既有 team 内容权限和 scoped API Key 的相关路由测试通过；核对任何未开放的 GET/POST 路由依然拒绝员工。
- [x] **5. 提交。** `git commit -m "feat: enforce center access for retrieval chunks and images"`。

### Task 9: MCP 个人凭据转发与撤权后的缓存行为

**Files:** 修改 `mcp/server/server.py`；新增 `test/unit_test/mcp/test_server_workbuddy_auth.py`；扩展既有 MCP datasets/document/chunk-image/streamable-http 测试。

**Interfaces:** 新配置 `RAGFLOW_MCP_AUTH_MODE=api-key|workbuddy`，默认 api-key；WorkBuddy 仅兼容 LaunchMode.HOST。`RAGFlowConnector(base_url, version="v1", auth_mode="api-key")`；既有内部 api_key 参数暂作为不公开的凭据传参保持工具调用接口，WorkBuddy 模式只能转发 X-OneID 头，不能生成 Authorization Key。

- [x] **1. 写失败测试。** `test_workbuddy_streamable_http_forwards_ticket_without_api_key` 用 ASGITransport 发 initialize/list/call，捕获后端请求，断言只转发 x-oneid-access-token，四工具 schema 无员工/中心/Key 参数。`test_duplicate_ticket_and_legacy_only_request_are_rejected` 断言重复/缺失票据拒绝。`test_reused_ticket_does_not_return_cached_revoked_metadata` 同票据第二次模拟后端撤权，断言重新调用后端且无旧库名/文档/图片；失败不回退 HOST_API_KEY。`test_two_concurrent_employees_never_share_credentials` 并发两个请求，断言后端收到各自票据。
- [x] **2. 验证红灯。** Run: `.venv/bin/python -m pytest test/unit_test/mcp/test_server_workbuddy_auth.py -q`；Expected: 模式/头转发不存在。
- [x] **3. 实现。** ASGI 原始 headers 检查重复头；票据只放每请求状态/上下文，不写 connector 全局变量。WorkBuddy 禁用 dataset IDs、dataset metadata、document metadata 的跨请求缓存及对应 inflight 去重，保留单请求局部合并。后端 401/403 和空授权消息传给调用者；部分文档查询不得吞掉权限失败后返回旧/部分结果。无后端身份验证时不得声称连接即授权。新增模式启动校验和只显示模式的日志。
- [x] **4. 验证绿灯。** Run: `.venv/bin/python -m pytest test/unit_test/mcp -q`；Expected: 四工具的新边界与旧 API Key/SSE/Streamable HTTP 测试通过。
- [x] **5. 提交。** `git commit -m "feat: forward WorkBuddy employee tickets through MCP"`。

### Task 10: 员工页签、组织详情和中心知识库授权页面

**Files:** 修改 `web/src/pages/admin/{users,user-detail}.tsx`、`layouts/navigation-layout.tsx`、`web/src/routes.tsx`、`web/src/services/{admin-service.ts,admin.service.d.ts}`、`web/src/locales/{zh,en}.ts`；新增 `web/src/pages/admin/{workbuddy-employees,center-access}.tsx` 及对应 `.test.tsx`。

**Interfaces:** 服务函数 `listWorkBuddyEmployees`、`getWorkBuddyEmployee`、`setWorkBuddyEmployeeEnabled`、`listWorkBuddyDepartments`、`listWorkBuddyCenters`、`setWorkBuddyCenter`、`listWorkBuddyDatasets`、`getWorkBuddyCenterDatasets`、`replaceWorkBuddyCenterDatasets`，参数/返回与 Task 5 一致；类型放 AdminService 命名空间，Employee 不复用必填邮箱的旧账号行类型。

- [x] **1. 写失败测试。** React Testing Library 的 `employee_tab_shows_center_path_and_enable_only` 验证页签隔离、姓名/中心/主部门/业务路径/状态/同步时间/最近访问、无密码/超管/删除动作。`empty_grants_and_unsynced_profile_are_explained` 验证“未配置可访问知识库”、待同步显示不伪造部门。`saving_center_replaces_complete_list_including_empty` 验证取消全部后提交 []，失败不显示成功；同名 KB 显示可识别 owner/ID。`center_page_is_available_without_commercial_flag` 验证新导航不受 IS_ENTERPRISE 控制。
- [x] **2. 验证红灯。** Run（web 目录）: `npx jest --runInBand --coverage=false src/pages/admin/workbuddy-employees.test.tsx src/pages/admin/center-access.test.tsx`；Expected: 新页面/行为缺失。
- [x] **3. 实现。** 复用现有表格、Tabs、Select 和 React Query；增加本地账号/WorkBuddy 员工页签；员工详情使用 user_id 展示完整层级，隐藏占位邮箱。中心页选择已同步节点标记中心、展示祖先/后代冲突、完整勾选有效 KB、保存后刷新当前授权。未保存修改切换中心时提醒，避免误把 A 的清单提交给 B；保存提交时绑定当时 center_id。请求失败显示可理解错误。中英文提供相同 key，不泄露配置实现细节到产品流程。
- [x] **4. 验证绿灯。** 重复 Jest 命令；`npm run type-check`、针对修改文件的 eslint、`npm run build`；记录基线已有错误。浏览器检查员工页/详情/中心页、窄屏与长路径、空授权、保存失败；本地账号原有创建/停用等操作回归。
- [x] **5. 提交。** `git commit -m "feat: add WorkBuddy employees and center dataset admin pages"`。

### Task 11: 实际方言集成、部署配置和两名员工验收

**Files:** 新增 `test/integration/workbuddy/test_center_access.py`、`docs/guides/workbuddy-center-access.md`；必要时修改实际部署所用环境模板，模板只有变量名和空 Secret；更新本计划验收记录。

**Interfaces:** 集成测试仅连接显式传入的隔离测试数据库，默认不运行真实服务；文档定义 `RAGFLOW_WORKBUDDY_TEST_DATABASE_URL` 及 `RAGFLOW_WORKBUDDY_RUN_INTEGRATION=1`。拒绝复用生产 DB 配置做迁移测试。

- [x] **1. 写失败测试。** `test_migration_on_legacy_schema_twice` 在与实际部署相同方言的临时库验证重复迁移。`test_concurrent_jit_has_single_mapping_and_no_orphan_user` 以独立连接/事务并发首登。`test_live_request_chain_cannot_bypass_center_grants` 运行真实 Quart+MCP HTTP 请求链，用受控 OneID、模型和存储替身；依次验证本企业首登/建号/允许库/跨库拒绝/撤权/本地禁用/调部门/外企业无建号，不能只跑服务函数模拟成功。
- [x] **2. 验证红灯。** Run: `RAGFLOW_WORKBUDDY_RUN_INTEGRATION=1 .venv/bin/python -m pytest test/integration/workbuddy -q`（安全提供临时库连接，命令和日志不得包含密码）；Expected: 尚未满足的端到端条件明确失败。
- [x] **3. 完成部署说明。** 核对运行中的后端、admin、前端与 MCP 各自代码版本/启动入口；文档写明备份、增量迁移、服务端 Secret 配置、默认关闭开关、新 WorkBuddy MCP 进程与已知 allowed hosts。保留9395诊断和9382旧 Key 入口，首次新正式联调使用独立空闲端口，不直接覆盖旧服务。Login Callback `http://127.0.0.1:9394/callback` 属于旧浏览器 OIDC 测试，免授权 MCP 不走该回调，不改成 /mcp。失败回滚先关闭新模式，保留新增表/员工来源，不退回会误识别来源的旧读写逻辑。
- [x] **4. 自动验证绿灯。** 集成测试和 Tasks 1–10 相关单测通过；确认构建产物与新服务使用同一提交。保留无 Secret 的测试记录及命令，不输出运行环境变量全集。
- [ ] **5. 真实验收。** 部署前完成配置差异和回滚命令，若仍需用户确认则只就具体部署结果请求最终确认。唯一确定“研发结构案例库”ID；若同名库不唯一，提供 owner/ID 请用户选定，不能猜。超管授权已核实中心；由用户在 WorkBuddy 重新连接并调用四工具，两名本企业真实员工分别验证建号、actor 和范围，再撤销授权重试。禁止让用户在聊天中粘贴原始票据/Secret。第二员工或真实环境未验证时如实标记待验收，不能用模拟测试声称已完成。
- [ ] **6. 提交。** `git commit -m "test: verify WorkBuddy center access end to end"`；记录已执行项、未执行项和生产切换状态。

## 依赖、交付和停止条件

1. Tasks 1–5：账号与中心管理服务可测，后台 API 可用；不代表现有 WorkBuddy 已可检索。
2. Tasks 6–9：正式后端与 MCP 个人身份链路接通，四工具按中心校验；默认无 grant。
3. Task 10：超管页面可配置与管理，当前项目版本可见。
4. Task 11：隔离数据库演练、真实员工、真实 KB 检索与撤权验收后，才报告正式接入完成。

实施中每任务完成更新复选框并报告验证证据。发现规格冲突先解决具体冲突，不自行扩大到网页 SSO、多中心合并或编辑权限。Tasks 1–10 已实现，Task 11 自动数据库与 HTTP 链路验证已通过；生产迁移、切换与两名真实员工验收待执行。不把独立9395诊断的通过结果算作生产功能交付。
