# WorkBuddy 员工 MCP 与中心知识库授权

## 功能范围

自建应用免授权模式自动携带 `x-oneid-access-token`。后端逐次在线验证票据、企业与目标应用，再查询 OneID 当前账号状态和部门父链。本企业员工首次通过验证后建立 MCP 专用账号；是否能检索，由主部门所在的唯一中心及超管授权清单决定。其他企业员工不建号、不检索。

WorkBuddy 员工没有密码、网页 access_token、个人 Tenant 或超管权限，不自动加入可编辑团队。姓名和邮箱不用于合并现有本地账号。缺少唯一主部门、完整父链或唯一中心时，可显示员工账号，但无知识库权限。OneID 正常启用状态不等同于独立的人事在职证明。

## 服务端配置

后端和超管服务使用同一版本代码、同一数据库及同一企业配置。Secret 仅放服务器受限环境文件（权限 0600），不能放进前端、MCP 工具参数、Git 或运行日志。示例值为空的 Secret 必须在服务器填写。

```dotenv
RAGFLOW_WORKBUDDY_ENABLED=true
RAGFLOW_WORKBUDDY_ISSUER=https://oauth2.account.tencent.com/authz/oidc/v2/1494560257098951656
RAGFLOW_WORKBUDDY_TENANT_ID=1494560257098951656
RAGFLOW_WORKBUDDY_APPLICATION_ID=i-f17b2d9e2f7447daaa380b5670b59aed
RAGFLOW_WORKBUDDY_CLIENT_SECRET=
RAGFLOW_WORKBUDDY_REQUESTING_CLIENTS=codebuddy
```

开关默认 false。启用时缺失企业、应用、Secret 等必要配置会拒绝启动。OneID 的 introspection.client_id 是请求客户端 codebuddy，aud 是目标自建应用；不能把二者比较为相等，也不能因此取消 aud 校验。

MCP 新进程使用以下非敏感配置，其后端 URL 必须指向已升级、已完成迁移的 API 服务：

```dotenv
RAGFLOW_MCP_AUTH_MODE=workbuddy
RAGFLOW_MCP_LAUNCH_MODE=host
RAGFLOW_MCP_BASE_URL=http://127.0.0.1:9380
RAGFLOW_MCP_HOST=0.0.0.0
RAGFLOW_MCP_PORT=9396
RAGFLOW_MCP_TRANSPORT_SSE_ENABLED=false
RAGFLOW_MCP_TRANSPORT_STREAMABLE_ENABLED=true
RAGFLOW_MCP_JSON_RESPONSE=true
RAGFLOW_MCP_ALLOWED_HOSTS=192.168.1.183:*,127.0.0.1:*,localhost:*
RAGFLOW_MCP_ALLOWED_ORIGINS=http://192.168.1.183:9396,http://127.0.0.1:9396
```

无需为员工填写 `RAGFLOW_MCP_HOST_API_KEY`。新端口需先确认空闲；不覆盖原 9382 API Key 入口或 9395 身份诊断入口。WorkBuddy 管理台仍选“自建应用免授权”，选择原应用，MCP URL 为实际新入口的 `/mcp`，Transport 为 http。服务端验证完成前，初始化/工具发现成功不代表获得知识库授权。

免授权请求不经过浏览器 Login Callback。现有 `http://127.0.0.1:9394/callback` 是先前独立浏览器 OIDC 测试的回调，无需改为 MCP URL。

## 迁移与账号

1. 确认要发布的分支提交、Python 3.13 环境、数据库类型、后端/admin 启动命令与 Node 22.12+ 构建环境。先备份实际数据库，并记录各进程当前版本、端口及回滚命令。
2. 在无生产写流量的迁移窗口使用升级版本的 `init_database_tables()` 入口执行增量迁移，然后启动升级的后端/admin。新代码会查询 User.account_source，不能先启动新代码再等待迁移。
3. 迁移增加来源字段和三张关系表，历史 User 回填 local，不改变既有 KB 所有者、权限、team 或内容。迁移可以重复执行；不能忽略迁移/唯一索引失败继续提供新身份服务。
4. 配置企业为本企业时，只初始化已核实后勤服务中心 `1494587270954266260`，不自动授予任何库，不导入 9394 原型 SQLite 测试账号。
5. 发布同版本前端产物。用户管理有“本地 / 历史账号”和“WorkBuddy 员工”两个页签；员工详情通过 user_id 获取，显示完整组织路径、状态、同步时间与当前授权。
6. 确认新后端和 MCP 正常，再让员工连接。首次身份验证成功会创建员工，即使尚未授权任何知识库。

实际部署服务若由容器或 systemd 管理，应把配置写入对应受限配置文件并采用该服务的既有发布方式；仅在交互式 shell export 不会改变正在运行的服务。不要用打印整个环境变量的方法排查 Secret。

## 中心授权

超管打开“中心知识库授权”：

- 在已同步组织中标记业务中心，不能同时标记同一父链上的祖先和后代。部门名可以改变，授权按企业+部门 ID 关联。
- 选择中心，勾选允许检索的库，保存完整清单。取消所有勾选并保存会撤销全部库权限。
- 一个库可以授予多个中心，一个中心可以拥有多个库；首期不合并员工多个中心权限，也不提供逐人例外或写权限。
- 无授权、中心未确定、OneID 异常、本地停用时，不允许取得库内容。超管本地停用不会被资料同步重新启用。

首轮确认的测试库（只读核对，尚未写入生产授权）：

| 名称 | 唯一 ID | 数据所有者 |
| --- | --- | --- |
| 研发结构案例库 | 87a21ce6bbba11f19a0cf319ea2a82ce | b5b693c476b811f184ce15fcee64160f |

后勤服务中心下的 AI效率工程部 / AI项目组员工可继承该中心的配置。这里的示例不是自动授权脚本，仍需超管保存授权清单。

## 自动验证

新增测试覆盖身份、企业/应用绑定、并发建号、管理 API、中心授权、默认拒绝、四个 MCP 工具、数据所有者、跨库切片/图片和撤权。

Peewee 模型与服务单测使用临时 SQLite；真实数据库测试单独运行。MySQL 集成必须显式设置 `RAGFLOW_WORKBUDDY_RUN_INTEGRATION=1` 和受限环境中的 `RAGFLOW_WORKBUDDY_TEST_DATABASE_URL`，连接库名必须是 `ragflow_wb_test_` 加 16 位十六进制字符。该临时库须由测试管理员预先创建，测试结束删除；禁止指向业务数据库。不要将含密码的 URL 粘贴到终端共享记录、聊天或提交文件。

```bash
PYTHONPATH=. .venv/bin/python -m pytest test/integration/workbuddy -q
```

未设置显式集成开关时跳过实际数据库测试。覆盖的真实 MySQL 5.7 场景：旧结构重复迁移、独立连接并发首登、真实 MCP HTTP → Quart → 规范化 OneID 适配器 → 数据库链路。OneID 外部 HTTP 在自动测试中由受控响应代替，不能把这当成真实平台员工验收。

前端验证：

```bash
cd web
npx jest --runInBand --coverage=false src/pages/admin/workbuddy-employees.test.tsx src/pages/admin/center-access.test.tsx
npm run type-check
npm run build
```

当前主分支有全量 TypeScript 基线错误，发布记录需列出原有错误与新增错误的差异，不能声称全量 type-check 已通过。

## 真实员工验收与状态

代码自动测试通过之后，必须在已升级的真实入口验证：

1. 第一名员工连接，后台出现一个 WorkBuddy 账号，姓名、中心、主部门及完整路径正确；重复连接不新增账号。
2. 超管只为测试中心授予上述唯一案例库，实际调用 ragflow_retrieval、search_documents、get_document_chunks、get_chunk_image。
3. 第二名本企业员工分别建号，访问事件中的 actor_user_id 对应各自员工，不共享用户身份。
4. 显式请求未授权库、跨库 doc/chunk/image，均拒绝；其他企业票据不能创建账号。
5. 撤销中心授权后，同一连接/同一票据下一次请求无权读取；本地停用同样生效。调部门以 OneID 当前返回路径重新计算。
6. 原网页用户、团队 me/team 和旧 API Key 入口回归正常。

本文件随代码交付；生产迁移、正式连接器切换、两名真实员工调用和生产授权写入的完成状态，应在发布记录逐项填写。未做真实联调时不得报告“正式接入已完成”。

## 回滚

出现问题先停止新 WorkBuddy MCP 进程并关闭后端 WorkBuddy 开关，保留原入口。新增表和来源字段保留，避免把员工账号当成本地账号；不要删除真实员工记录、改 KB 所有者或清理已有内容。保持包含账号来源保护的后端/admin 版本，或回滚前明确验证旧代码不会给员工设置密码/授予管理员。旧共享 Key 的撤销单独评估现有调用方。

首期每请求校验员工状态、中心与授权；仅 Discovery、应用 token、网络连接可复用。未来缩短调用耗时不能无说明地延长停用或撤权的生效时间。
