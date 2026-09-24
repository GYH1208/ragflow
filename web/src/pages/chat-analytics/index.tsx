import { Button } from '@/components/ui/button';
import { DatePickerWithRange } from '@/components/ui/range-picker';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { useChatAnalytics } from '@/hooks/use-chat-analytics';
import { ChatAnalyticsGranularity } from '@/interfaces/chat-analytics';
import { format, startOfDay, subDays } from 'date-fns';
import { useMemo, useState } from 'react';
import { DateRange } from 'react-day-picker';
import { useTranslation } from 'react-i18next';
import { AssistantDonutChart } from './assistant-donut-chart';
import { SummaryCards } from './summary-cards';
import { TrendChart } from './trend-chart';

const allAssistantsValue = '__all__';

export default function ChatAnalyticsPage() {
  const { t } = useTranslation();
  const defaultRange = useMemo(() => {
    const today = startOfDay(new Date());
    return { from: subDays(today, 29), to: today };
  }, []);
  const [range, setRange] = useState<DateRange>(defaultRange);
  const [dialogId, setDialogId] = useState<string>();
  const [granularity, setGranularity] =
    useState<ChatAnalyticsGranularity>('day');
  const from = range.from ?? defaultRange.from;
  const to = range.to ?? from;
  const query = useChatAnalytics({
    dialogId,
    fromDate: format(from, 'yyyy-MM-dd'),
    toDate: format(to, 'yyyy-MM-dd'),
    granularity,
  });

  return (
    <main className="h-full overflow-auto bg-bg-base p-4 sm:p-6">
      <div className="mx-auto flex max-w-[1680px] flex-col gap-4">
        <h1 className="text-2xl font-semibold text-text-primary">
          {t('chatAnalytics.title')}
        </h1>

        <SummaryCards
          total={query.data.total_count}
          last30Days={query.data.last_30_days_count}
          today={query.data.today_count}
          loading={query.loading}
        />

        {query.error ? (
          <section
            className="flex min-h-64 flex-col items-center justify-center gap-4 rounded-lg border-0.5 border-border-button bg-bg-input"
            role="alert"
          >
            <p className="text-text-secondary">
              {t('chatAnalytics.loadError')}
            </p>
            <Button
              type="button"
              variant="accent"
              onClick={() => query.refetch()}
            >
              {t('chatAnalytics.retry')}
            </Button>
          </section>
        ) : (
          <>
            <TrendChart
              data={query.data.trend}
              granularity={granularity}
              onGranularityChange={setGranularity}
            />

            <section className="flex flex-wrap items-center gap-3 rounded-lg border-0.5 border-border-button bg-bg-input px-4 py-3 shadow-sm">
              <label
                className="text-sm font-medium text-text-primary"
                htmlFor="chat-analytics-assistant"
              >
                {t('chatAnalytics.assistant')}
              </label>
              <Select
                value={dialogId ?? allAssistantsValue}
                onValueChange={(value) =>
                  setDialogId(value === allAssistantsValue ? undefined : value)
                }
              >
                <SelectTrigger
                  id="chat-analytics-assistant"
                  className="w-56"
                  aria-label={t('chatAnalytics.assistant')}
                >
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value={allAssistantsValue}>
                    {t('chatAnalytics.allAssistants')}
                  </SelectItem>
                  {query.data.assistant_options.map((assistant) => (
                    <SelectItem key={assistant.id} value={assistant.id}>
                      {assistant.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>

              <span className="ml-0 text-sm font-medium text-text-primary sm:ml-3">
                {t('chatAnalytics.dateRange')}
              </span>
              <DatePickerWithRange
                required
                selected={range}
                onSelect={(nextRange, selectedDay) => {
                  if (range.to) {
                    setRange({ from: selectedDay, to: undefined });
                    return;
                  }
                  setRange(nextRange);
                }}
              />
            </section>

            <AssistantDonutChart assistants={query.data.assistants} />
          </>
        )}
      </div>
    </main>
  );
}
