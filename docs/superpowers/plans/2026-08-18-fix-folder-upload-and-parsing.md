# 修复文件夹重复显示与解析提交计划

> 执行方式：在当前会话内按测试驱动方式逐项实现，每完成一项立即运行对应测试。

## 目标

修复知识库文件夹上传时偶发重复创建/显示、目录切换后上传落到错误父目录，以及上传成功后解析任务未可靠提交的问题；同时恢复旧版 `.doc` 文件依赖的 Tika/Java 运行环境。

## 已确认的根因

1. `KnowledgeFileBrowser` 使用查询响应中的 `parent_folder.id` 作为上传目标。目录状态已经变化但新查询尚未返回时，上传仍可能携带旧目录 ID。
2. 上传回调没有单次提交保护，也没有在完成后刷新知识库目录查询；快速重复提交或陈旧缓存会造成重复/错误显示。
3. `FileService.ensure_kb_folder_path` 采用“查询后插入”，并发请求可能同时判断目录不存在并各自插入。
4. 自动解析调用没有被等待；大批文件提交失败时，上传对话框仍会关闭，且知识库列表不会反映最新解析状态。
5. 当前任务执行进程找不到 Java，Python Tika 无法启动，因此旧 `.doc` 文件全部失败；其他格式仍在处理，但单进程存在队列积压。

## 实施步骤

### 1. 前端目录目标与上传刷新

涉及文件：

- `web/src/pages/dataset/dataset/knowledge-file-browser.tsx`
- `web/src/pages/dataset/dataset/use-upload-document.ts`
- `web/src/pages/dataset/dataset/__tests__/knowledge-file-browser.test.tsx`
- 新增 `web/src/pages/dataset/dataset/__tests__/use-upload-document.test.tsx`

先增加失败测试，覆盖：

- 目录状态变化时立即把新的 `folderId` 传给上传 Hook，不等待旧查询响应。
- 同一次上传尚未结束时忽略第二次提交。
- 上传成功后等待自动解析，并调用目录刷新回调。
- 自动解析失败时不静默关闭上传对话框。

随后实现：

- 以本地 `folderId` 为上传与新建目录的权威目标，根目录仍回退到查询返回的根 ID。
- 在上传 Hook 内增加进行中引用锁，并把完整提交阶段纳入 loading 状态。
- 等待解析提交成功后再关闭对话框，并在上传完成后刷新当前目录。

### 2. 后端目录创建并发保护

涉及文件：

- `api/db/services/file_service.py`
- `test/unit_test/api/db/services/test_file_service_upload_document.py`

先增加失败测试，模拟两个请求针对同一父目录创建相同路径，确认最终只复用一个目录。随后将路径检查与创建放入数据库事务，并锁定当前父目录记录；每一级目录都使用锁定读取，避免并发“先查后插”。

### 3. 恢复旧版 DOC 解析运行环境

操作项：

- 在当前用户目录安装兼容 Tika 2.6.0 的 OpenJDK 17 运行时。
- 创建持久化的用户级解析服务单元，显式设置 `JAVA_HOME`、`PATH` 和 `PYTHONPATH`。
- 停止旧的临时解析单元，启动并启用持久化单元。

验证项：

- `java -version` 成功。
- Tika 能在解析服务账户下启动并读取一份真实 `.doc`。
- 任务执行日志不再出现 `Unable to run java` 或 `Unable to start Tika server`。

### 4. 验证与交付

执行：

- 前端定向 Jest 测试、TypeScript 类型检查和 ESLint。
- 后端定向 Pytest；必要时运行相关知识库文件服务测试集。
- 对同一目录执行并发路径创建验证，检查数据库无同级重名目录。
- 实际提交一份可安全测试的旧 `.doc` 解析任务并观察完成状态；不批量重跑用户全部失败文档，避免未经确认产生大规模计算负载。
- 检查服务健康状态和日志。
- 使用中文 commit message 提交本次代码改动。
