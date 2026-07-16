# Admin Chinese Internationalization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add complete Simplified Chinese translations to the Admin console and let administrators switch language from the Admin sidebar.

**Architecture:** Keep the existing application-wide `react-i18next` instance and `localStorage.lng` persistence. Add a Chinese `admin` resource tree matching English, then mount a small Admin-only language switcher that consumes `supportedLanguages` and `changeLanguageAsync()`.

**Tech Stack:** TypeScript, React 18, react-i18next, Radix Dropdown Menu, Jest, Testing Library

## Global Constraints

- Do not change Admin backend APIs, authentication, routing, or user records.
- Do not change the main application Header language selector.
- Do not introduce a separate Admin language state or persistence key.
- Do not add dependencies.
- Preserve unrelated staged and unstaged work, especially `uv.lock` and existing design documents.

---

## File Structure

- Create `web/src/locales/__tests__/admin-locales.test.ts`: verifies Chinese and English Admin resource keys remain aligned.
- Modify `web/src/locales/zh.ts`: owns Simplified Chinese Admin copy.
- Create `web/src/pages/admin/components/admin-language-switcher.tsx`: owns the Admin-only language dropdown.
- Create `web/src/pages/admin/components/admin-language-switcher.test.tsx`: verifies language selection delegates to the shared locale service.
- Modify `web/src/pages/admin/layouts/navigation-layout.tsx`: mounts the switcher in the sidebar footer.

### Task 1: Simplified Chinese Admin resource coverage

**Files:**
- Create: `web/src/locales/__tests__/admin-locales.test.ts`
- Modify: `web/src/locales/zh.ts:before the explore resource block`

**Interfaces:**
- Consumes: locale exports shaped as `{ translation: Record<string, unknown> }`.
- Produces: `translation.admin`, structurally identical to English `translation.admin`.

- [ ] **Step 1: Write the failing locale coverage test**

```ts
import translationEn from '../en';
import translationZh from '../zh';

const collectLeafKeys = (
  value: Record<string, unknown>,
  prefix = '',
): string[] =>
  Object.entries(value)
    .flatMap(([key, child]) => {
      const path = prefix ? `${prefix}.${key}` : key;
      return child !== null && typeof child === 'object'
        ? collectLeafKeys(child as Record<string, unknown>, path)
        : [path];
    })
    .sort();

describe('Simplified Chinese Admin translations', () => {
  it('contains every English Admin translation key', () => {
    expect(collectLeafKeys(translationZh.translation.admin)).toEqual(
      collectLeafKeys(translationEn.translation.admin),
    );
  });

  it('uses Simplified Chinese for core Admin navigation', () => {
    expect(translationZh.translation.admin.serviceStatus).toBe('服务状态');
    expect(translationZh.translation.admin.userManagement).toBe('用户管理');
    expect(translationZh.translation.admin.sandboxSettings).toBe('沙箱设置');
  });
});
```

- [ ] **Step 2: Run the test and verify RED**

```bash
cd web
PATH=/home/qaadmin/.local/node-v22.12.0-linux-x64/bin:$PATH npm test -- --runInBand src/locales/__tests__/admin-locales.test.ts
```

Expected: FAIL because `translationZh.translation.admin` is undefined.

- [ ] **Step 3: Add the complete Simplified Chinese resource**

Add `admin` at the same resource level as `empty` and `explore`. It must contain exactly the English leaf keys from `web/src/locales/en.ts:3070-3239` with the following copy:

```ts
admin: {
  loginTitle: '管理控制台',
  title: 'RAGFlow',
  confirm: '确认',
  close: '关闭',
  yes: '是',
  no: '否',
  delete: '删除',
  cancel: '取消',
  reset: '重置',
  import: '导入',
  description: '描述',
  noDescription: '暂无描述',
  none: '无',
  resourceType: {
    dataset: '知识库',
    chat: '聊天',
    agent: '智能体',
    search: '搜索',
    file: '文件',
    team: '团队',
    memory: '记忆',
  },
  permissionType: {
    enable: '启用',
    read: '读取',
    write: '写入',
    share: '分享',
  },
  serviceStatus: '服务状态',
  userManagement: '用户管理',
  sandboxSettings: '沙箱设置',
  registrationWhitelist: '注册白名单',
  roles: '角色',
  monitoring: '监控',
  sandboxSettingsPage: {
    description: '配置代码执行沙箱提供商。智能体中的代码组件会使用该沙箱。',
    providerSelection: '提供商选择',
    providerSelectionDescription: '选择用于执行代码的沙箱提供商',
    namedProviderConfiguration: '{{name}} 配置',
    namedProviderConfigurationDescription: '配置 {{name}} 的连接参数。',
    saveConfiguration: '保存配置',
    saving: '正在保存……',
    testConnectionResultModal: {
      title: '连接测试结果',
      testing: '正在测试沙箱提供商连接……',
      success: '已成功连接沙箱提供商',
      failed: '连接沙箱提供商失败',
      exitCode: '退出码',
      executionTime: '执行时间',
      stdout: '标准输出',
      stderr: '错误输出/堆栈信息',
    },
    testConnection: '测试连接',
    testing: '正在测试……',
  },
  selectFile: '选择文件',
  noFileSelected: '未选择文件',
  back: '返回',
  active: '已启用',
  inactive: '未启用',
  enable: '启用',
  disable: '禁用',
  all: '全部',
  actions: '操作',
  newUser: '新建用户',
  email: '邮箱',
  name: '名称',
  nickname: '昵称',
  status: '状态',
  id: 'ID',
  serviceType: '服务类型',
  host: '主机',
  port: '端口',
  role: '角色',
  user: '用户',
  userType: '用户类型',
  superuser: '超级管理员',
  normalUser: '普通用户',
  createTime: '创建时间',
  lastLoginTime: '最后登录时间',
  lastUpdateTime: '最后更新时间',
  isAnonymous: '是否匿名',
  isSuperuser: '是否为超级管理员',
  deleteUser: '删除用户',
  deleteUserConfirmation: '确定要删除该用户吗？',
  createNewUser: '创建新用户',
  changePassword: '修改密码',
  newPassword: '新密码',
  confirmNewPassword: '确认新密码',
  password: '密码',
  confirmPassword: '确认密码',
  invalidEmail: '请输入有效的邮箱地址！',
  passwordRequired: '请输入密码！',
  passwordMinLength: '密码长度必须超过 8 个字符。',
  confirmPasswordRequired: '请确认密码！',
  confirmPasswordDoNotMatch: '两次输入的密码不一致！',
  read: '读取',
  write: '写入',
  share: '分享',
  create: '创建',
  extraInfo: '附加信息',
  serviceDetail: '服务 {{name}} 详情',
  taskExecutorDetail: '任务执行器详情',
  whitelistManagement: '白名单管理',
  exportAsExcel: '导出 Excel',
  importFromExcel: '导入 Excel',
  createEmail: '新增邮箱',
  deleteEmail: '删除邮箱',
  editEmail: '编辑邮箱',
  deleteWhitelistEmailConfirmation:
    '确定要从白名单中删除该邮箱吗？此操作无法撤销。',
  importWhitelist: '导入白名单（Excel）',
  importSelectExcelFile: 'Excel 文件（.xlsx）',
  importOverwriteExistingEmails: '覆盖已存在的邮箱',
  importInvalidExcelFile: '请选择有效的 Excel 文件',
  importFileRequired: '请选择要导入的文件',
  importFileTips: '文件必须只包含一个名为 <code>email</code> 的表头列。',
  chunkNum: '切片数',
  docNum: '文档数',
  tokenNum: 'Token 用量',
  language: '语言',
  createDate: '创建日期',
  updateDate: '更新日期',
  permission: '权限',
  agentTitle: '智能体标题',
  canvasCategory: '画布分类',
  newRole: '新建角色',
  addNewRole: '新增角色',
  roleName: '角色名称',
  roleNameRequired: '请输入角色名称',
  resources: '资源',
  editRoleDescription: '编辑角色描述',
  deleteRole: '删除角色',
  deleteRoleConfirmation: '确定要删除该角色吗？此操作无法撤销。',
  alive: '正常',
  timeout: '超时',
  fail: '失败',
},
```

- [ ] **Step 4: Run the locale test and verify GREEN**

Run the Step 2 command again. Expected: both tests PASS.

- [ ] **Step 5: Commit Task 1**

```bash
git add web/src/locales/zh.ts web/src/locales/__tests__/admin-locales.test.ts
PATH=/home/qaadmin/.local/node-v22.12.0-linux-x64/bin:$PATH git commit -m "fix(i18n): add Chinese admin translations"
```

### Task 2: Admin language switcher

**Files:**
- Create: `web/src/pages/admin/components/admin-language-switcher.test.tsx`
- Create: `web/src/pages/admin/components/admin-language-switcher.tsx`
- Modify: `web/src/pages/admin/layouts/navigation-layout.tsx:imports and sidebar footer`

**Interfaces:**
- Consumes: `supportedLanguages: Array<{ code: string; displayName: string }>` and `changeLanguageAsync(lng: string): Promise<void>`.
- Produces: `AdminLanguageSwitcher(): JSX.Element`.

- [ ] **Step 1: Write the failing component test**

```tsx
import { fireEvent, render, screen } from '@testing-library/react';
import AdminLanguageSwitcher from './admin-language-switcher';

const mockChangeLanguageAsync = jest.fn().mockResolvedValue(undefined);

jest.mock('@/locales/config', () => ({
  supportedLanguages: [
    { code: 'en', displayName: 'English' },
    { code: 'zh', displayName: '中文' },
  ],
  changeLanguageAsync: (lng: string) => mockChangeLanguageAsync(lng),
}));

jest.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) => key,
    i18n: { resolvedLanguage: 'en', language: 'en' },
  }),
}));

jest.mock('@/components/ui/button', () => ({
  Button: ({
    children,
    ...props
  }: React.ButtonHTMLAttributes<HTMLButtonElement>) => (
    <button {...props}>{children}</button>
  ),
}));

jest.mock('@/components/ui/dropdown-menu', () => ({
  DropdownMenu: ({ children }: React.PropsWithChildren) => <div>{children}</div>,
  DropdownMenuTrigger: ({ children }: React.PropsWithChildren) => <>{children}</>,
  DropdownMenuContent: ({ children }: React.PropsWithChildren) => <div>{children}</div>,
  DropdownMenuItem: ({
    children,
    onClick,
  }: React.PropsWithChildren<{ onClick?: () => void }>) => (
    <button onClick={onClick}>{children}</button>
  ),
}));

describe('AdminLanguageSwitcher', () => {
  it('shows the current language and switches to the selected language', () => {
    render(<AdminLanguageSwitcher />);
    expect(
      screen.getByRole('button', { name: 'admin.language' }),
    ).toHaveTextContent('English');

    fireEvent.click(screen.getByRole('button', { name: '中文' }));

    expect(mockChangeLanguageAsync).toHaveBeenCalledWith('zh');
  });
});
```

- [ ] **Step 2: Run the component test and verify RED**

```bash
cd web
PATH=/home/qaadmin/.local/node-v22.12.0-linux-x64/bin:$PATH npm test -- --runInBand src/pages/admin/components/admin-language-switcher.test.tsx
```

Expected: FAIL because `admin-language-switcher.tsx` does not exist.

- [ ] **Step 3: Implement the Admin language switcher**

Create `web/src/pages/admin/components/admin-language-switcher.tsx`:

```tsx
import { useTranslation } from 'react-i18next';

import { Button } from '@/components/ui/button';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { changeLanguageAsync, supportedLanguages } from '@/locales/config';

const AdminLanguageSwitcher = () => {
  const { t, i18n } = useTranslation();
  const languageCode = i18n.resolvedLanguage || i18n.language;
  const currentLanguage = supportedLanguages.find(
    ({ code }) => code === languageCode,
  );

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          type="button"
          variant="ghost"
          aria-label={t('admin.language')}
          className="justify-start"
        >
          {currentLanguage?.displayName || languageCode}
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start">
        {supportedLanguages.map(({ code, displayName }) => (
          <DropdownMenuItem
            key={code}
            onClick={() => void changeLanguageAsync(code)}
          >
            {displayName}
          </DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  );
};

export default AdminLanguageSwitcher;
```

- [ ] **Step 4: Mount the switcher in the Admin sidebar footer**

Import `AdminLanguageSwitcher` from `../components/admin-language-switcher`, then replace the footer row with:

```tsx
<div className="flex justify-between items-center gap-2">
  <span className="leading-none text-xs text-accent-primary">
    {version}
  </span>

  <div className="flex items-center gap-2">
    <AdminLanguageSwitcher />
    <ThemeSwitch />
  </div>
</div>
```

- [ ] **Step 5: Run the component test and verify GREEN**

Run the Step 2 command again. Expected: PASS with `changeLanguageAsync('zh')`.

- [ ] **Step 6: Commit Task 2**

```bash
git add web/src/pages/admin/components/admin-language-switcher.tsx web/src/pages/admin/components/admin-language-switcher.test.tsx web/src/pages/admin/layouts/navigation-layout.tsx
PATH=/home/qaadmin/.local/node-v22.12.0-linux-x64/bin:$PATH git commit -m "feat(admin): add language switcher"
```

### Task 3: Frontend verification

**Files:**
- Verify only; no planned production file changes.

**Interfaces:**
- Consumes: the completed locale resource and Admin switcher.
- Produces: test, type, lint, and build evidence.

- [ ] **Step 1: Run focused tests**

```bash
cd web
PATH=/home/qaadmin/.local/node-v22.12.0-linux-x64/bin:$PATH npm test -- --runInBand src/locales/__tests__/admin-locales.test.ts src/pages/admin/components/admin-language-switcher.test.tsx
```

Expected: 2 test suites PASS.

- [ ] **Step 2: Run TypeScript type checking**

```bash
cd web
PATH=/home/qaadmin/.local/node-v22.12.0-linux-x64/bin:$PATH npm run type-check
```

Expected: exit code 0.

- [ ] **Step 3: Lint changed TypeScript files**

```bash
cd web
PATH=/home/qaadmin/.local/node-v22.12.0-linux-x64/bin:$PATH npx eslint \
  src/locales/zh.ts \
  src/locales/__tests__/admin-locales.test.ts \
  src/pages/admin/components/admin-language-switcher.tsx \
  src/pages/admin/components/admin-language-switcher.test.tsx \
  src/pages/admin/layouts/navigation-layout.tsx \
  --report-unused-disable-directives
```

Expected: exit code 0 with no lint errors.

- [ ] **Step 4: Build the frontend**

```bash
cd web
PATH=/home/qaadmin/.local/node-v22.12.0-linux-x64/bin:$PATH npm run build
```

Expected: Vite production build completes successfully.

- [ ] **Step 5: Inspect final scope**

```bash
git diff --check HEAD~2..HEAD
git status --short
```

Expected: no whitespace errors; unrelated pre-existing work remains untouched.
