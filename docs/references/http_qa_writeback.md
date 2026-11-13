# 外部系统 Q&A 写入接口

通过普通 HTTP 接口把一组已确认的问答写入知识库。此接口没有注册为 MCP 工具。

## 请求

```http
POST /api/v1/datasets/{dataset_id}/documents/{document_id}/qa
Authorization: Bearer <完整 API Key>
Content-Type: application/json
```

`dataset_id` 为目标知识库 ID。`document_id` 为该知识库下已启用、采用 Q&A
解析方式且不在解析中的文档 ID。建议预先建立一个专用空文档，避免后续重新解析
普通上传文件时清除写入的问答。接口不创建知识库或文档。

只接受现有完整 API Key；只读检索 Key、员工 OneID、登录会话不能用于本接口。
完整 Key 可设置知识库范围；非空 `allowed_dataset_ids` 仅允许回写所列知识库，空数组表示保留全部可访问权限。范围检查在回写、关键词抽取及向量化之前执行。只读检索 Key 仍不能回写。
有权限的团队成员使用未限定范围的完整 Key 调用时，模型和索引均使用知识库所有者的配置。

```json
{
  "question": "电脑无法开机、无任何指示灯，应该如何处理？",
  "answer": "适用范围：企业办公环境。\n\n处理步骤：\n1. 确认插座有电。\n2. 检查电源线和适配器。\n\n升级条件：仍无反应时，报修硬件。",
  "source_system": "it_service",
  "external_id": "IT-0001",
  "keyword_top_n": 5
}
```

| 字段 | 类型 | 必填 | 约束 |
| --- | --- | --- | --- |
| question | string | 是 | 非空，最多 2000 个字符 |
| answer | string | 是 | 非空，最多 20000 个字符，支持多行 |
| source_system | string | 是 | 最多 64 个字符，仅 ASCII 字母、数字、下划线、短横线 |
| external_id | string | 是 | 最多 128 个字符，大小写敏感，使用业务系统的稳定唯一 ID |
| important_keywords | string[] | 否 | 最多 20 项，每项最多 100 个字符；清理空白、去重 |
| keyword_top_n | integer | 否 | 自动抽取数量上限，默认 5，范围 1～10，不接受布尔值或数字字符串 |

字符串清理首尾空白，CRLF/CR 统一为 LF，不总结或截断正文。不接受 NUL 字符。
问题中不能包含内部保留的 TAB＋`回答：` / TAB＋`Answer:` 分隔序列。
超过配置的 Embedding 模型实际输入上限的问题会返回 400，不静默截断向量输入。

## 处理和关键词

处理顺序：校验及鉴权 → 幂等检查 → 关键词 → 问题向量化 → 索引写入 → 计数提交。
不进行文件解析、OCR、切块或父子分块。问题和完整答案作为一个 Q&A chunk 保存。

- 非空 `important_keywords`：直接使用传入关键词，不调用抽取模型；`keyword_top_n` 不影响结果。
- 未传关键词、空数组或清理后为空：自动抽取，输入完整问答，实际数量可能少于上限。
- 模型选择：文档 `parser_config.llm_id`，其次知识库配置，最后租户默认聊天模型。
- 抽取过程等待最多 10 秒。异常、超时、空输出会降级为空关键词，继续分词、向量化和入库。
- 向量使用目标知识库的 Embedding 模型，仅编码问题。完整答案及关键词另行存储。
- 即使抽取失败，问题全文匹配和向量检索仍然可用。

## 成功响应

HTTP 200，`code=0`：

```json
{
  "code": 0,
  "data": {
    "chunk_id": "8feaf91a087cb16d71a2b881683adf42",
    "dataset_id": "<dataset_id>",
    "document_id": "<document_id>",
    "source_system": "it_service",
    "external_id": "IT-0001",
    "created": true,
    "important_keywords": ["无法开机", "电源", "指示灯"],
    "keyword_status": "generated"
  }
}
```

`keyword_status` 为 `generated`（自动抽取）、`provided`（调用方提供）或 `failed`
（抽取失败，关键词为空）。`failed` 不代表问答写入失败，不需要为补关键词重复提交。
接口不修改调用方提交的业务答案，也不判断答案是否正确或是否已通过业务审核。

## 幂等和重试

身份范围：知识库所属租户＋知识库 ID＋文档 ID＋source_system＋external_id。
换 API Key 不改变幂等身份。不同业务 ID 即使问答相同也允许分别创建。

相同身份、相同规范化请求返回原 chunk_id，`created=false`，不重复抽词、向量化或
增加计数。手动关键词的顺序和重复项不影响比较；自动抽取时 keyword_top_n 属于请求内容。
相同身份但内容改变返回 409，不覆盖已有内容；修改问答应调用现有 chunk 更新接口。

进程异常或超时后，使用相同身份和相同请求重试。已完成的关键词/向量会从持久记录恢复，
索引已接收但响应丢失时不会重复计数。并发重复请求返回处理中；异常退出的处理租约约
5 分钟后可接管。关键词或向量尚未准备完成时发生中断，重试可能需要再次调用模型。

索引写入阶段锁定幂等记录、目标文档和知识库，写入后回读确认再提交计数，避免和
重新解析或租约接管交错。此阶段客户端取消请求也会等待已开始的写入结束；它可能
已经成功提交，调用方应通过相同请求确认结果。

成功写入后手动删除 chunk 或重新解析文档，再提交原身份会返回 `CHUNK_REMOVED`，
不会静默恢复已删除内容。确需重新新增时使用新的业务版本 ID。

## 错误响应

```json
{
  "code": 503,
  "message": "Embedding service is temporarily unavailable.",
  "data": {
    "error_code": "EMBEDDING_UNAVAILABLE",
    "retryable": true
  }
}
```

| HTTP | code | error_code | 处理 |
| --- | --- | --- | --- |
| 400 | 101 | INVALID_ARGUMENT | 修正参数、JSON 或问题长度 |
| 400 | 101 | INVALID_DOCUMENT_TYPE | 使用 Q&A 文档 |
| 401 | 401 | 鉴权层标准返回 | 检查 Key，不盲目重试 |
| 403 | 403 | 鉴权层标准返回 | 只读 Key 不允许写入 |
| 404 | 404 | TARGET_NOT_FOUND | 目标不存在、未启用或不可访问 |
| 409 | 409 | IDEMPOTENCY_CONFLICT | 相同身份内容不同，或未完成写入的 chunk 已被外部修改 |
| 409 | 409 | CHUNK_REMOVED | 原成功写入的 chunk 已删除 |
| 409 | 106 | REQUEST_PROCESSING | 稍后用相同请求重试 |
| 409 | 106 | DOCUMENT_BUSY | 等待文档解析结束后重试 |
| 503 | 503 | EMBEDDING_UNAVAILABLE | 向量服务异常，稍后重试 |
| 503 | 503 | STORAGE_UNAVAILABLE | SQL/索引异常或写入结果不确定，稍后重试 |

401/403 复用现有鉴权层，其 data 可能为空；调用方首先检查 HTTP 状态及 code。
网络超时不代表没有写入。可按 1、2、5 秒退避重试，达到业务侧重试上限后保留记录补偿。
不要通过更换 external_id 重试不确定的请求。

## 调用及部署

将示例请求保存为 qa.json：

```bash
curl -X POST \
  'https://<host>/api/v1/datasets/<dataset_id>/documents/<document_id>/qa' \
  -H 'Authorization: Bearer <API_KEY>' \
  -H 'Content-Type: application/json' \
  --data-binary @qa.json
```

部署新版后正常启动后端，现有数据库初始化流程会创建新增的 `qa_writeback` 表；
不需要改变 MCP 配置。若数据库账号没有建表权限，须先由部署流程创建该表。
只有索引写入及计数提交成功才返回成功；实际召回仍受助手绑定、启用状态和检索配置影响。
