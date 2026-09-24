export type ChatAnalyticsGranularity = 'day' | 'week' | 'month';

export interface ChatAnalyticsParams {
  dialogId?: string;
  fromDate: string;
  toDate: string;
  granularity: ChatAnalyticsGranularity;
}

export interface ChatAnalyticsResponse {
  total_count: number;
  last_30_days_count: number;
  today_count: number;
  trend: Array<{ date: string; count: number }>;
  assistants: Array<{ id: string; name: string; count: number }>;
  assistant_options: Array<{ id: string; name: string }>;
}
