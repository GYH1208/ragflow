import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { useTranslation } from 'react-i18next';

interface AssistantTableProps {
  assistants: Array<{ id: string; name: string; count: number }>;
}

export function AssistantTable({ assistants }: AssistantTableProps) {
  const { t } = useTranslation();

  return (
    <section className="overflow-hidden rounded-lg border-0.5 border-border-button bg-bg-input shadow-sm">
      <Table rootClassName="rounded-none">
        <TableHeader>
          <TableRow>
            <TableHead>{t('chatAnalytics.assistantName')}</TableHead>
            <TableHead className="text-right">
              {t('chatAnalytics.totalQuestions')}
            </TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {assistants.length ? (
            assistants.map((assistant) => (
              <TableRow key={assistant.id}>
                <TableCell>{assistant.name}</TableCell>
                <TableCell className="text-right tabular-nums">
                  {assistant.count.toLocaleString()}
                </TableCell>
              </TableRow>
            ))
          ) : (
            <TableRow>
              <TableCell colSpan={2} className="h-24 text-center text-text-secondary">
                {t('chatAnalytics.noData')}
              </TableCell>
            </TableRow>
          )}
        </TableBody>
      </Table>
    </section>
  );
}
