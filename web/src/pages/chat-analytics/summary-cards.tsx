import { Card } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { CalendarDays, Clock3, MessageSquareText } from 'lucide-react';
import { useTranslation } from 'react-i18next';

interface SummaryCardsProps {
  total: number;
  last30Days: number;
  today: number;
  loading: boolean;
}

const cardStyles = [
  { icon: MessageSquareText, iconClass: 'bg-blue-50 text-blue-600' },
  { icon: CalendarDays, iconClass: 'bg-emerald-50 text-emerald-600' },
  { icon: Clock3, iconClass: 'bg-orange-50 text-orange-600' },
];

export function SummaryCards({
  total,
  last30Days,
  today,
  loading,
}: SummaryCardsProps) {
  const { t } = useTranslation();
  const cards = [
    { label: t('chatAnalytics.lifetimeCount'), value: total },
    { label: t('chatAnalytics.last30DaysCount'), value: last30Days },
    { label: t('chatAnalytics.todayCount'), value: today },
  ];

  return (
    <section className="grid gap-4 md:grid-cols-3" aria-label="Summary">
      {cards.map(({ label, value }, index) => {
        const { icon: Icon, iconClass } = cardStyles[index];
        return (
          <Card key={label} className="flex min-h-28 items-center gap-4 p-5">
            <span
              className={`flex size-12 shrink-0 items-center justify-center rounded-full ${iconClass}`}
              aria-hidden="true"
            >
              <Icon className="size-6" />
            </span>
            <div className="min-w-0">
              <p className="mb-1 truncate text-sm text-text-secondary">
                {label}
              </p>
              {loading ? (
                <Skeleton
                  className="h-8 w-24 bg-border-button"
                  data-testid="summary-skeleton"
                />
              ) : (
                <p className="text-3xl font-semibold tracking-tight text-text-primary">
                  {value.toLocaleString()}
                </p>
              )}
            </div>
          </Card>
        );
      })}
    </section>
  );
}
