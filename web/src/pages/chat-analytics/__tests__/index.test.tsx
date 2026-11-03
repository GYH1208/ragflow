import { useChatAnalytics, useMcpAnalytics } from '@/hooks/use-chat-analytics';
import { fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import ChatAnalyticsPage from '..';

const React = jest.requireActual<typeof import('react')>('react');
(globalThis as typeof globalThis & { React: typeof React }).React = React;

jest.mock('@/hooks/use-chat-analytics', () => ({
  useChatAnalytics: jest.fn(),
  useMcpAnalytics: jest.fn(),
}));
jest.mock('react-router', () => ({
  Link: ({ children }: { children: any }) => <>{children}</>,
}));
jest.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) =>
      ({
        'chatAnalytics.title': 'Q&A Analytics',
        'chatAnalytics.lifetimeCount': 'All-time Q&As',
        'chatAnalytics.last30DaysCount': 'Q&As in the last 30 days',
        'chatAnalytics.todayCount': "Today's Q&As",
        'chatAnalytics.trendTitle': 'Q&A trend',
        'chatAnalytics.allAssistants': 'All assistants',
        'chatAnalytics.assistant': 'Assistant',
        'chatAnalytics.dateRange': 'Date range',
        'chatAnalytics.byDay': 'By day',
        'chatAnalytics.byWeek': 'By week',
        'chatAnalytics.byMonth': 'By month',
        'chatAnalytics.assistantName': 'Chat assistant',
        'chatAnalytics.totalQuestions': 'Total Q&As',
        'chatAnalytics.loadError': 'Unable to load analytics',
        'chatAnalytics.retry': 'Retry',
        'chatAnalytics.noData': 'No data available',
      })[key] ?? key,
  }),
}));
jest.mock('recharts', () => ({
  Cell: () => null,
  CartesianGrid: () => null,
  Line: () => null,
  LineChart: ({ children }: { children: any }) => (
    <div data-testid="trend-chart">{children}</div>
  ),
  Pie: ({ children }: { children: any }) => <div>{children}</div>,
  PieChart: ({ children }: { children: any }) => (
    <div data-testid="assistant-donut">{children}</div>
  ),
  ResponsiveContainer: ({ children }: { children: any }) => (
    <div>{children}</div>
  ),
  Tooltip: () => null,
  XAxis: () => null,
  YAxis: () => null,
}));

const mockUseChatAnalytics = jest.mocked(useChatAnalytics);
const mockUseMcpAnalytics = jest.mocked(useMcpAnalytics);
const refetch = jest.fn();
const mcpRefetch = jest.fn();
const mcpFixture = {
  authorized: true as const,
  available: true as const,
  total_count: 231,
  categories: { retrieval: 152, content: 69, other: 10 },
  tools: [
    { name: 'search_documents', count: 79 },
    { name: 'ragflow_retrieval', count: 73 },
    { name: 'get_document_chunks', count: 69 },
    { name: 'list_documents', count: 5 },
    { name: 'get_chunk_image', count: 2 },
    { name: 'export_document_catalog', count: 2 },
    { name: 'list_knowledge_bases', count: 1 },
  ],
  updated_at: '2026-11-02T07:11:47+00:00',
};

const fixture = {
  total_count: 1149,
  last_30_days_count: 546,
  today_count: 21,
  trend: [
    { date: '2026-09-23', count: 29 },
    { date: '2026-09-24', count: 21 },
  ],
  assistants: [
    { id: 'assistant-1', name: 'Support assistant', count: 800 },
    { id: 'assistant-2', name: 'Sales assistant', count: 349 },
  ],
  assistant_options: [
    { id: 'assistant-1', name: 'Support assistant' },
    { id: 'assistant-2', name: 'Sales assistant' },
  ],
};

const renderDashboard = () => render(<ChatAnalyticsPage />);

beforeEach(() => {
  refetch.mockReset();
  mcpRefetch.mockReset();
  mockUseMcpAnalytics.mockReturnValue({
    data: mcpFixture,
    loading: false,
    error: null,
    refetch: mcpRefetch,
  });
  mockUseChatAnalytics.mockReturnValue({
    data: fixture,
    loading: false,
    error: null,
    refetch,
  });
});

it('shows real cumulative MCP statistics in a separate tab and refreshes them', async () => {
  const user = userEvent.setup();
  renderDashboard();
  await user.click(screen.getByRole('tab', { name: 'MCP 调用' }));
  expect(screen.getByText('231')).toBeInTheDocument();
  expect(screen.getByText('152')).toBeInTheDocument();
  expect(screen.getByText(/累计统计/)).toBeInTheDocument();
  expect(screen.getByText('search_documents')).toBeInTheDocument();
  expect(screen.getByText('34.2%')).toBeInTheDocument();
  expect(screen.queryByTestId('trend-chart')).not.toBeInTheDocument();
  await user.click(screen.getByRole('button', { name: '刷新 MCP 数据' }));
  expect(mcpRefetch).toHaveBeenCalledTimes(1);
  expect(refetch).not.toHaveBeenCalled();
  await user.click(screen.getByRole('tab', { name: '聊天问答' }));
  expect(screen.getByTestId('trend-chart')).toBeInTheDocument();
});

it('shows a read failure instead of zero counts and still allows chat analysis', async () => {
  mockUseMcpAnalytics.mockReturnValue({
    data: { authorized: true, available: false },
    loading: false,
    error: null,
    refetch: mcpRefetch,
  });
  const user = userEvent.setup();
  renderDashboard();
  await user.click(screen.getByRole('tab', { name: 'MCP 调用' }));
  expect(screen.getByRole('alert')).toHaveTextContent(
    '暂时无法读取 MCP 调用数据',
  );
  expect(screen.queryByText('0')).not.toBeInTheDocument();
  await user.click(screen.getByRole('button', { name: '重试' }));
  expect(mcpRefetch).toHaveBeenCalledTimes(1);
});

it('shows a real zero-data state', async () => {
  mockUseMcpAnalytics.mockReturnValue({
    data: {
      ...mcpFixture,
      total_count: 0,
      categories: { retrieval: 0, content: 0, other: 0 },
      tools: mcpFixture.tools.map((tool) => ({ ...tool, count: 0 })),
    },
    loading: false,
    error: null,
    refetch: mcpRefetch,
  });
  const user = userEvent.setup();
  renderDashboard();
  await user.click(screen.getByRole('tab', { name: 'MCP 调用' }));
  expect(screen.getByText('暂无 MCP 调用记录')).toBeInTheDocument();
});

it('does not show global statistics to an ordinary user', async () => {
  mockUseMcpAnalytics.mockReturnValue({
    data: { authorized: false },
    loading: false,
    error: null,
    refetch: mcpRefetch,
  });
  const user = userEvent.setup();
  renderDashboard();
  await user.click(screen.getByRole('tab', { name: 'MCP 调用' }));
  expect(
    screen.getByText('仅超级管理员可查看 MCP 服务汇总数据'),
  ).toBeInTheDocument();
  expect(screen.queryByText('231')).not.toBeInTheDocument();
});

it('renders an unknown tool even when its name matches an object prototype property', async () => {
  mockUseMcpAnalytics.mockReturnValue({
    data: {
      ...mcpFixture,
      total_count: 234,
      categories: { retrieval: 152, content: 69, other: 13 },
      tools: [...mcpFixture.tools, { name: '__proto__', count: 3 }],
    },
    loading: false,
    error: null,
    refetch: mcpRefetch,
  });
  const user = userEvent.setup();
  renderDashboard();
  await user.click(screen.getByRole('tab', { name: 'MCP 调用' }));
  expect(screen.getAllByText('__proto__')).toHaveLength(2);
  expect(screen.getByText('234')).toBeInTheDocument();
});

afterEach(() => {
  jest.useRealTimers();
});

it('renders summary, trend, and assistant distribution', () => {
  renderDashboard();

  expect(screen.getAllByText('1,149')).toHaveLength(2);
  expect(screen.getByText('546')).toBeInTheDocument();
  expect(screen.getByText('21')).toBeInTheDocument();
  expect(screen.getByTestId('trend-chart')).toBeInTheDocument();
  expect(screen.getByTestId('assistant-donut')).toBeInTheDocument();
  expect(screen.getByText('聊天助理问答分布')).toBeInTheDocument();
  expect(screen.getByText('问答总量')).toBeInTheDocument();
  expect(screen.getByText('Support assistant')).toBeInTheDocument();
  expect(screen.getByText('Sales assistant')).toBeInTheDocument();
  expect(screen.getByText('70%')).toBeInTheDocument();
  expect(screen.getByText('30%')).toBeInTheDocument();
  expect(screen.queryByRole('columnheader')).not.toBeInTheDocument();
  expect(screen.queryByText(/source/i)).not.toBeInTheDocument();
});

it('changes granularity without resetting the current filters', async () => {
  const user = userEvent.setup();
  renderDashboard();

  const initialParams = mockUseChatAnalytics.mock.calls.at(-1)?.[0];
  await user.click(screen.getByRole('button', { name: 'By week' }));

  expect(mockUseChatAnalytics).toHaveBeenLastCalledWith({
    ...initialParams,
    dialogId: undefined,
    granularity: 'week',
  });
});

it('starts a new date range before choosing its end date', () => {
  jest.useFakeTimers().setSystemTime(new Date('2026-09-24T12:00:00Z'));
  renderDashboard();

  fireEvent.click(
    screen.getByRole('button', {
      name: 'Aug 26, 2026 - Sep 24, 2026',
    }),
  );
  fireEvent.click(
    screen.getByRole('button', { name: /Thursday, September 10th, 2026/ }),
  );

  expect(mockUseChatAnalytics).toHaveBeenLastCalledWith(
    expect.objectContaining({
      fromDate: '2026-09-10',
      toDate: '2026-09-10',
    }),
  );

  fireEvent.click(
    screen.getByRole('button', { name: /Tuesday, September 15th, 2026/ }),
  );
  expect(mockUseChatAnalytics).toHaveBeenLastCalledWith(
    expect.objectContaining({
      fromDate: '2026-09-10',
      toDate: '2026-09-15',
    }),
  );
});

it('renders zero and empty states without crashing', () => {
  mockUseChatAnalytics.mockReturnValue({
    data: {
      ...fixture,
      total_count: 0,
      last_30_days_count: 0,
      today_count: 0,
      trend: [],
      assistants: [],
    },
    loading: false,
    error: null,
    refetch,
  });

  renderDashboard();

  expect(screen.getAllByText('0')).toHaveLength(4);
  expect(screen.getByText('暂无数据')).toBeInTheDocument();
});

it('shows loading placeholders while data is being fetched', () => {
  mockUseChatAnalytics.mockReturnValue({
    data: fixture,
    loading: true,
    error: null,
    refetch,
  });

  renderDashboard();

  expect(screen.getAllByTestId('summary-skeleton')).toHaveLength(3);
});

it('offers a retry action after a request error', () => {
  mockUseChatAnalytics.mockReturnValue({
    data: fixture,
    loading: false,
    error: new Error('network failure'),
    refetch,
  });

  renderDashboard();
  fireEvent.click(screen.getByRole('button', { name: 'Retry' }));

  expect(screen.getByText('Unable to load analytics')).toBeInTheDocument();
  expect(refetch).toHaveBeenCalledTimes(1);
});
