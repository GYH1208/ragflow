# 百炼聊天模型目录更新（2026-11-05）

`Tongyi-Qianwen` 使用百炼接口。聊天模型下拉列表由
`conf/llm_factories.json` 和租户自定义记录合并生成，不会随着百炼上线模型自动更新。
本次在该供应商下新增 10 个聊天模型，保留已有条目和其他供应商配置：

- `qwen3.8-max`、`qwen3.8-flash`
- `qwen3.7-max`、`qwen3.7-plus`、`qwen3.7-flash`
- `qwen3.6-plus`、`qwen3.6-flash`
- `deepseek-v4.1-flash`、`deepseek-v4-pro`、`deepseek-v4-flash-0731`

模型 ID 已通过现有百炼实例的 `/compatible-mode/v1/models` 接口核对。
接口返回模型不等于已经完成生成调用测试，也不能保证所有租户、地域均有调用权限。
上下文容量和工具调用能力按下列官方资料填写。`max_tokens` 在此目录中表示上下文
容量，不是要求模型输出的长度。聊天类型沿用已有 Qwen3.5 目录约定；本次不增加
视觉模型路由。

- [Qwen3.8 Max](https://help.aliyun.com/zh/model-studio/qwen3-8-max)
- [Qwen3.8 Flash](https://help.aliyun.com/zh/model-studio/qwen3-8-flash)
- [Qwen3.7 Max](https://help.aliyun.com/zh/model-studio/qwen3-7-max)
- [Qwen3.7 Plus](https://help.aliyun.com/zh/model-studio/qwen3-7-plus)
- [Qwen3.7 Flash](https://help.aliyun.com/zh/model-studio/qwen3-7-flash)
- [Qwen3.6 Plus](https://help.aliyun.com/zh/model-studio/qwen3-6-plus)
- [Qwen3.6 Flash](https://help.aliyun.com/zh/model-studio/qwen3-6-flash)
- [DeepSeek V4.1 Flash](https://help.aliyun.com/zh/model-studio/deepseek-v4-1-flash)
- [DeepSeek V4 Pro](https://help.aliyun.com/zh/model-studio/deepseek-v4-pro)
- [百炼 DeepSeek 接口](https://docs.bailian.console.aliyun.com/zh/model-studio/deepseek-api)

`thinking: true` 仅声明模型支持思考，不代表应用已开启思考。当前 Qwen3 调用策略仍
传入 `enable_thinking=false`；调整调用策略需要独立验证，不包含在目录更新中。

配置在 API 服务启动时加载，发布后须重新启动 API 服务并刷新模型设置页面。
用户仍需在具体问答应用中选择模型；本次不修改账号默认模型或现有应用模型。
回退此目录提交后重启 API 服务即可恢复原列表；若已选择新增模型，应先切回原来
可用的模型，再回退目录。
