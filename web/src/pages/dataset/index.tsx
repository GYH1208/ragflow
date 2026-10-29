import { useFetchKnowledgeBaseConfiguration } from '@/hooks/use-knowledge-request';
import { KnowledgeBaseProvider } from '@/pages/dataset/contexts/knowledge-base-context';

import { Outlet } from 'react-router';
import { SideBar } from './sidebar';

export default function DatasetWrapper() {
  const { data, loading } = useFetchKnowledgeBaseConfiguration();

  return (
    <KnowledgeBaseProvider knowledgeBase={data} loading={loading}>
      <article className="flex size-full min-w-0 flex-col pt-3 lg:grid lg:grid-cols-[auto_minmax(0,1fr)] lg:grid-rows-1">
        <SideBar dataset={data} />

        <Outlet />
      </article>
    </KnowledgeBaseProvider>
  );
}
