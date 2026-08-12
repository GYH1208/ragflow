import { IDatasetCategory } from '@/interfaces/database/dataset';

export type DatasetCategoryOption = {
  value: string | null;
  label: string;
};

export function buildDatasetCategoryOptions(
  categories: IDatasetCategory[],
  tenantId: string,
  uncategorizedLabel: string,
): DatasetCategoryOption[] {
  return [
    { value: null, label: uncategorizedLabel },
    ...categories
      .filter((category) => category.tenant_id === tenantId)
      .map((category) => ({ value: category.id, label: category.name })),
  ];
}
