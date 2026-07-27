# Chat QA Analytics Frontend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a working `/chat/:id/qa-analytics` frontend with a sidebar entry, dashboard interactions, question detail sheet, and navigation back to the exact chat turn.

**Architecture:** Reuse the existing chat application sidebar and add a dedicated analytics route. Keep the dashboard presentation independent from data transport by consuming typed analytics data from a local fixture adapter; a later backend task can replace the adapter without changing the page components or interactions.

**Tech Stack:** React 18, TypeScript, React Router 7, Tailwind CSS, Radix UI components, Recharts, Jest, Testing Library.

## Global Constraints

- The dashboard grain is one completed user question and its paired assistant answer.
- The analytics route is `/chat/:id/qa-analytics`.
- The analytics entry appears below the chat application name and above the conversation section.
- Frontend-only phase uses typed fixture data; it does not modify backend APIs or persistence.
- Knowledge-base multi-selection uses OR; different filter categories use AND.
- The primary row action is “查看详情”, not “查看会话”.
- “查看完整对话” navigates with both `conversationId` and `messageId`.

---

### Task 1: Typed analytics model and interaction selectors

**Files:**
- Create: `web/src/pages/next-chats/qa-analytics/types.ts`
- Create: `web/src/pages/next-chats/qa-analytics/mock-data.ts`
- Create: `web/src/pages/next-chats/qa-analytics/selectors.ts`
- Test: `web/src/pages/next-chats/qa-analytics/selectors.test.ts`

**Interfaces:**
- Produces: `QaStatus`, `QaQuestion`, `QaAnalyticsData`, `QaFilters`, `filterQuestions(questions, filters)`, `paginateQuestions(questions, page, pageSize)`, and `buildChatQuestionUrl(chatId, conversationId, messageId)`.
- Consumes: no application services.

- [ ] **Step 1: Write failing selector tests**

```ts
it('combines knowledge base OR filters with status and keyword AND filters', () => {
  expect(
    filterQuestions(questions, {
      knowledgeBaseIds: ['hr', 'finance'],
      status: 'cited',
      keyword: '入职',
      dateRange: 'all',
    }).map((item) => item.id),
  ).toEqual(['q-1']);
});

it('builds a URL that targets one question in a conversation', () => {
  expect(buildChatQuestionUrl('chat-1', 'conv-1', 'msg-1')).toBe(
    '/chat/chat-1?conversationId=conv-1&messageId=msg-1',
  );
});
```

- [ ] **Step 2: Run tests and verify RED**

Run: `cd web && npx jest src/pages/next-chats/qa-analytics/selectors.test.ts --runInBand`

Expected: FAIL because the analytics types and selector functions do not exist.

- [ ] **Step 3: Implement minimal types, selectors, pagination, and fixture**

Create typed records for summary metrics, 30-day trend data, knowledge bases, questions, final citations, and retrieval candidates. Implement filtering without UI dependencies and provide enough fixture rows to exercise every status, multiple knowledge bases, keyword filtering, dates, and pagination.

- [ ] **Step 4: Run tests and verify GREEN**

Run: `cd web && npx jest src/pages/next-chats/qa-analytics/selectors.test.ts --runInBand`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add web/src/pages/next-chats/qa-analytics
HUSKY=0 git commit -m "前端：新增问答分析数据模型"
```

### Task 2: Dedicated route and sidebar analytics entry

**Files:**
- Create: `web/src/pages/next-chats/chat/qa-analytics-link.tsx`
- Test: `web/src/pages/next-chats/chat/qa-analytics-link.test.tsx`
- Modify: `web/src/pages/next-chats/chat/sessions.tsx`
- Modify: `web/src/routes.tsx`
- Modify: `web/src/locales/zh.ts`
- Modify: `web/src/locales/en.ts`

**Interfaces:**
- Produces: `QaAnalyticsLink({ active?: boolean })`.
- Consumes: the current `:id` route parameter and React Router navigation.

- [ ] **Step 1: Write failing navigation test**

```tsx
it('navigates from a chat application to its analytics route', async () => {
  render(
    <MemoryRouter initialEntries={['/chat/chat-1']}>
      <Routes>
        <Route path="/chat/:id" element={<QaAnalyticsLink />} />
        <Route
          path="/chat/:id/qa-analytics"
          element={<div>analytics destination</div>}
        />
      </Routes>
    </MemoryRouter>,
  );

  await userEvent.click(screen.getByRole('button', { name: '问答分析' }));
  expect(screen.getByText('analytics destination')).toBeInTheDocument();
});
```

- [ ] **Step 2: Run test and verify RED**

Run: `cd web && npx jest src/pages/next-chats/chat/qa-analytics-link.test.tsx --runInBand`

Expected: FAIL because `QaAnalyticsLink` does not exist.

- [ ] **Step 3: Implement the link and register the route**

Add a chart icon button immediately below the existing chat application header. Register `/chat/:id/qa-analytics` before the general chat route. Give the button selected styling when rendered in the analytics page.

- [ ] **Step 4: Run navigation test and verify GREEN**

Run: `cd web && npx jest src/pages/next-chats/chat/qa-analytics-link.test.tsx --runInBand`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add web/src/pages/next-chats/chat web/src/routes.tsx web/src/locales/en.ts web/src/locales/zh.ts
HUSKY=0 git commit -m "前端：新增问答分析独立入口"
```

### Task 3: Dashboard presentation and working filters

**Files:**
- Create: `web/src/pages/next-chats/qa-analytics/dashboard.tsx`
- Create: `web/src/pages/next-chats/qa-analytics/metric-card.tsx`
- Create: `web/src/pages/next-chats/qa-analytics/trend-chart.tsx`
- Create: `web/src/pages/next-chats/qa-analytics/filter-bar.tsx`
- Create: `web/src/pages/next-chats/qa-analytics/question-table.tsx`
- Test: `web/src/pages/next-chats/qa-analytics/dashboard.test.tsx`

**Interfaces:**
- Produces: `QaAnalyticsDashboard({ data, onViewConversation })`, where `onViewConversation(question)` is called only from the detail sheet.
- Consumes: `QaAnalyticsData`, selectors from Task 1, and existing UI primitives.

- [ ] **Step 1: Write failing dashboard interaction tests**

```tsx
it('filters questions by status and keyword and clears all filters', async () => {
  render(<QaAnalyticsDashboard data={qaAnalyticsMockData} />);

  await userEvent.selectOptions(screen.getByLabelText('状态'), 'missed');
  await userEvent.type(screen.getByLabelText('问题关键词'), '试用期');
  await userEvent.click(screen.getByRole('button', { name: '查询' }));

  expect(screen.getByText('试用期转正的条件是什么？')).toBeInTheDocument();
  expect(screen.queryByText('员工入职需要准备哪些材料？')).not.toBeInTheDocument();

  await userEvent.click(screen.getByRole('button', { name: '清空筛选' }));
  expect(screen.getByText('员工入职需要准备哪些材料？')).toBeInTheDocument();
});
```

- [ ] **Step 2: Run test and verify RED**

Run: `cd web && npx jest src/pages/next-chats/qa-analytics/dashboard.test.tsx --runInBand`

Expected: FAIL because the dashboard does not exist.

- [ ] **Step 3: Implement the dashboard**

Render four responsive metric cards, a 30-day line chart with day/week toggle, controlled filters, a responsive table, and pagination. Apply filters only after “查询”; “清空筛选” resets both draft and applied filters.

- [ ] **Step 4: Run dashboard tests and verify GREEN**

Run: `cd web && npx jest src/pages/next-chats/qa-analytics/dashboard.test.tsx --runInBand`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add web/src/pages/next-chats/qa-analytics
HUSKY=0 git commit -m "前端：实现问答分析看板交互"
```

### Task 4: Question detail sheet and exact-chat navigation

**Files:**
- Create: `web/src/pages/next-chats/qa-analytics/question-detail-sheet.tsx`
- Modify: `web/src/pages/next-chats/qa-analytics/dashboard.tsx`
- Modify: `web/src/pages/next-chats/qa-analytics/dashboard.test.tsx`

**Interfaces:**
- Produces: `QuestionDetailSheet({ question, open, onOpenChange, onViewConversation })`.
- Consumes: `QaQuestion` and `buildChatQuestionUrl`.

- [ ] **Step 1: Write failing detail interaction test**

```tsx
it('opens the selected question and navigates to its exact chat turn', async () => {
  const onViewConversation = jest.fn();
  render(
    <QaAnalyticsDashboard
      data={qaAnalyticsMockData}
      onViewConversation={onViewConversation}
    />,
  );

  await userEvent.click(
    screen.getAllByRole('button', { name: '查看详情' })[0],
  );
  expect(screen.getByRole('dialog')).toHaveTextContent('最终回答');

  await userEvent.click(screen.getByRole('button', { name: '查看完整对话' }));
  expect(onViewConversation).toHaveBeenCalledWith(
    expect.objectContaining({
      conversationId: 'conversation-1',
      messageId: 'message-1',
    }),
  );
});
```

- [ ] **Step 2: Run test and verify RED**

Run: `cd web && npx jest src/pages/next-chats/qa-analytics/dashboard.test.tsx --runInBand`

Expected: FAIL because the row action does not open a detail sheet.

- [ ] **Step 3: Implement the detail sheet and navigation**

Show the question, answer, status, completion time, conversation metadata, final citations, and retrieval candidates in a right-side sheet. Call `onViewConversation(question)` when the user clicks “查看完整对话”; the route page owns navigation.

- [ ] **Step 4: Run test and verify GREEN**

Run: `cd web && npx jest src/pages/next-chats/qa-analytics/dashboard.test.tsx --runInBand`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add web/src/pages/next-chats/qa-analytics
HUSKY=0 git commit -m "前端：新增问答详情与对话定位"
```

### Task 5: Route page assembly and final verification

**Files:**
- Create: `web/src/pages/next-chats/qa-analytics/index.tsx`
- Modify: `web/src/pages/next-chats/chat/sessions.tsx`
- Modify: `web/src/routes.tsx`
- Create: `design-qa.md`

**Interfaces:**
- Produces: the default route component for `/chat/:id/qa-analytics`.
- Consumes: `Sessions`, `QaAnalyticsDashboard`, and `qaAnalyticsMockData`.

- [ ] **Step 1: Assemble the analytics route**

Render `RootLayoutContainer`, the shared `Sessions` sidebar with the analytics item active, and the dashboard in the right content area. Conversation clicks navigate back to `/chat/:id` with the selected `conversationId`. The dashboard's `onViewConversation` callback uses `buildChatQuestionUrl(chatId, conversationId, messageId)`.

- [ ] **Step 2: Run focused tests**

Run:

```bash
cd web
npx jest \
  src/pages/next-chats/qa-analytics/selectors.test.ts \
  src/pages/next-chats/chat/qa-analytics-link.test.tsx \
  src/pages/next-chats/qa-analytics/dashboard.test.tsx \
  --runInBand
```

Expected: all focused tests PASS.

- [ ] **Step 3: Run static checks**

Run:

```bash
cd web
npx tsc --noEmit
npx eslint src/pages/next-chats/qa-analytics src/pages/next-chats/chat/qa-analytics-link.tsx src/pages/next-chats/chat/sessions.tsx src/routes.tsx --ext .ts,.tsx --report-unused-disable-directives
```

Expected: both commands exit 0.

- [ ] **Step 4: Run and visually verify**

Start the existing Vite application, open `/chat/:id/qa-analytics`, compare it with the supplied dashboard screenshot, and exercise:

- sidebar entry and conversation return navigation;
- status, knowledge-base, date, and keyword filters;
- clear filters;
- day/week trend toggle;
- pagination;
- detail sheet;
- exact-chat navigation.

Record the comparison and fixes in `design-qa.md`. The final line must be `final result: passed`.

- [ ] **Step 5: Commit**

```bash
git add web/src/pages/next-chats/qa-analytics web/src/pages/next-chats/chat web/src/routes.tsx web/src/locales/en.ts web/src/locales/zh.ts design-qa.md
HUSKY=0 git commit -m "前端：完成问答分析页面"
```
