import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { useMcpAnalytics } from '@/hooks/use-chat-analytics';
import { Activity, BookOpen, Files, RefreshCw, Search } from 'lucide-react';

const toolLabels: Record<string, string> = {
  ragflow_retrieval: '知识库检索',
  search_documents: '搜索文档',
  get_document_chunks: '读取文档片段',
  get_chunk_image: '获取片段图片',
  list_knowledge_bases: '浏览知识库',
  list_documents: '浏览文档列表',
  export_document_catalog: '导出文档目录',
};

const toolColors: Record<string, string> = {
  ragflow_retrieval: 'bg-blue-500',
  search_documents: 'bg-blue-500',
  get_document_chunks: 'bg-emerald-500',
};

export function McpDashboard() {
  const { data, loading, error, refetch } = useMcpAnalytics();

  if (loading && !data && !error) {
    return (
      <section
        aria-label="正在加载 MCP 数据"
        className="grid gap-4 md:grid-cols-4"
      >
        {Array.from({ length: 4 }, (_, index) => (
          <Skeleton key={index} className="h-32 bg-border-button" />
        ))}
      </section>
    );
  }

  if (data && !data.authorized) {
    return (
      <Card className="p-8 text-center text-text-secondary">
        仅超级管理员可查看 MCP 服务汇总数据
      </Card>
    );
  }

  if (error || !data || !data.authorized || !data.available) {
    return (
      <Card
        role="alert"
        className="flex min-h-64 flex-col items-center justify-center gap-4 p-6"
      >
        <p className="text-text-secondary">暂时无法读取 MCP 调用数据</p>
        <Button type="button" variant="accent" onClick={() => refetch()}>
          重试
        </Button>
      </Card>
    );
  }

  const cards = [
    {
      label: '累计工具调用',
      count: data.total_count,
      icon: Activity,
      style: 'bg-violet-500/10 text-violet-500',
    },
    {
      label: '知识检索',
      count: data.categories.retrieval,
      icon: Search,
      style: 'bg-blue-500/10 text-blue-500',
    },
    {
      label: '文档内容读取',
      count: data.categories.content,
      icon: BookOpen,
      style: 'bg-emerald-500/10 text-emerald-500',
    },
    {
      label: '目录、图片与其他',
      count: data.categories.other,
      icon: Files,
      style: 'bg-amber-500/10 text-amber-500',
    },
  ];
  const tools = [...data.tools].sort(
    (left, right) =>
      right.count - left.count || left.name.localeCompare(right.name),
  );
  const maximum = Math.max(1, ...tools.map((tool) => tool.count));
  const updatedAt = new Date(data.updated_at);
  const formattedTime = new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Shanghai',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  }).format(updatedAt);

  return (
    <div className="flex flex-col gap-4">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-lg font-medium text-text-primary">
            员工 MCP 调用概览
          </h2>
          <p className="mt-1 text-sm text-text-secondary">
            累计统计 · WorkBuddy 员工 MCP · 每 30 秒自动刷新
          </p>
        </div>
        <Button
          type="button"
          variant="outline"
          disabled={loading}
          onClick={() => refetch()}
          aria-label="刷新 MCP 数据"
        >
          <RefreshCw
            className={`size-4 ${loading ? 'animate-spin' : ''}`}
            aria-hidden="true"
          />
          刷新数据
        </Button>
      </header>

      <section
        className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4"
        aria-label="MCP 调用汇总"
      >
        {cards.map(({ label, count, icon: Icon, style }) => (
          <Card key={label} className="flex min-h-28 items-center gap-4 p-5">
            <span
              className={`flex size-12 shrink-0 items-center justify-center rounded-full ${style}`}
              aria-hidden="true"
            >
              <Icon className="size-6" />
            </span>
            <div className="min-w-0">
              <p className="mb-1 text-sm text-text-secondary">{label}</p>
              <p className="text-3xl font-semibold tabular-nums tracking-tight text-text-primary">
                {count.toLocaleString()}
              </p>
            </div>
          </Card>
        ))}
      </section>

      <Card as="section" className="overflow-hidden">
        <header className="flex flex-wrap items-center justify-between gap-2 border-b-0.5 border-border-button px-5 py-4">
          <h3 className="font-medium text-text-primary">各接口调用排行</h3>
          <span className="text-sm text-text-secondary">
            调用次数 / 占全部调用比例
          </span>
        </header>
        {data.total_count === 0 ? (
          <p className="px-5 py-12 text-center text-text-secondary">
            暂无 MCP 调用记录
          </p>
        ) : (
          <ol className="grid gap-5 p-5" aria-label="各接口调用次数">
            {tools.map((tool) => {
              const percentage = (
                (tool.count / data.total_count) *
                100
              ).toFixed(1);
              return (
                <li
                  key={tool.name}
                  className="grid gap-x-6 gap-y-2 sm:grid-cols-[minmax(210px,0.65fr)_minmax(0,1fr)] sm:items-center"
                >
                  <div className="min-w-0">
                    <p className="text-sm font-medium text-text-primary">
                      {Object.prototype.hasOwnProperty.call(
                        toolLabels,
                        tool.name,
                      )
                        ? toolLabels[tool.name]
                        : tool.name}
                    </p>
                    <p className="break-all font-mono text-xs text-text-secondary">
                      {tool.name}
                    </p>
                  </div>
                  <div className="flex min-w-0 items-center gap-4">
                    <div
                      className="h-3 min-w-0 flex-1 overflow-hidden rounded-full bg-border-button"
                      aria-hidden="true"
                    >
                      <div
                        className={`h-full rounded-full ${Object.prototype.hasOwnProperty.call(toolColors, tool.name) ? toolColors[tool.name] : 'bg-amber-500'}`}
                        style={{ width: `${(tool.count / maximum) * 100}%` }}
                      />
                    </div>
                    <span className="w-14 text-right text-sm font-semibold tabular-nums text-text-primary">
                      {tool.count.toLocaleString()}
                    </span>
                    <span className="w-14 text-right text-sm tabular-nums text-text-secondary">
                      {percentage}%
                    </span>
                  </div>
                </li>
              );
            })}
          </ol>
        )}
        <footer className="flex flex-col gap-2 border-t-0.5 border-border-button px-5 py-4 text-xs leading-relaxed text-text-secondary">
          <p>
            数据保存时间：
            <time dateTime={data.updated_at}>{formattedTime}</time>（北京时间）
          </p>
          <p>
            自计数功能启用后累计，包含进入执行后失败的调用；不含初始化、工具发现及执行前被拒绝的请求。一次提问可触发多次工具调用。
          </p>
        </footer>
      </Card>
    </div>
  );
}
