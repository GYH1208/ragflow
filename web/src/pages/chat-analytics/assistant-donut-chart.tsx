import { Cell, Pie, PieChart, ResponsiveContainer, Tooltip } from 'recharts';

interface AssistantDonutChartProps {
  assistants: Array<{ id: string; name: string; count: number }>;
}

const palette = [
  '#2FC49F',
  '#4C8BF5',
  '#FFA63B',
  '#756AF5',
  '#50B5D8',
  '#E86788',
  '#35B89A',
  '#5B8FF9',
];

export function AssistantDonutChart({ assistants }: AssistantDonutChartProps) {
  const total = assistants.reduce((sum, assistant) => sum + assistant.count, 0);
  const colorById = new Map(
    [...assistants]
      .sort((left, right) => left.id.localeCompare(right.id))
      .map((assistant, index) => [
        assistant.id,
        palette[index % palette.length],
      ]),
  );
  const chartData = total
    ? assistants
        .filter((assistant) => assistant.count > 0)
        .map((assistant) => ({
          ...assistant,
          color: colorById.get(assistant.id),
        }))
    : [{ id: 'empty', name: '暂无数据', count: 1, color: '#E5E7EB' }];

  return (
    <section className="overflow-hidden rounded-lg border-0.5 border-border-button bg-bg-input shadow-sm">
      <header className="border-b-0.5 border-border-button px-5 py-4">
        <h2 className="font-medium text-text-primary">聊天助理问答分布</h2>
        <p className="mt-1 text-sm text-text-secondary">按当前筛选范围统计</p>
      </header>

      <div className="grid min-h-72 gap-6 px-5 py-6 md:grid-cols-[minmax(240px,0.8fr)_minmax(320px,1.2fr)] md:items-center">
        <div
          className="relative mx-auto h-56 w-full max-w-72"
          role="img"
          aria-label={`聊天助理问答分布，总计 ${total.toLocaleString()} 次`}
        >
          <ResponsiveContainer width="100%" height="100%">
            <PieChart>
              <Pie
                data={chartData}
                dataKey="count"
                nameKey="name"
                cx="50%"
                cy="50%"
                innerRadius={66}
                outerRadius={88}
                paddingAngle={total ? 2 : 0}
                stroke="var(--bg-input)"
                strokeWidth={2}
                isAnimationActive={false}
              >
                {chartData.map((entry) => (
                  <Cell key={entry.id} fill={entry.color} />
                ))}
              </Pie>
              {total ? (
                <Tooltip
                  formatter={(value) => [
                    Number(value).toLocaleString(),
                    '问答次数',
                  ]}
                />
              ) : null}
            </PieChart>
          </ResponsiveContainer>
          <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center">
            <span className="text-3xl font-semibold tabular-nums text-text-primary">
              {total.toLocaleString()}
            </span>
            <span className="mt-1 text-sm text-text-secondary">问答总量</span>
          </div>
        </div>

        {assistants.length ? (
          <ul className="grid gap-3" aria-label="聊天助理问答明细">
            {assistants.map((assistant) => {
              const percentage = total
                ? Math.round((assistant.count / total) * 100)
                : 0;
              return (
                <li
                  key={assistant.id}
                  className="grid grid-cols-[minmax(0,1fr)_auto_auto] items-center gap-4 text-sm"
                >
                  <span className="flex min-w-0 items-center gap-2 text-text-primary">
                    <span
                      className="size-2.5 shrink-0 rounded-full"
                      style={{ backgroundColor: colorById.get(assistant.id) }}
                      aria-hidden="true"
                    />
                    <span className="truncate" title={assistant.name}>
                      {assistant.name}
                    </span>
                  </span>
                  <span className="min-w-14 text-right font-medium tabular-nums text-text-primary">
                    {assistant.count.toLocaleString()}
                  </span>
                  <span className="min-w-12 text-right tabular-nums text-text-secondary">
                    {percentage}%
                  </span>
                </li>
              );
            })}
          </ul>
        ) : (
          <p className="text-center text-sm text-text-secondary md:text-left">
            暂无数据
          </p>
        )}
      </div>
    </section>
  );
}
