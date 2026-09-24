import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { renderHook, waitFor } from '@testing-library/react';
import { createElement } from 'react';

import chatAnalyticsService from '@/services/chat-analytics-service';

import { useChatAnalytics } from '../use-chat-analytics';

jest.mock('@/services/chat-analytics-service', () => ({
  __esModule: true,
  default: { getDashboard: jest.fn() },
}));

const mockedGetDashboard = jest.mocked(chatAnalyticsService.getDashboard);

const fixture = {
  total_count: 1149,
  last_30_days_count: 546,
  today_count: 21,
  trend: [{ date: '2026-09-24', count: 21 }],
  assistants: [{ id: 'd1', name: 'Support', count: 1149 }],
  assistant_options: [{ id: 'd1', name: 'Support' }],
};

const renderAnalyticsHook = () => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const wrapper = ({ children }: { children?: React.ReactNode }) =>
    createElement(QueryClientProvider, { client: queryClient }, children);
  const params = {
    dialogId: 'd1',
    fromDate: '2026-09-01',
    toDate: '2026-09-24',
    granularity: 'week' as const,
  };

  return {
    ...renderHook(() => useChatAnalytics(params), { wrapper }),
    params,
  };
};

beforeEach(() => {
  jest.clearAllMocks();
});

test('sends all dashboard filters and returns the aggregated response', async () => {
  mockedGetDashboard.mockResolvedValue({
    data: { code: 0, data: fixture },
  } as never);

  const { result } = renderAnalyticsHook();

  await waitFor(() => expect(result.current.loading).toBe(false));

  expect(mockedGetDashboard).toHaveBeenCalledWith({
    params: {
      dialog_id: 'd1',
      from_date: '2026-09-01',
      to_date: '2026-09-24',
      granularity: 'week',
    },
  });
  expect(result.current.data).toEqual(fixture);
});

test('exposes request errors and keeps a retry function', async () => {
  const failure = new Error('Unable to load analytics');
  mockedGetDashboard.mockRejectedValue(failure);

  const { result, params } = renderAnalyticsHook();

  await waitFor(() => expect(result.current.error).toBe(failure));

  expect(result.current.data.total_count).toBe(0);
  expect(result.current.refetch).toEqual(expect.any(Function));
  expect(params).toEqual({
    dialogId: 'd1',
    fromDate: '2026-09-01',
    toDate: '2026-09-24',
    granularity: 'week',
  });
});
