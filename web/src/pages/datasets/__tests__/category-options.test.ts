import { buildDatasetCategoryOptions } from '../category-options';

describe('buildDatasetCategoryOptions', () => {
  it('includes uncategorized and only categories owned by the dataset team', () => {
    expect(
      buildDatasetCategoryOptions(
        [
          {
            id: 'cat-1',
            tenant_id: 'tenant-1',
            name: '研发',
            count: 2,
            can_manage: true,
          },
          {
            id: 'cat-2',
            tenant_id: 'tenant-2',
            name: '销售',
            count: 1,
            can_manage: false,
          },
        ],
        'tenant-1',
        '未分类',
      ),
    ).toEqual([
      { value: null, label: '未分类' },
      { value: 'cat-1', label: '研发' },
    ]);
  });
});
