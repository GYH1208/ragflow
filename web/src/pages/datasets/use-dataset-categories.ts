import message from '@/components/ui/message';
import { KnowledgeApiAction } from '@/hooks/use-knowledge-request';
import { IDatasetCategorySummary } from '@/interfaces/database/dataset';
import {
  createDatasetCategory,
  deleteDatasetCategory,
  listDatasetCategories,
  moveDatasetToCategory,
  updateDatasetCategory,
} from '@/services/knowledge-service';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';

export const DatasetCategoryQueryKey = 'datasetCategories';

const emptySummary: IDatasetCategorySummary = {
  categories: [],
  total_count: 0,
  uncategorized_count: 0,
};

const categoryErrorTranslationKeys: Record<string, string> = {
  'Dataset category name already exists': 'knowledgeList.categoryNameExists',
  'Dataset category not found': 'knowledgeList.categoryNotFound',
  'No authorization to manage this dataset category':
    'knowledgeList.categoryManageForbidden',
  'No authorization to assign this dataset category':
    'knowledgeList.categoryAssignForbidden',
};

export function getDatasetCategoryErrorMessage(
  response: any,
  translate: (key: string) => string,
  fallbackKey: string,
) {
  const serverMessage = response?.data?.message || response?.message;
  const translationKey = categoryErrorTranslationKeys[serverMessage];

  if (translationKey) {
    return translate(translationKey);
  }
  return serverMessage || translate(fallbackKey);
}

export function useDatasetCategories(ownerIds: string[] = []) {
  const { t } = useTranslation();
  const translate = (key: string) => t(key);
  const queryClient = useQueryClient();
  const query = useQuery<IDatasetCategorySummary>({
    queryKey: [DatasetCategoryQueryKey, ownerIds],
    initialData: emptySummary,
    queryFn: async () => {
      const { data } = await listDatasetCategories({
        ext: { owner_ids: ownerIds },
      });
      if (data?.code !== 0) {
        throw new Error(
          getDatasetCategoryErrorMessage(
            data,
            translate,
            'knowledgeList.loadCategoriesFailed',
          ),
        );
      }
      return data?.data ?? emptySummary;
    },
  });

  const invalidate = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: [DatasetCategoryQueryKey] }),
      queryClient.invalidateQueries({
        queryKey: [KnowledgeApiAction.FetchKnowledgeListByPage],
      }),
    ]);
  };

  const createMutation = useMutation({
    mutationFn: async (name: string) => {
      const { data } = await createDatasetCategory(name.trim());
      if (data?.code !== 0) {
        throw new Error(
          getDatasetCategoryErrorMessage(
            data,
            translate,
            'knowledgeList.createCategoryFailed',
          ),
        );
      }
      return data.data;
    },
    onSuccess: invalidate,
    onError: (error: Error) => message.error(error.message),
  });

  const renameMutation = useMutation({
    mutationFn: async ({ id, name }: { id: string; name: string }) => {
      const { data } = await updateDatasetCategory(id, name.trim());
      if (data?.code !== 0) {
        throw new Error(
          getDatasetCategoryErrorMessage(
            data,
            translate,
            'knowledgeList.renameCategoryFailed',
          ),
        );
      }
      return data.data;
    },
    onSuccess: invalidate,
    onError: (error: Error) => message.error(error.message),
  });

  const deleteMutation = useMutation({
    mutationFn: async (id: string) => {
      const { data } = await deleteDatasetCategory(id);
      if (data?.code !== 0) {
        throw new Error(
          getDatasetCategoryErrorMessage(
            data,
            translate,
            'knowledgeList.deleteCategoryFailed',
          ),
        );
      }
      return data.data;
    },
    onSuccess: invalidate,
    onError: (error: Error) => message.error(error.message),
  });

  const moveMutation = useMutation({
    mutationFn: async ({
      datasetId,
      categoryId,
    }: {
      datasetId: string;
      categoryId: string | null;
    }) => {
      const { data } = await moveDatasetToCategory(datasetId, categoryId);
      if (data?.code !== 0) {
        throw new Error(
          getDatasetCategoryErrorMessage(
            data,
            translate,
            'knowledgeList.moveDatasetFailed',
          ),
        );
      }
      return data.data;
    },
    onSuccess: invalidate,
    onError: (error: Error) => message.error(error.message),
  });

  return {
    summary: query.data,
    loading: query.isFetching,
    error: query.error,
    createCategory: createMutation.mutateAsync,
    renameCategory: renameMutation.mutateAsync,
    deleteCategory: deleteMutation.mutateAsync,
    moveDataset: moveMutation.mutateAsync,
    creating: createMutation.isPending,
    renaming: renameMutation.isPending,
    deleting: deleteMutation.isPending,
    moving: moveMutation.isPending,
  };
}
