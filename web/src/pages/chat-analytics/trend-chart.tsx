import { Button } from '@/components/ui/button';
import { ChatAnalyticsGranularity } from '@/interfaces/chat-analytics';
import { useTranslation } from 'react-i18next';
import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';

interface TrendChartProps {
  data: Array<{ date: string; count: number }>;
  granularity: ChatAnalyticsGranularity;
  onGranularityChange: (value: ChatAnalyticsGranularity) => void;
}

export function TrendChart({
  data,
  granularity,
  onGranularityChange,
}: TrendChartProps) {
  const { t } = useTranslation();
  const choices: Array<{
    value: ChatAnalyticsGranularity;
    label: string;
  }> = [
    { value: 'day', label: t('chatAnalytics.byDay') },
    { value: 'week', label: t('chatAnalytics.byWeek') },
    { value: 'month', label: t('chatAnalytics.byMonth') },
  ];

  return (
    <section className="rounded-lg border-0.5 border-border-button bg-bg-input shadow-sm">
      <header className="flex flex-wrap items-center justify-between gap-3 border-b-0.5 border-border-button px-5 py-4">
        <h2 className="font-medium text-text-primary">
          {t('chatAnalytics.trendTitle')}
        </h2>
        <div
          className="inline-flex overflow-hidden rounded-md border-0.5 border-border-button"
          role="group"
          aria-label={t('chatAnalytics.trendTitle')}
        >
          {choices.map(({ value, label }) => (
            <Button
              key={value}
              type="button"
              variant={granularity === value ? 'accent' : 'secondary'}
              className="rounded-none border-0 border-r-0.5 border-border-button last:border-r-0"
              aria-pressed={granularity === value}
              onClick={() => onGranularityChange(value)}
            >
              {label}
            </Button>
          ))}
        </div>
      </header>
      <div className="h-72 px-3 pb-3 pt-5 sm:h-80">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={data} margin={{ left: -10, right: 14, top: 8 }}>
            <CartesianGrid strokeDasharray="3 3" vertical={false} />
            <XAxis dataKey="date" tickLine={false} axisLine={false} />
            <YAxis allowDecimals={false} tickLine={false} axisLine={false} />
            <Tooltip />
            <Line
              type="monotone"
              dataKey="count"
              stroke="#1677ff"
              strokeWidth={2.5}
              dot={{ r: 3, fill: '#1677ff' }}
              activeDot={{ r: 5 }}
            />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </section>
  );
}
