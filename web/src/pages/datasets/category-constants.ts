export const ALL_CATEGORY = 'all';
export const UNCATEGORIZED_CATEGORY = 'uncategorized';

export type DatasetCategorySelection = string;

export const parseCategorySearchParam = (
  value: string | null,
): DatasetCategorySelection => value || ALL_CATEGORY;

export const toCategoryRequestFilter = (
  selection: DatasetCategorySelection,
): string | undefined => (selection === ALL_CATEGORY ? undefined : selection);
