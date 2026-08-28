import { render, screen } from '@testing-library/react';

import { useFetchUserInfo } from '@/hooks/use-user-setting-request';

import { SideBar } from '../sidebar';

const React = jest.requireActual<typeof import('react')>('react');
(globalThis as any).React = React;

jest.mock('@/hooks/use-knowledge-request', () => ({
  useFetchKnowledgeGraph: () => ({ data: { graph: {} }, loading: false }),
}));

jest.mock('@/components/icon-font', () => ({
  IconFontFill: () => <span />,
}));

jest.mock('@/routes', () => ({
  Routes: {
    DatasetBase: '/dataset',
    Files: '/files',
    DatasetTesting: '/retrieval',
    DataSetOverview: '/logs',
    DataSetSetting: '/configuration',
    KnowledgeGraph: '/knowledge-graph',
  },
}));

jest.mock('@/hooks/route-hook', () => ({
  useSecondPathName: () => 'files',
}));

jest.mock('@/hooks/use-user-setting-request', () => ({
  useFetchUserInfo: jest.fn(),
}));

jest.mock('react-router', () => ({
  Link: ({ children, to, ...props }: any) => (
    <a href={to} {...props}>
      {children}
    </a>
  ),
  useParams: () => ({ id: 'kb-1' }),
}));

jest.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) =>
      ({
        'knowledgeDetails.subbarFiles': '文件列表',
        'knowledgeDetails.testing': '检索测试',
        'knowledgeDetails.overview': '日志',
        'knowledgeDetails.configuration': '配置',
        'knowledgeDetails.knowledgeGraph': '知识图谱',
        'knowledgeDetails.files': '个文件',
        'knowledgeDetails.created': '创建于',
      })[key] ?? key,
  }),
}));

const useFetchUserInfoMock = useFetchUserInfo as jest.Mock;

const dataset = {
  avatar: '',
  chunk_count: 0,
  chunk_method: 'naive',
  category_id: null,
  create_date: '2026-08-06',
  create_time: 1785988800000,
  created_by: 'owner-1',
  description: 'HR policies',
  document_count: 3,
  embedding_model: 'bge-m3',
  size: 8 * 1024 * 1024,
  graphrag_task_finish_at: '',
  graphrag_task_id: null,
  id: 'kb-1',
  language: 'Chinese',
  mindmap_task_finish_at: null,
  mindmap_task_id: null,
  name: '人事制度知识库',
  nickname: 'owner',
  pagerank: 0,
  parser_config: {},
  permission: 'team',
  pipeline_id: '',
  raptor_task_finish_at: '',
  raptor_task_id: '',
  similarity_threshold: 0.2,
  status: '1',
  tenant_avatar: '',
  tenant_embd_id: 1,
  tenant_id: 'owner-1',
  team_id: 'team-1',
  team_name: 'HR 团队',
  token_num: 0,
  update_date: '2026-08-06',
  update_time: 1785988800000,
  vector_similarity_weight: 0.3,
  connectors: [],
};

const owner = {
  access_token: 'owner-token',
  avatar: '',
  color_schema: 'Bright',
  create_date: '2026-08-01',
  create_time: 1785556800000,
  email: 'owner@example.com',
  id: 'owner-1',
  is_active: '1',
  is_anonymous: '0',
  is_authenticated: '1',
  is_superuser: true,
  language: 'Chinese',
  last_login_time: '2026-08-28',
  login_channel: 'password',
  nickname: 'owner',
  password: '',
  status: '1',
  timezone: 'UTC+8\tAsia/Shanghai',
  update_date: '2026-08-28',
  update_time: 1787884800000,
};

function renderSidebar(userId: string) {
  useFetchUserInfoMock.mockReturnValue({
    data: { ...owner, id: userId },
    loading: false,
  });

  return render(<SideBar dataset={dataset as any} />);
}

describe('dataset owner-only configuration navigation', () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  it('shows the configuration entry to the knowledge base owner', () => {
    renderSidebar('owner-1');

    expect(screen.getByRole('link', { name: '配置' })).toBeInTheDocument();
  });

  it('hides the configuration entry from a non-owner team member', () => {
    renderSidebar('member-1');

    expect(
      screen.queryByRole('link', { name: '配置' }),
    ).not.toBeInTheDocument();
  });
});
