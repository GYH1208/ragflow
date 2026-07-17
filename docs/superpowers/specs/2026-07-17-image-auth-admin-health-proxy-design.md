# 图片鉴权与管理员健康检查修复设计

## 背景

当前存在两个互不依赖的问题：

1. 普通知识库解析页面使用原生 `<img>` URL 加载 Chunk 图片。图片接口要求鉴权，但前端只在分享页面存在 `shared_id` 时通过 `fetch` 附加 Authorization，因此没有可用 Session Cookie 时图片请求返回 401。
2. 后台启动脚本清空了 `NO_PROXY`，却保留了服务器环境中的 `ALL_PROXY`。管理员服务使用 `requests` 探测本机 RAGFlow 和 MinIO 时，请求被发往代理并得到 502，页面将正常服务误报为超时。

## 目标

- 已登录用户在普通页面和分享页面都能加载有权限访问的文档图片。
- 管理员服务对 `localhost`、`127.0.0.1` 和 `::1` 的健康检查不经过外部代理。
- 不修改图片存储、VLM、Chunk 数据结构或管理员状态判定协议。

## 方案比较

### 方案 A：在现有边界上做最小修复（采用）

- 前端检测到 Authorization 时始终使用现有的 `fetchDocumentImage`，通过鉴权请求生成 Blob URL；没有 Authorization 时保留直接 URL，以兼容公开或 Cookie 会话场景。
- 启动脚本保留外部 `ALL_PROXY`，但明确设置本地地址的 `NO_PROXY`/`no_proxy`，让本地健康检查直连。

优点是改动小、保留现有缓存逻辑，也不会破坏外部模型访问可能依赖的代理。

### 方案 B：移除图片接口鉴权

前端无需改动，但任何知道图片 ID 的请求都可能读取图片，扩大数据泄露风险，因此不采用。

### 方案 C：健康检查代码禁用 requests 环境代理

可只修复管理员探针，但其他本机 HTTP 调用仍可能被相同环境配置影响；同时会把部署环境问题固化到业务代码中，因此不作为首选。

## 详细设计

### 图片请求

`useDocumentImageUrl` 按以下规则选择加载方式：

- Authorization 非空：调用 `fetchDocumentImage(directUrl, authorization)`，复用当前按 Token 和 URL 建立的 Blob 缓存。
- Authorization 为空：直接返回 `directUrl`，允许浏览器携带 Cookie 或访问公开场景。
- 请求失败：不生成损坏的 Blob URL，保持当前空地址行为。

该变化不再以 `shared_id` 作为是否添加 Authorization 的条件。

### 本地代理绕过

启动脚本加载 `.env` 后设置：

```bash
export NO_PROXY="localhost,127.0.0.1,::1"
export no_proxy="$NO_PROXY"
```

不清除 `ALL_PROXY`，避免影响需要代理访问的外部模型服务。现有 HTTP/HTTPS 代理清理逻辑保持不变。

## 测试

- 前端单元测试验证：普通登录页面存在 Authorization、但没有 `shared_id` 时，图片通过带 Authorization 的 fetch 加载。
- 前端单元测试验证：没有 Authorization 时仍使用直接图片 URL。
- 启动脚本测试验证：加载脚本后的 `NO_PROXY` 和 `no_proxy` 必须包含三个本地地址，且不得清除已有 `ALL_PROXY`。
- 回归验证管理员探针：RAGFlow `/api/v1/system/ping` 与 MinIO `/minio/health/live` 均返回 alive。

## 非目标

- 不配置或调用 VLM。
- 不迁移或重新解析现有文档图片。
- 不修改管理员页面的刷新频率或状态展示。
- 不处理历史 Chunk 指向已删除 MinIO 对象的独立数据问题。
