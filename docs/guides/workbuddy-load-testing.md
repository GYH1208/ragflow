# 单服务器 WorkBuddy MCP 受控负载测试

用于只有一台业务服务器时，先测员工 MCP 链路的基线耗时、资源消耗和少量并发下的表现。此脚本有意限制负载，不能据此证明支持 400 人同时使用，也不是完整 WorkBuddy 客户端问答测试。

脚本：`test/load/workbuddy_load.py`。使用现有 Python 3.13 环境中的 `httpx`，真实执行时还需要 `psutil`；不需要安装 Locust，不修改业务服务或启动第二套服务。默认仅离线检查配置，不发送网络请求。

## 测试范围与限制

- 仅访问本机 `/mcp`，默认 `http://127.0.0.1:9396/mcp`，使用员工 `x-oneid-access-token`，不接受 URL 凭据，不跟随重定向，不使用环境代理。
- 模拟 Streamable HTTP 的 initialize、initialized、tools/list，再循环执行配置中的只读工具。使用服务器当前支持的 `2025-06-18` 协议和 JSON 响应模式。
- 支持 `search_documents`、`ragflow_retrieval`、`get_document_chunks`、`get_chunk_image`。显式指定 1～2 个知识库，禁止默认扫描全部库。固定测试参数循环执行；不会自动遍历所有文档或分页读取全库。
- 每档必须从 1 个虚拟用户起步、递增且不超过 10 个；每档请求启动窗口最多 120 秒，档间冷却 15 秒。窗口结束后不再发新请求，已启动请求最多再等待 `--timeout` 秒（默认 20 秒，上限 60 秒）。报告记录实测墙钟时间和窗口口径。
- 所有用户和所有步骤共用请求限速，默认每秒 0.5 个 MCP 请求，硬上限每秒 2 个；初始化也计数。不重试、不补发积压请求；全程最多 1000 个请求，默认预算 300 个。
- 虚拟用户交替使用票据文件中的凭据。多个用户共用一张票据只能验证同一员工并发访问，不能证明不同员工或不同中心的身份隔离。即便提供多张票据，脚本也不会认定它们属于不同员工。
- 工具不修改知识库内容，但现有鉴权仍会同步员工/组织资料和访问时间；首次使用真实员工票据可能建立员工账号。禁止通过此测试向业务数据库导入模拟员工或替换真实鉴权。
- 真实运行会调用 OneID，语义检索还可能调用实际配置的 Embedding/Rerank 服务。MCP 限速不是 OneID 请求数限制：一次工具调用可能触发多次后端鉴权和外部请求。首次运行前应了解相关配额与费用。

## 准备工作负载

从项目根目录执行：

```bash
mkdir -p -m 700 logs/workbuddy-load-input
cp test/load/workbuddy-workload.example.json logs/workbuddy-load-input/workload.json
chmod 600 logs/workbuddy-load-input/workload.json
```

编辑复制后的文件，把全部 `REPLACE_WITH_...` 替换为测试员工已获授权的真实知识库和文档 ID，并把问题、文件名关键词改成已确认能得到结果的内容。示例文件含占位符，故意不能直接运行。选用体量适中的代表性文档；若先只测搜索，可以只保留第一项。工作负载文件保留在被 Git 忽略的 `logs/` 中。

每项的 `min_results` 默认 1，空结果会停止测试，以免把配置错误或实际无数据当成成功；如果空结果就是预期场景，可显式改为 0。它只校验返回格式和数量，不判断回答质量，也不自动证明知识库权限正确。

图片读取可选：选取已经确认有 `image_id` 的切片，用切片的 `id`/`chunk_id`，而非 `image_id`，增加以下工作负载项：

```json
{
  "tool": "get_chunk_image",
  "arguments": {
    "dataset_id": "REPLACE_WITH_AUTHORIZED_DATASET_ID",
    "document_id": "REPLACE_WITH_AUTHORIZED_DOCUMENT_ID",
    "chunk_id": "REPLACE_WITH_CHUNK_ID"
  },
  "min_results": 1
}
```

## 员工票据

本项目还提供下文的“一次性内存入口”，可由 WorkBuddy 在真实工具调用中提交票据，无需创建员工票据文件。文件方式仅适用于已有受控凭据输入的运维场景。

由有权操作的测试人员在服务器受限文件中保存有效票据。文件内容为 JSON 字符串数组，每个元素是一张票据；只保存原始票据，不加 `Bearer ` 前缀。不要把票据粘贴到聊天、命令行参数或提交文件。

文件须属于当前运行用户，权限为 `0600` 或更严格，且是普通文件。脚本拒绝符号链接、组/其他用户可访问的文件，以及重复票据。建议位置：`logs/workbuddy-load-input/tickets.json`，父目录权限 `0700`。脚本不会从业务日志中提取票据，也不会生成或刷新员工票据。

## 先做离线检查

```bash
.venv/bin/python test/load/workbuddy_load.py \
  --workload logs/workbuddy-load-input/workload.json
```

输出包含 `"mode": "dry_run"`、`"network_requests": 0`。加上 `--tokens-file` 也仍是离线检查；只有 `--execute` 会连接服务。

## 首轮：一个用户，少量请求

在低峰时段执行，测试期间观察原有用户访问。先用 30 秒、每秒 0.2 个请求的设置；三步握手加三个工具约需要 30 秒，工具较慢时可能覆盖不全，此时不会标记测试成功。

```bash
.venv/bin/python test/load/workbuddy_load.py \
  --workload logs/workbuddy-load-input/workload.json \
  --tokens-file logs/workbuddy-load-input/tickets.json \
  --execute --users 1 --duration 30 --rps 0.2 --max-requests 10
```

默认监控当前部署的四个用户级 systemd 服务：API、员工 MCP、任务执行和数据同步。任一服务未运行时拒绝开始。若部署服务名不同，用重复的 `--service <unit>` 显式列出要监控的服务。不能用不存在的服务跳过监控。

单用户试跑通过后，再运行 120 秒的单用户基线：

```bash
.venv/bin/python test/load/workbuddy_load.py \
  --workload logs/workbuddy-load-input/workload.json \
  --tokens-file logs/workbuddy-load-input/tickets.json \
  --execute --users 1 --duration 120 --rps 0.5 --max-requests 100
```

检查报告后再安排 `--users 1,3,5,10 --duration 120 --rps 1 --max-requests 600`，不会自动升级到更高负载。这里是最多 10 个活跃客户端、全局每秒最多 1 个请求，不等于 10 个持续在途请求。低速率、大量初始化或较慢的工具可能导致部分用户没进入工具阶段；报告会标记 `incomplete_stage`。

## 停止机制

资源每 2 秒采样。满足任一条件，停止新增请求并取消压测端正在等待的请求，不重启业务服务：

- 任一监控的原始服务进程退出，包括服务重启后换了进程；监控采样本身失败也会停止。
- 系统可用内存低于 4 GiB。
- 系统总体 CPU 使用率连续 3 次采样达到 85%。单个进程使用一个完整 CPU 核时可能显示约 100%，与系统总体百分比口径不同；报告同时记录各进程 CPU，便于发现单进程瓶颈。
- 任一请求超时、HTTP 错误（包括 401、403、429）、JSON-RPC 错误、MCP `isError`、返回格式异常、预期数据缺失或响应超过 8 MiB。
- 连续 3 个成功工具调用耗时均达到 15 秒。
- 达到请求总量上限，或收到 Ctrl+C / SIGTERM。

取消客户端等待不能保证撤销后端和第三方已经开始的工作。停止后仍需观察资源与现有用户体验恢复，不要立即重跑加压。

## 报告与判读

每次执行创建新的 `logs/workbuddy-load/<UTC时间戳>/`，目录 `0700`，文件 `0600`；可用 `--report-dir` 指定一个尚不存在的新目录。

- `requests.jsonl`：每次请求的时间、虚拟用户序号、工作负载项序号、工具名、HTTP 状态、耗时、成功/失败和固定错误分类。不会保存票据、请求参数、问题、文档正文或上游错误正文。
- `resources.jsonl`：主机 CPU、可用内存、测试进程与业务进程的 CPU/RSS、进程存活状态。
- `summary.json`：停止原因、请求总数、实际请求开始速率、每档覆盖情况，以及每个操作的成功样本数、失败数、成功请求耗时 P50/P95/P99。失败和取消单独计数，不纳入成功耗时分位数。

退出码：0 为离线检查通过，或全部指定档位完成；1 为配置/预检失败；2 为测试提前停止或覆盖不全。预算耗尽也属于提前停止。`completed` 只说明脚本完成指定负载并通过其检查，不是业务上线验收。

每档只有在所有虚拟用户至少成功调用一次工具，且所有工作负载项至少有一个成功样本时，才计为完成。先看停止原因与失败数，再看分位数。样本很少时 P95/P99 接近最大值，只作为基线记录；不要把单次低负载结果外推为 400 人容量结论。

同机运行时，压测进程会争用 CPU、内存和网络，而且本机调用未覆盖 WorkBuddy 到服务器的实际网络。报告保留测试进程消耗，后续容量验证应结合真实使用速率、外部服务配额以及少量真实 WorkBuddy 客户端验证。多员工身份隔离、撤权、停用验收独立执行，不在本轮业务负载中反复修改授权。

## 离线回归

```bash
.venv/bin/python -m pytest test/load/test_workbuddy_load.py -q
```

测试使用受控 HTTP 响应，不连接正在运行的 MCP、OneID、业务数据库或模型服务。

## 一次性内存入口

`test/load/workbuddy_load_entry.py` 是独立测试进程，提供临时 MCP 地址 `http://192.168.1.183:9397/mcp`。它只暴露 `start_controlled_load_test`，不修改正式 9396 服务，也不实现生产身份缓存。

启动前，运维人员创建当前用户独占的 `0600` 配置文件，绑定已授权员工的企业和 union ID 的 SHA-256，以及固定工作负载、报告目录和单次 `run_id`。union ID 哈希仅用于匹配测试员工，不是身份凭据。员工必须经现有 OneIDClient 在线验证；脚本随后对 9396 的每次实际业务调用仍走正式鉴权和实时数据库授权。

配置字段包括 `run_id`、`expected_union_hash`、`enterprise_id`、`workload`、`report_dir`、`host`、`port`、`allowed_peers`。本次入口固定使用 9397；来源仅允许环回和已观察到的网关 `10.10.78.93`，不信任客户端提供的转发地址。入口限制最多 5 个客户端、每档最多 60 秒、全局每秒最多 1 个 MCP 请求、总预算最多 240 个请求。默认 1、3、5 客户端各 60 秒，档间冷却 15 秒；握手计入限速。

确认用户场景为约 400 位员工日常分散使用后，提供固定的 `profile="distributed_peak"` 短时探测档：1、5、10 客户端各 45 秒，全局限速每秒 2 个 MCP 请求，总预算 300，档间冷却 15 秒。该档的四项负载设置不可单独覆盖，不提高原压测器的每秒 2 次硬上限，仍受同样的资源、鉴权错误、慢请求及票据窗口保护。它是预设探测负载，不能等同于已观测的 400 人生产峰值。

未加 `--serve` 时，仅检查本地配置，不监听、不请求 OneID：

```bash
.venv/bin/python -m test.load.workbuddy_load_entry --config logs/workbuddy-memory-entry/RUN/entry-config.json
```

实际启动时，通过用户级临时服务加载现有 WorkBuddy 应用配置环境文件，禁用 core dump。配置和命令行均不包含原始员工票据。默认等待用户启动最多一小时，未启动也会自动关闭；不得把未启动、超时、提前停止当作测试成功。

用户操作：

1. 在连接器管理端创建独立的临时测试连接器，指向 9397，复用现有员工认证及票据注入配置。普通自定义 MCP 地址未必具备员工票据注入，必须以实际调用验证。避免把共享的正式连接器切到测试入口。
2. 在 WorkBuddy 新会话中仅选择测试连接器，明确调用 `start_controlled_load_test` 并传入运维提供的 `run_id`。初始化、列出工具不会保存票据或启动负载；工具调用缺失或重复的票据头、其他员工/企业、错误 run_id、重复启动均被拒绝。
3. 返回 `state=accepted` 后可切回正式连接器。测试不需要 WorkBuddy 保持会话；临时服务结束后应删除或停用测试连接器。

票据只经内存交给压测程序，窗口最多 300 秒（包括身份验证），不生成 `tickets.json`。时间到、真实鉴权失效、资源保护触发或手动停止后，不再新增压测请求。程序清除持有票据的列表引用并退出；Python 字符串不提供物理内存覆写保证。不要开启请求头调试日志、抓取请求内容或制作进程转储。

`entry-status.jsonl` 记录等待、运行及终止状态；`entry-events.jsonl` 记录工具发现、启动调用及固定错误类别；`entry-http.jsonl` 只记录方法、固定路径、来源 IP、时间和状态码，不记录请求头、查询参数或正文。`load/` 下保留原压测报告，不保存请求参数、正文或票据。CLI 退出码 0 只代表已启动且完成，2 代表未启动/提前停止，1 代表配置或进程错误。

首轮工作负载不传 `page_size`，验证 MCP 默认每页 10 条的实际行为，使用多条固定问题轮换。压测器现支持语义检索 `page_size` 最大 100；30 条和 100 条应使用独立且更低负载的工作负载，不把大响应边界测试与默认场景的数据混算。精确耗时从发出 MCP HTTP 请求计至完整响应，不含限速排队和 WorkBuddy 模型生成文字的时间。客户端数量不等于实际在途请求数，每秒限速还可能使系统未达到饱和，报告不能直接证明 400 员工的容量。

入口、压测器、MCP 和 OneID 客户端的相关离线回归：

```bash
.venv/bin/python -m pytest test/load test/unit_test/mcp test/unit_test/api/auth/workbuddy -q
```
