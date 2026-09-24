import { useChatAnalytics } from '@/hooks/use-chat-analytics';
import { fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import ChatAnalyticsPage from '..';

const React = jest.requireActual<typeof import('react')>('react');
(globalThis as typeof globalThis & { React: typeof React }).React = React;

jest.mock('@/hooks/use-chat-analytics', () => ({
  useChatAnalytics: jest.fn(),
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
const refetch = jest.fn();

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
  mockUseChatAnalytics.mockReturnValue({
    data: fixture,
    loading: false,
    error: null,
    refetch,
  });
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
