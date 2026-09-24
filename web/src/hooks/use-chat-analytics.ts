import {
  ChatAnalyticsParams,
  ChatAnalyticsResponse,
} from '@/interfaces/chat-analytics';
import chatAnalyticsService from '@/services/chat-analytics-service';
import { useQuery } from '@tanstack/react-query';

const emptyAnalytics: ChatAnalyticsResponse = {
  total_count: 0,
  last_30_days_count: 0,
  today_count: 0,
  trend: [],
  assistants: [],
  assistant_options: [],
};

export const useChatAnalytics = (params: ChatAnalyticsParams) => {
  const query = useQuery<ChatAnalyticsResponse, Error>({
    queryKey: ['chatAnalytics', params],
    initialData: emptyAnalytics,
    refetchOnWindowFocus: false,
    queryFn: async () => {
      const response = await chatAnalyticsService.getDashboard(
        {
          params: {
            dialog_id: params.dialogId,
            from_date: params.fromDate,
            to_date: params.toDate,
            granularity: params.granularity,
          },
        },
      );
      const payload = response.data;
      if (payload.code !== 0) {
        throw new Error(payload.message || 'Unable to load chat analytics');
      }
      return payload.data;
    },
  });

  return {
    data: query.data,
    loading: query.isFetching,
    error: query.error,
    refetch: query.refetch,
  };
};
