# Admin 简体中文国际化设计

## 背景

RAGFlow Admin 页面已经通过 `react-i18next` 使用 `admin.*` 翻译键，英文及部分语言包也包含完整的 `admin` 节点。但是简体中文语言包缺少该节点，因此用户选择中文时，Admin 文案会回退到英文。同时，Admin 导航没有语言切换入口，只能继承主站保存在本地的语言设置。

## 目标

- 为当前 Admin 页面提供完整的简体中文文案。
- 在 Admin 左侧栏提供语言切换入口。
- 继续复用主站现有的语言加载、切换和持久化机制。
- 不改变 Admin 后端、鉴权、路由或主站 Header 行为。

## 方案

### 中文语言资源

在 `web/src/locales/zh.ts` 中增加 `admin` 节点。键结构以英文语言包的 `admin` 节点为准，覆盖 Admin 页面当前使用的全部翻译键，包括导航、服务状态、用户管理、沙箱配置、白名单、角色、表单校验和状态文案。

翻译键必须与英文保持同构。这样新增 Admin 文案时，可以通过自动化测试发现简体中文遗漏，而不是在运行时静默回退到英文。

### Admin 语言切换入口

在 `web/src/pages/admin/layouts/navigation-layout.tsx` 左侧栏底部加入语言下拉菜单，放在版本号和主题切换区域附近。

下拉菜单复用：

- `supportedLanguages` 生成选项和显示名称；
- 当前 `i18n.language` 确定已选语言；
- `changeLanguageAsync()` 加载语言包、更新文档语言并写入 `localStorage.lng`。

切换后 `react-i18next` 触发 Admin 页面重新渲染，无需刷新，也不新增 Admin 专属语言状态。

## 数据流

1. 应用启动时由现有 `initLanguage()` 从 `localStorage.lng` 读取语言。
2. Admin 页面从 `i18next` 获取当前语言和翻译函数。
3. 用户在 Admin 左侧栏选择语言。
4. `changeLanguageAsync()` 按需加载目标语言包并切换语言。
5. Admin 页面立即使用目标语言重新渲染，语言选择继续供主站和 Admin 共用。

## 错误处理

沿用 `loadLanguageAsync()` 的现有行为：不支持的语言输出警告，语言包加载失败输出错误。Admin 入口只展示 `supportedLanguages` 中的语言，因此正常交互不会传入未知语言。

## 测试

- 增加语言资源一致性测试，递归比较英文和简体中文 `admin` 节点的键集合。
- 增加 Admin 导航测试，验证语言入口存在，并且选择语言时调用现有切换逻辑。
- 运行相关 Jest 测试、TypeScript 类型检查和修改文件的 ESLint 检查。

## 非目标

- 不补齐其他语言缺失的 Admin 文案。
- 不重构主站 Header 的语言选择组件。
- 不修改后端用户语言字段或管理员账号数据。
- 不改变 Admin 页面布局和视觉风格。
