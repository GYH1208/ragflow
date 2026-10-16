# WorkBuddy MCP OAuth 完整连接实施计划

> 本计划暂不执行：用户已选择[自建应用免授权 + OneID 资料补全方案](../specs/2026-10-14-workbuddy-delegated-identity-design.md)。先确认平台身份票据协议，再据此制定新实施计划；不按下文开发动态客户端注册、自建授权页和个人 OAuth 令牌签发功能。

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. 建议在当前会话由主代理执行；实施前完成计划评审。

**Goal:** 员工在 WorkBuddy 点击连接、完成 OneID 授权后，无需填写静态 Key，即可通过个人 MCP 令牌检索“研发结构案例库”，知识库能识别实际员工。

**Architecture:** 在现有知识库 API 内增加 OAuth 授权服务，现有 MCP 作为资源服务。复用现有检索实现及 scoped 权限策略，明确区分员工身份和案例库的数据租户；不通过管理员共享 Key 隐藏员工身份。

**Tech Stack:** Python、Quart、Starlette MCP SDK、PyJWT、Peewee、现有数据库和 Redis、HTTPS 反向代理。

**Spec:** [接入设计](../specs/2026-10-14-workbuddy-oneid-mcp-oauth-design.md)。此计划交付设计第一至第四阶段。后台新增授权展示/撤销界面及正式连接器迁移列入后续阶段；现有后台停用必须在本阶段生效。

## Global Constraints

- 本企业所有员工均可访问；WorkBuddy 管理员控制连接器对谁可见。
- 首次联调只开放“研发结构案例库”的唯一 ID。名称仅用于查找，权限校验使用 ID。
- scope 固定为 `knowledge:retrieve`；拒绝写入、管理与原始文件下载。
- 强制下游 PKCE `S256`；授权码 5 分钟、一次性；access token 15 分钟；refresh token 最长 30 天并轮换。
- 上游固定 OneID issuer 和企业 ID，默认不发送 nonce；开启后严格验证，不自动降级。
- 签名、issuer、audience、有效期、subject、适用的 azp/at_hash，以及 UserInfo subject 一致性必须校验。
- 不将原型配置、真实 Client Secret、授权码或令牌写入 Git 或日志。
- 所有新功能默认关闭；隔离开发与联调，不替换正在使用的连接器。
- WorkBuddy 真实远程验收使用证书可信且双方可访问的 HTTPS 地址。只有模拟客户端通过不算完成。

## Review Focus

1. 两个员工或多个浏览器同时授权，事务、回调和令牌不能串到另一人（任务 2、3、6）。
2. 专用用户没有个人 Tenant，也能检索已有案例库，且不能成为案例库所有者（任务 1、4）。
3. 代理路径、Host、Origin、URL 编码和 WorkBuddy 动态回调差异不能导致错误拒绝或开放重定向（任务 3、5、6）。
4. 权限撤销、账号停用和缓存命中同时发生时，下一次请求不能继续返回内容（任务 4、5）。
5. 刷新重放、上游网络失败或企业字段缺失不能默默退回共享 Key 或继续签发令牌（任务 2、3）。

## 文件与接口分工

新增模块集中在 `api/apps/mcp_oauth/`，不放入现有按邮箱匹配的网页登录逻辑：

| 文件 | 职责 |
| --- | --- |
| `config.py` | 功能开关、固定 URL/企业/库范围、有效期和回调校验配置 |
| `types.py` | `VerifiedIdentity`、`OAuthContext`、`TokenPair` 数据类型 |
| `identity.py` | OneID 交换、声明验证、企业判断及 JIT 账号绑定 |
| `store.py` | Redis 短期事务/授权码；数据库令牌摘要、轮换及撤销 |
| `routes.py` | 显式注册根路径 OAuth 与元数据接口 |
| `policy.py` | 当前员工、scope、库范围和数据所有者的统一判定 |

`VerifiedIdentity` 字段为 `issuer: str, sub: str, enterprise_id: str, display_name: str`；只能由完成验证的适配器创建。

`OAuthContext` 字段为 `actor_user_id: str, grant_id: str, client_id: str, resource: str, scope: frozenset[str], dataset_ids: frozenset[str]`，不持有原始令牌。

`TokenPair` 为返回 OAuth 客户端的 access/refresh token 及有效期结构，关闭自动 repr，数据库仅保存令牌摘要。上游刷新凭据单独加密。

## 任务 1：身份、授权存储与配置

**Files:** 新增上述 `config.py`、`types.py`、`store.py` 与包初始化；修改 `api/db/db_models.py`；新增 `test/unit_test/api/apps/mcp_oauth/test_store.py`、`test_config.py`。

**Interfaces:** `load_config() -> OAuthConfig`；`resolve_access(token: str, resource: str) -> OAuthContext | None`；`revoke_user_grants(user_id: str) -> int`。数据库新增 ExternalIdentity、OAuthClient、OAuthGrant、OAuthAccessToken、OAuthRefreshToken；User 增加默认兼容旧用户的账号类型字段。

- [ ] 写失败测试：缺 issuer/企业 ID/空库列表时开启功能失败；关闭功能不要求 OAuth 配置；数据库无原文令牌；同一身份并发写入只能保留一条绑定；撤销后 resolve 返回 None。
- [ ] 运行 `.venv/bin/python -m pytest test/unit_test/api/apps/mcp_oauth/test_store.py test/unit_test/api/apps/mcp_oauth/test_config.py -q`，确认失败来自待实现行为。
- [ ] 实现配置与幂等迁移，沿用项目已有数据库初始化入口。令牌摘要使用 SHA-256，随机令牌至少 256 位。授权状态读取失败时拒绝访问。
- [ ] 实现 Redis 短期状态的带 TTL 写入和原子消费；刷新轮换使用数据库事务和条件更新，避免多个进程重复消费。
- [ ] 重跑上述测试，确认全通过，并验证迁移重复执行不破坏旧用户。
- [ ] 只提交本任务文件；当前已知提交钩子缺少 npx，若执行环境仍不满足，记录未提交状态，不跳过钩子冒充成功。

## 任务 2：OneID 身份验证与真实自动建号

**Files:** 新增 `identity.py`、`test_identity.py`；修改 `api/apps/restful_apis/user_api.py` 的专用账号登录/找回限制；补充 `admin/server/services.py` 对无个人 Tenant 用户的管理兼容。

**Interfaces:** `verify_oneid_login(code: str, transaction_id: str, browser_binding: str) -> VerifiedIdentity`；`ensure_local_user(identity: VerifiedIdentity) -> str`；`revalidate_upstream(grant_id: str) -> VerifiedIdentity`。依赖任务 1 的配置、身份唯一键和授权记录。

- [ ] 写失败测试：错误签名/issuer/audience/sub、错误或缺少企业声明、跨浏览器 state、nonce 开启后缺失均失败；同名不同 sub 是不同账号；同 sub 重复及并发授权复用账号；停用账号不恢复。
- [ ] 增加专用用户测试：不设置密码，不创建个人 Tenant，不加入编辑团队；内部 `.invalid` 标识不可用于邮件注册/密码找回；既有用户正常登录仍有效。
- [ ] 运行 `.venv/bin/python -m pytest test/unit_test/api/apps/mcp_oauth/test_identity.py -q`，确认预期失败。
- [ ] 将独立原型已验证的 OneID 逻辑移植到正式适配器，使用任务 1 的持久化事务；不导入原型的 SQLite、HTTPServer 或测试配置。复核 tid 的官方语义及企业值后配置严格比较。
- [ ] 上游 scope 支持 `openid profile offline_access`；加密保存上游 refresh token。刷新时检查企业和本地账号；网络失败、声明缺失或上游拒绝均不给新令牌。
- [ ] 重跑本任务测试，并运行 `.venv/bin/python -m pytest test/testcases/test_web_api/test_auth_app/test_oauth_client_unit.py -q`；记录通过结果后提交本任务文件。

## 任务 3：WorkBuddy 所需 OAuth 服务接口

**Files:** 新增 `routes.py`、`test_protocol.py`；修改 `api/apps/__init__.py` 显式注册 OAuth 蓝图，不改变旧 API 前缀。

**Interfaces:** 按设计提供 metadata/register/authorize/oneid callback/token/revoke。内部 `exchange_code(client_id: str, code: str, redirect_uri: str, verifier: str, resource: str) -> TokenPair`；`rotate_refresh(client_id: str, refresh_token: str, resource: str) -> TokenPair`。

- [ ] 写失败测试：发现元数据端点 URL 一致；注册响应回显 redirect_uris；PKCE S256 成功；plain、错误 verifier、错 client/redirect/resource、超 scope、码重放均拒绝。
- [ ] 增加回调测试：官方私有协议含 `%3A`、本机动态端口分别通过；前缀碰撞、任意外域、回调 URI 非精确匹配拒绝。注册时允许的形式与实际授权时精确匹配分开处理。
- [ ] 增加并发授权、确认页 CSRF、客户端名称冒充、刷新令牌串用及重放测试；上游 state 和 WorkBuddy 下游 state 分别正确保存/回传。
- [ ] 运行 `.venv/bin/python -m pytest test/unit_test/api/apps/mcp_oauth/test_protocol.py -q`，确认预期失败。
- [ ] 实现接口、绑定浏览器的安全 cookie、限流及客户端授权确认页。内部验证上游 state；下游原样回传客户端 state，PKCE 绑定到下游事务。错误响应符合 OAuth 格式且不带秘密。
- [ ] 执行同一测试命令，确认全通过后提交。

## 任务 4：员工令牌真正限制知识库检索

**Files:** 新增 `policy.py`、`test_policy.py`；修改 `api/apps/__init__.py`、`api/apps/api_key_auth.py`、`api/apps/restful_apis/dataset_api.py`、`document_api.py`、`chunk_api.py` 和 `api/apps/services/dataset_api_service.py`；扩展 `test/unit_test/api/apps/restful_apis/test_scoped_retrieval_routes.py`。

**Interfaces:** `authorize_dataset(context: OAuthContext, dataset_id: str) -> Knowledgebase`；`current_oauth_context() -> OAuthContext | None`。返回经授权的实际库记录，其 tenant_id 用于检索底层索引和模型；actor_user_id 始终保留真实员工。

- [ ] 写失败测试：无 Tenant 的员工可查询获准案例库，其他库 403；修改文档/chunk ID 无法跨库；列表先过滤后分页/计数；图片、缓存命中仍检查授权；写入和管理接口拒绝。
- [ ] 加入有效浏览器 session 搭配无效 OAuth bearer 的测试，必须拒绝 OAuth 请求而非回退到浏览器管理员身份；新令牌也不能落入旧 API Key 分支。
- [ ] 运行 `.venv/bin/python -m pytest test/unit_test/api/apps/mcp_oauth/test_policy.py test/unit_test/api/apps/restful_apis/test_scoped_retrieval_routes.py -q`，确认预期失败。
- [ ] 新增显式 OAuth 认证分支和 scope 限制。仅改造 MCP 使用的只读路径，不把全局 `add_tenant_id_to_kwargs` 改成任意代入所有者。按库记录决定数据租户，按员工记录审计。
- [ ] 复用 scoped Key 的知识库范围与限流策略，OAuth 以员工/授权为配额主体；配置为空、授权服务不可用时拒绝。停用、撤销和策略缩小在下一次请求生效。
- [ ] 重跑本任务测试及 `test/unit_test/api/apps/test_api_key_auth.py`，确认旧 Key 行为与新边界均正确后提交。

## 任务 5：MCP 授权发现与真实资源服务

**Files:** 新增 `mcp/server/oauth_auth.py`、`test/unit_test/mcp/test_server_oauth.py`；修改 `mcp/server/server.py`；新增 API 的 `/api/v1/mcp_oauth/context` 路由（在 OAuth 蓝图显式注册）。

**Interfaces:** `validate_mcp_token(token: str) -> OAuthContext` 在 MCP 通过内部后端 context 接口验证原始个人 token，接口只返回该令牌自己的最小授权上下文；内部地址来自服务器配置，不由用户传入。OAuth 模式禁止 SELF_HOST 的共享 Key 替代。

- [ ] 写失败测试：无效/缺失 bearer 在 initialize、tools/list、tools/call 阶段均返回 401 及 resource_metadata challenge；有效个人 token 可调用；后端不可达返回服务错误，不降级。
- [ ] 写测试：MCP 转发的是同一个个人访问 token；缓存命中仍校验停用状态；员工 B 不能复用员工 A 的 MCP 会话状态；错误 Origin/Host 被拒绝，配置的可信域正常连接。
- [ ] 运行 `.venv/bin/python -m pytest test/unit_test/mcp/test_server_oauth.py -q`，确认预期失败。
- [ ] 实现 OAuth middleware 和元数据路径，与现有 stateless Streamable HTTP 配合。所有内容路径在后端再校验，MCP 层不是唯一权限边界；日志只记录员工、授权、工具、库 ID 和结果状态。
- [ ] 执行 `.venv/bin/python -m pytest test/unit_test/mcp -q` 及任务 4 的权限测试，确认通过后提交。

## 任务 6：HTTPS 联调与用户验收

**Files:** 新增 `docker/nginx/workbuddy-mcp-oauth.conf.example`、`docs/guides/workbuddy-mcp-oauth.md`、`test/integration/test_workbuddy_mcp_oauth_flow.py`。示例禁止真实凭据和用户资料。

**Interfaces:** HTTPS `/mcp` 转发 MCP；OAuth 和 well-known 转发 API；OneID 回调为同一 HTTPS 入口的 `/oauth/oneid/callback`。由任务 1 配置规范资源地址和唯一库 ID。

- [ ] 写集成测试，使用测试 OneID、真实本地 OAuth 路由及 MCP 客户端贯穿发现→注册→授权码→token→initialize→tools/list→retrieval。断言返回固定检索夹具，审计员工与授权 subject 一致，拒绝另一个库。
- [ ] 运行 `.venv/bin/python -m pytest test/integration/test_workbuddy_mcp_oauth_flow.py -q`，由失败定位组件接线并修复，最终测试通过。
- [ ] 通过现有受权管理入口只读查找“研发结构案例库”，确认唯一 ID 和可用于验收的文档问题；查不到或同名时请求定位信息，不创建替代库冒充真实检索。
- [ ] 准备一个与生产分开的测试服务实例、HTTPS 入口及证书。确认 WorkBuddy 的实际请求端和浏览器均可访问且信任证书；不以关闭 TLS 校验作为解决方法。
- [ ] 交付精确的 OneID Login Callback URL 和 WorkBuddy MCP Server URL，由用户登记；Client Secret 通过服务端配置，不通过聊天发送。启用 OneID refresh token 并验证实际行为。
- [ ] 新建仅供联调的 WorkBuddy OAuth 连接器，保持当前正在使用的连接器可用。用户点击连接、授权并提问，核对真实库 ID、文档/片段和员工审计记录。只返回 whoami 或假检索结果不算验收成功。
- [ ] 第二名员工重复流程，核对用户及凭据隔离；再次授权同一员工不重复建号；测试停用、重新授权和刷新失败提示。不能访问客户端时保留此项未完成，请用户提供对应操作结果。
- [ ] 指南记录确切可用地址、启动/停止命令、证书安装方式、测试结果和限制；撤销临时测试授权。新后台 UI 与正式切换转入下一阶段，不能自动撤销在用的生产 Key。

## 完成标准与执行状态

只有真实 WorkBuddy 完成个人 OAuth 并返回“研发结构案例库”的真实检索结果，才能报告“完整连接已验证”。单元测试、模拟客户端和独立 OneID 登录成功分别记录，不能替代此结论。

当前状态：计划已编写，生产实现与上述测试尚未执行；联调库名称已确认。企业声明值核验、库 ID、HTTPS 入口和企业连接器个人凭据行为在对应任务中落实。
