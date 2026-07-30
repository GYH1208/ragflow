# 企业微信 WebSocket 流式回复设计

## 目标

把企业微信智能机器人长连接模式从“等待完整 RAG 回答后主动发送一条消息”调整为原位更新的流式回复。用户发出问题后先看到处理状态，模型生成正文后持续看到增量内容，最终消息仍保留现有引用清理、引用图片和引用原文件能力。

## 范围

- 仅企业微信 WebSocket 模式启用流式回复。
- 企业微信 Webhook 和其他聊天 Channel 保持非流式行为。
- 复用 RAGFlow 已有的 `async_chat(..., stream=True)` 生成器，不修改模型适配层。
- 不把模型的思考过程发送到外部聊天渠道。
- 流式正文使用 Markdown，最终引用图片和原文件继续沿用现有上传与发送链路。

## 协议设计

企业微信的长连接传输和流式消息是两层不同概念。当前实现通过 `aibot_send_msg` 主动发送完整 Markdown；流式回复改用 `aibot_respond_msg`，并复用入站 `aibot_msg_callback` 的 `headers.req_id`。

每次回答生成一个稳定的 `stream.id`。同一回答的所有帧使用相同的入站 `req_id` 和 `stream.id`：

```json
{
  "cmd": "aibot_respond_msg",
  "headers": {"req_id": "<入站 req_id>"},
  "body": {
    "msgtype": "stream",
    "stream": {
      "id": "<本次回答 stream id>",
      "content": "<截至当前的完整正文>",
      "finish": false
    }
  }
}
```

`content` 是全量替换内容，而不是单次 delta。最后一帧设置 `finish=true`。

## 组件与数据流

### Channel 公共能力

公共 `Channel` 增加默认关闭的 `supports_streaming` 能力标记，以及默认回退到普通 `send` 的 `send_stream` 方法。这样启动层不依赖企业微信具体类型，其他 Channel 无须改动。

企业微信 WebSocket 实例开启该能力并覆盖 `send_stream`：

1. 校验原始回复 `req_id`、`stream.id` 和正文。
2. 通过 `aibot_respond_msg` 发送全量流式正文。
3. 最终帧成功后，再按现有顺序上传并发送引用图片、引用原文件。

### RAG 回答处理

Channel 启动层按能力选择生成模式：

- 不支持流式：保持现有 `async_chat(..., stream=False)` 路径。
- 支持流式：先发送“正在查询知识库，请稍候…”占位帧，再消费 `async_chat(..., stream=True)`。

流式过程中累计可见正文，并忽略 `<think>` 区间及思考状态事件。每次对外发送前移除已完成的 `[ID:n]` 引用标记，避免企业微信短暂显示内部引用编号。最终事件携带引用信息时，使用累计正文完成引用图片和原文件解析，然后发送 `finish=true`。

`structure_answer` 仍处理每个生成事件，保证会话内容和最终引用正常落库；会话只在回答结束时统一更新数据库。

### WebSocket 请求关联

现有 `_ws_request` 总是生成新的请求 ID。它将增加可选的显式 `request_id` 参数：

- 主动发送、素材上传继续自动生成请求 ID。
- `aibot_respond_msg` 显式使用入站回调请求 ID。
- 同一流的各帧串行发送和等待 ACK，因此待处理请求表中不会同时出现相同 ID。

## 错误处理

- 首个流式占位帧发送失败：记录异常并回退到现有完整消息发送路径。
- 生成过程中失败：尽力用相同流发送 `finish=true` 的错误消息；若 WebSocket 回复上下文失效，再回退到主动发送。
- 最终正文为空：用明确的兜底提示结束流，避免消息长期停留在“处理中”。
- 引用图片或文件失败：只记录当前附件错误，不影响已经完成的文字流。
- WebSocket 断线：复用现有待处理请求失败和重连机制。

## 测试设计

单元测试覆盖：

1. 显式 `request_id` 被原样写入 WebSocket 帧并能完成 ACK 关联。
2. 企业微信流式帧使用 `aibot_respond_msg`、稳定 `stream.id`、全量 `content` 和正确的 `finish`。
3. 最终帧之后才发送引用附件。
4. Channel 启动层对企业微信使用 `stream=True`，按 delta 累积全量正文，并以 `finish=true` 结束。
5. 思考内容不对外发送。
6. 非流式 Channel 保持原有一次性发送行为。
7. 流式生成异常能够结束或回退，不留下未完成流。

## 验收标准

- 用户发送消息后，企业微信在等待 RAG 检索和模型首 token 期间显示处理状态。
- 正文生成后在同一消息气泡中持续更新，而不是产生多条聊天消息。
- 最终消息正文与原非流式答案一致，不包含思考过程和引用编号。
- 引用图片、原文件和会话记录保持现有行为。
- 企业微信 Webhook 与其他 Channel 行为不变。
