jest.mock('@/hooks/use-knowledge-request', () => ({
  KnowledgeApiAction: { FetchKnowledgeListByPage: 'fetchKnowledgeListByPage' },
}));

jest.mock('@/components/ui/message', () => ({
  __esModule: true,
  default: { error: jest.fn() },
}));

jest.mock('@/services/knowledge-service', () => ({}));

import * as categoryHooks from '../use-dataset-categories';

describe('dataset category error localization', () => {
  it('shows a localized duplicate-name error instead of the server English text', () => {
    const getErrorMessage = (categoryHooks as any)
      .getDatasetCategoryErrorMessage;

    expect(typeof getErrorMessage).toBe('function');
    expect(
      getErrorMessage(
        { data: { message: 'Dataset category name already exists' } },
        (key: string) =>
          ({
            'knowledgeList.categoryNameExists': '分类名称已存在',
            'knowledgeList.createCategoryFailed': '新建分类失败',
          })[key] ?? key,
        'knowledgeList.createCategoryFailed',
      ),
    ).toBe('分类名称已存在');
  });
});
