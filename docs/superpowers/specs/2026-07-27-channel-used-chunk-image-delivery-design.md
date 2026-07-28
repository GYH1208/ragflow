# 通用证据解析引擎与企业微信图片发送设计

## 1. 背景

企业微信渠道当前通过最终回答中的 `[ID:n]` 引用标记定位
`reference.chunks[n]`，再发送该 Chunk 的图片。这条链路不可靠：

- 大模型可能写错、漏写或重复写入 `[ID:n]`。
- `[ID:n]` 是本轮数组下标，不是稳定的 Chunk ID。
- 回答可能综合多个 Chunk，但引用标记只覆盖其中一部分。
- 相关带图 Chunk 可能已经进入检索候选，却没有出现在最终引用中。
- 模型不知道候选 Chunk 是否包含图片，文字判断可能与后端实际图片数据矛盾。

图片发送不应继续依赖模型生成的引用格式。系统需要一个通用的证据解析能力，
在最终回答生成并发送后，判断哪些候选 Chunk 能直接支撑回答，并输出稳定的
`used_chunk_ids`。

## 2. 第一版目标

1. 文字回答发送成功后才启动证据解析，不增加用户等待文字的时间。
2. 从当前检索候选中找出能够直接支撑最终回答的真实 Chunk。
3. 使用稳定的 Chunk ID 输出 `reference.used_chunk_ids`。
4. 企业微信只发送 `used_chunk_ids` 对应 Chunk 中的图片。
5. 同一回答使用多个带图 Chunk 时，发送全部正确图片。
6. 图片按答案首次使用顺序发送，相同 `image_id` 只发送一次。
7. 匹配不确定时只发文字，不发送猜测图片。
8. 证据结果可被问答分析等后续功能复用。
9. Web 聊天现有引用展示逻辑保持不变。

## 3. 第一版非目标

- 不重新生成、改写、截断或替换文字回答。
- 不让生成式大模型直接决定需要发送哪些图片。
- 不分析图片像素内容。
- 不使用 OCR、图片描述模型或视觉模型校验图片。
- 不补充检索当前候选以外的 Chunk。
- 不重新解析或重新索引知识库。
- 不重新计算或写入 Chunk embedding。
- 不新增数据库表或数据库列。
- 不修改 Web 聊天现有 `[ID:n]` 引用行为。
- 不改变来源文件的现有发送逻辑。

第一版中“正确图片”的定义是：图片所属 Chunk 能直接、可信地支撑最终回答
中的至少一个业务事实片段。系统不宣称能够读取模型内部注意力或逐 Token
来源。

## 4. 核心原则

### 4.1 文字优先

执行顺序必须是：

```text
生成最终回答
  ↓
保存回答与原始 reference
  ↓
发送清理后的文字及现有来源文件（不含图片）
  ↓
确认文字发送成功
  ↓
启动证据解析
  ↓
保存 used_chunk_ids
  ↓
单独发送全部匹配图片
```

证据解析不得在文字和现有来源文件发送之前执行。首包沿用现有来源文件选择与
发送逻辑，但不再夹带图片。解析超时、异常、无匹配、保存失败、图片读取失败或
渠道发送失败，都不能撤回、替换或重新发送文字。

### 4.2 展示引用与证据结果解耦

系统分别保留：

- `answer`：原始业务回答。
- `[ID:n]`：现有 Web 引用展示格式。
- `reference.used_chunk_ids`：后端证据解析选中的稳定 Chunk ID。

企业微信图片发送只使用 `used_chunk_ids`，不扫描 `[ID:n]` 选择图片。

### 4.3 准确率优先

第一版优先降低错误图片率：

- 低置信度时只发文字。
- 正确 Chunk 不在当前候选中时不发送相似图片替代。
- reranker 不可用或超时时不降级为宽松匹配。
- 检索排名不能单独作为“回答使用了该 Chunk”的结论。

### 4.4 通用引擎、单一首发消费者

证据解析引擎不依赖企业微信。第一版由企业微信渠道在文字发送后调用；后续
问答分析可以直接读取保存的 `used_chunk_ids`，其他渠道也可以复用相同接口。

## 5. 数据结构

继续使用现有 `Conversation.reference` JSON，不做数据库迁移。

每轮 reference 新增两个键：

```json
{
  "message_id": "message-1",
  "chunks": [
    {
      "id": "36ba21ce61e20e31",
      "content": "考勤异常处理路径……",
      "image_id": "bucket-36ba21ce61e20e31"
    }
  ],
  "used_chunk_ids": [
    "36ba21ce61e20e31"
  ]
}
```

- `message_id` 用于把异步返回的证据结果写回正确问答轮次。
- `used_chunk_ids` 使用真实 Chunk ID，不使用本轮数组下标。
- `used_chunk_ids` 可以包含无图片 Chunk；渠道发送时再过滤非空 `image_id`。
- 详细匹配分数只进入日志和进程内结果，不长期写入 Conversation。

### 5.1 通用输入类型

```python
@dataclass(frozen=True)
class EvidenceChunk:
    chunk_id: str
    content: str
    image_id: str | None
    vector: list[float]
```

### 5.2 通用输出类型

```python
@dataclass(frozen=True)
class EvidenceMatch:
    segment_index: int
    chunk_id: str
    hybrid_score: float
    rerank_score: float


@dataclass(frozen=True)
class EvidenceResolution:
    used_chunk_ids: list[str]
    matches: list[EvidenceMatch]
    unmatched_segments: list[int]
    status: Literal["resolved", "no_match", "error"]
    duration_ms: float
```

## 6. 组件边界

### 6.1 通用证据解析引擎

新增 `rag/nlp/evidence.py`，职责是：

- 处理答案副本。
- 拆分有效事实片段。
- 批量计算片段 embedding。
- 使用现有 Chunk 向量进行混合初排。
- 使用 reranker 复核少量候选。
- 汇总稳定的 `used_chunk_ids`。
- 返回结构化匹配结果。

该模块不得：

- 导入企业微信渠道实现。
- 读取或写入 Conversation。
- 发送文字、图片或来源文件。
- 修改传入的原始回答或 Chunk。

### 6.2 应用层证据服务

新增 `api/db/services/evidence_service.py`，职责是：

- 根据聊天应用取得 embedding 和 reranker 模型。
- 通过现有 `fetch_chunk_vectors()` 批量读取候选 Chunk 已有向量。
- 调用通用证据解析引擎。
- 将结果转换为 JSON 可保存的数据。
- 定向更新指定 `conversation_id + message_id` 的 reference。
- 记录耗时、匹配结果和降级原因。

### 6.3 企业微信渠道

调整 `api/channels/bootstrap.py`，职责是：

- 保存并发送当前文字回答。
- 仅在文字发送成功后调用证据服务。
- 仅在候选中至少存在一个 `image_id` 时启动证据解析。
- 根据 `used_chunk_ids` 收集图片。
- 按首次使用顺序去重并发送全部图片。

来源文件继续使用现有逻辑，本次只替换图片选择依据。

## 7. 证据匹配算法

### 7.1 输入

```python
async def resolve(
    question: str,
    answer: str,
    chunks: list[EvidenceChunk],
    embedding_model,
    rerank_model,
    config: EvidenceConfig,
) -> EvidenceResolution:
    ...
```

输入只包含当前问答和当前检索候选，不补充检索。

### 7.2 处理答案副本

证据引擎可以从答案副本中清除 `[ID:n]` 等内部引用标记，但不得修改原始回答。

以下内容不进入事实匹配：

- 空行。
- 纯 Markdown 标记。
- 只有“来源”“参考”“路径来源”等字样的占位文本。
- “知识库未找到图片”“建议联系人事获取截图”等知识可用性描述。
- 长度不足且没有业务事实的标题或过渡句。

“迟到不能补卡”“请假不存在迟到情况”等业务否定事实必须保留，不能因为包含
否定词而整体过滤。

### 7.3 分段

- 按自然句、换行和列表项拆分答案。
- “操作步骤”“注意事项”等短标题与紧随其后的业务内容合并。
- 保留答案中的原始顺序，并为每个有效片段分配稳定的 `segment_index`。
- 用户问题仅用于补充过短片段的上下文，不替代片段本身作为证据查询。

### 7.4 批量初排

1. 一次性批量计算全部事实片段的 embedding。
2. 并行读取全部候选 Chunk 的已有向量。
3. 使用聊天应用当前的关键词/向量权重计算混合分数。
4. 每个片段保留前 3 个候选进入 reranker。
5. Chunk 向量缺失或维度错误时，该 Chunk 不参与匹配。

Chunk embedding 直接复用知识库索引中的已有结果，不重新生成或写回。

### 7.5 reranker 复核

- 将所有“事实片段—初排候选”组合批量提交给聊天应用已配置的 reranker。
- 同时检查混合分数、reranker 分数以及候选之间的分差。
- 只有同时通过可信条件的候选才能进入 `used_chunk_ids`。
- 每个事实片段独立判断，不能因为其他片段已经匹配而提前停止。
- 多个 Chunk 分别支撑回答时允许全部保留。

`EvidenceConfig` 至少包含：

```python
@dataclass(frozen=True)
class EvidenceConfig:
    min_hybrid_score: float
    min_rerank_score: float
    min_score_margin: float
    shortlist_size: int = 3
    timeout_seconds: float = 10.0
```

三个可信阈值是证据引擎内部配置，不复用知识库的宽松召回阈值。上线默认值必须
通过人工标注回归集标定后固化；在没有样本数据时不宣称虚假的准确率。

### 7.6 汇总

- 按 `segment_index` 从小到大处理匹配。
- 同一片段内按可信分数从高到低排列。
- 相同 `chunk_id` 只保留第一次出现。
- 未找到可信来源的片段写入 `unmatched_segments`。
- 没有任何可信匹配时返回 `status="no_match"` 和空 `used_chunk_ids`。

## 8. 图片选择与发送

图片选择流程：

```text
used_chunk_ids
  ↓
在当前 reference.chunks 中按真实 ID 查找
  ↓
过滤空 image_id
  ↓
按 Chunk 首次使用顺序排列
  ↓
相同 image_id 去重
  ↓
全部发送
```

规则：

1. 不使用 `[ID:n]` 选择图片。
2. 不发送只进入检索候选但未进入 `used_chunk_ids` 的图片。
3. 多个可信 Chunk 均带图时发送全部图片。
4. 图片不存在或单张发送失败时继续处理剩余图片。
5. 图片发送失败不重试、不撤回文字。

## 9. 定向持久化与并发

渠道收到用户问题时，在本轮 reference 中写入对应 `message_id`。

证据解析完成后调用：

```python
ConversationService.update_reference_evidence(
    conversation_id: str,
    message_id: str,
    used_chunk_ids: list[str],
) -> bool
```

该方法必须：

1. 在数据库事务中重新读取最新 Conversation。
2. 根据 `message_id` 找到目标 reference。
3. 只更新目标 reference 的 `used_chunk_ids`。
4. 保留其他消息、reference 和同时到达的新问答。
5. 找不到目标 reference 时返回失败，不使用 `reference[-1]` 猜测。

保存失败时，内存中的证据结果仍可用于本次图片发送，但必须记录持久化失败。

## 10. 异常与降级

| 情况 | 行为 |
| --- | --- |
| 文字发送失败 | 不启动证据解析 |
| 回答为空 | 不启动证据解析 |
| 没有检索候选 | 不启动证据解析 |
| 候选中没有图片 | 不启动证据解析 |
| embedding 不可用 | 记录原因，只保留已发送文字 |
| Chunk 向量读取失败 | 记录原因，只保留已发送文字 |
| reranker 不可用或超时 | 不做宽松降级，不发送图片 |
| 证据解析超过 10 秒 | 终止本轮解析，不发送图片 |
| 没有可信 Chunk | 保存空 `used_chunk_ids`，不发送图片 |
| `used_chunk_ids` 保存失败 | 记录异常，仍可使用内存结果发送图片 |
| 单张图片不存在或发送失败 | 记录 Chunk ID/image ID，继续发送剩余图片 |

## 11. 日志与可观测性

每轮证据解析记录：

- `conversation_id`
- `message_id`
- 用户问题
- 候选 Chunk ID
- 有效答案片段数量
- 每个片段的初排 Chunk ID 和分数
- 每个片段的 reranker Chunk ID 和分数
- 最终 `used_chunk_ids`
- 实际发送的 `image_id`
- 解析总耗时
- 未发送图片的明确原因

日志不得输出：

- 图片二进制。
- 模型密钥或渠道凭据。
- 与本轮证据判断无关的额外用户隐私数据。

## 12. 测试设计

### 12.1 分段测试

- 引用标记只从答案副本删除，原始回答保持不变。
- 短标题与后续业务内容正确合并。
- 纯 Markdown、空行和来源占位被忽略。
- 知识可用性描述不参与匹配。
- 业务否定事实继续参与匹配。

### 12.2 证据匹配测试

- 正确带图 Chunk 排名不是第一时仍能选中。
- 检索第一名带图但与回答无关时不选中。
- 多个 Chunk 分别支撑回答时全部进入 `used_chunk_ids`。
- 多个 Chunk 使用相同图片时只发送一次。
- 多个相关 Chunk 使用不同图片时全部发送。
- 第一、第二名过于接近且无法确认时不选中。
- reranker 失败或超时时不降级猜测。
- 正确 Chunk 不在当前候选中时不发送相似图片。

### 12.3 持久化与并发测试

- `used_chunk_ids` 写入指定 `message_id` 对应的 reference。
- 用户连续发送两条消息时不会写到错误轮次。
- 历史 reference 没有 `message_id` 时不误更新。
- 保存失败不影响已经发送的文字。
- 详细匹配分数不会写入 Conversation。

### 12.4 企业微信时序测试

必须验证：

```text
保存回答
→ 发送文字及现有来源文件（不含图片）
→ 启动证据解析
→ 保存 used_chunk_ids
→ 发送图片
```

覆盖：

- 慢速证据解析不会延迟文字发送。
- 解析异常时文字仍只发送一次。
- 没有图片候选时不调用 embedding 或 reranker。
- 多张正确图片在文字之后全部发送。
- 单张图片发送失败后继续发送剩余图片。
- Web 聊天引用行为保持不变。

### 12.5 回归样本

至少覆盖：

- “忘记打卡了怎么办”
- “补卡路径是什么”
- “考勤异常在哪里处理”
- “有没有处理考勤异常的流程图”
- 候选中包含无关带图 Chunk
- 正确带图 Chunk 排名不是第一
- 一个回答需要多张图片
- 正确 Chunk 不在当前候选中

## 13. 验收标准

1. 文字发送发生在证据引擎启动之前。
2. 启用证据引擎前后，企业微信可见文字内容完全一致。
3. 企业微信图片选择不再依赖 `[ID:n]`。
4. `used_chunk_ids` 全部是当前候选中的真实 Chunk ID。
5. 所有可信带图 Chunk 对应的图片均被发送并去重。
6. 低置信度样本只发文字。
7. 回归集中不得出现已知错误图片。
8. 证据或图片异常不影响文字回答。
9. Web 聊天引用行为保持不变。
10. 不重新解析、重新索引或重新生成 Chunk embedding。
11. 不新增数据库表或数据库列。
12. 第一版不补充检索、不使用 OCR 或视觉模型。

## 14. 准确度评估

上线前从真实企业微信日志建立人工标注回归集，覆盖单 Chunk、多 Chunk、无关带图
候选、正确图片非第一名、无可信来源和多图片回答。

评估：

- `used_chunk precision`：选中的 Chunk 是否直接支撑回答。
- `image recall`：回答使用带图 Chunk 时是否成功发送对应图片。
- `wrong image rate`：发送图片中与回答无关的比例。

第一版以降低 `wrong image rate` 为首要目标。生产阈值在回归集上标定后固定为
内部默认值；低置信度时允许降低图片召回率，不允许用相似图片补位。
