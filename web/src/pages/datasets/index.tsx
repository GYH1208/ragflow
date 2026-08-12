import { CardContainer } from '@/components/card-container';
import { EmptyCardType } from '@/components/empty/constant';
import { EmptyAppCard } from '@/components/empty/empty';
import ListFilterBar from '@/components/list-filter-bar';
import { RenameDialog } from '@/components/rename-dialog';
import { Button } from '@/components/ui/button';
import { RAGFlowPagination } from '@/components/ui/ragflow-pagination';
import { useFetchNextKnowledgeListByPage } from '@/hooks/use-knowledge-request';
import { useFetchUserInfo } from '@/hooks/use-user-setting-request';
import { useQueryClient } from '@tanstack/react-query';
import { pick } from 'lodash';
import { Plus } from 'lucide-react';
import { useCallback, useEffect, useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { useSearchParams } from 'react-router';
import {
  ALL_CATEGORY,
  UNCATEGORIZED_CATEGORY,
  parseCategorySearchParam,
  toCategoryRequestFilter,
} from './category-constants';
import { DatasetCard } from './dataset-card';
import { DatasetCategorySidebar } from './dataset-category-sidebar';
import { DatasetCreatingDialog } from './dataset-creating-dialog';
import { useSaveKnowledge } from './hooks';
import { useDatasetCategories } from './use-dataset-categories';
import { useRenameDataset } from './use-rename-dataset';
import { useSelectOwners } from './use-select-owners';

export default function Datasets() {
  const { t } = useTranslation();
  const { data: userInfo } = useFetchUserInfo();
  const [searchUrl, setSearchUrl] = useSearchParams();
  const selectedCategory = parseCategorySearchParam(searchUrl.get('category'));
  const categoryFilter = toCategoryRequestFilter(selectedCategory);
  const {
    visible,
    hideModal,
    showModal,
    onCreateOk,
    loading: creatingLoading,
  } = useSaveKnowledge();

  const {
    kbs,
    total_datasets,
    pagination,
    setPagination,
    handleInputChange,
    searchString,
    filterValue,
    handleFilterSubmit,
  } = useFetchNextKnowledgeListByPage(categoryFilter);

  const ownerIds = (filterValue.owner as string[] | undefined) ?? [];
  const {
    summary: categorySummary,
    loading: categoriesLoading,
    error: categoriesError,
    createCategory,
    renameCategory,
    deleteCategory,
    moveDataset,
    creating: categoryCreating,
    renaming: categoryRenaming,
    deleting: categoryDeleting,
    moving: datasetMoving,
  } = useDatasetCategories(ownerIds);

  const owners = useSelectOwners();

  const {
    datasetRenameLoading,
    initialDatasetName,
    onDatasetRenameOk,
    datasetRenameVisible,
    hideDatasetRenameModal,
    showDatasetRenameModal,
  } = useRenameDataset();

  const handlePageChange = useCallback(
    (page: number, pageSize?: number) => {
      setPagination({ page, pageSize });
    },
    [setPagination],
  );
  const isCreate = searchUrl.get('isCreate') === 'true';
  const queryClient = useQueryClient();
  useEffect(() => {
    if (isCreate) {
      queryClient.invalidateQueries({ queryKey: ['tenantInfo'] });
      showModal();
      const nextSearchUrl = new URLSearchParams(searchUrl);
      nextSearchUrl.delete('isCreate');
      setSearchUrl(nextSearchUrl);
    }
  }, [isCreate, showModal, searchUrl, setSearchUrl, queryClient]);

  const handleCategorySelect = useCallback(
    (categoryId: string) => {
      const nextSearchUrl = new URLSearchParams(searchUrl);
      if (categoryId === ALL_CATEGORY) {
        nextSearchUrl.delete('category');
      } else {
        nextSearchUrl.set('category', categoryId);
      }
      nextSearchUrl.set('page', '1');
      setSearchUrl(nextSearchUrl);
    },
    [searchUrl, setSearchUrl],
  );

  useEffect(() => {
    if (
      categoriesLoading ||
      categoriesError ||
      selectedCategory === ALL_CATEGORY ||
      selectedCategory === UNCATEGORIZED_CATEGORY
    ) {
      return;
    }
    if (!categorySummary.categories.some(({ id }) => id === selectedCategory)) {
      handleCategorySelect(ALL_CATEGORY);
    }
  }, [
    categoriesError,
    categoriesLoading,
    categorySummary.categories,
    handleCategorySelect,
    selectedCategory,
  ]);

  const handleRenameCategory = useCallback(
    (id: string, name: string) => renameCategory({ id, name }),
    [renameCategory],
  );

  const handleDeleteCategory = useCallback(
    async (id: string) => {
      const result = await deleteCategory(id);
      if (selectedCategory === id) {
        handleCategorySelect(UNCATEGORIZED_CATEGORY);
      }
      return result;
    },
    [deleteCategory, handleCategorySelect, selectedCategory],
  );

  const handleMoveDataset = useCallback(
    (datasetId: string, categoryId: string | null) =>
      moveDataset({ datasetId, categoryId }),
    [moveDataset],
  );

  const manageableCategories = useMemo(
    () => categorySummary.categories.filter(({ can_manage }) => can_manage),
    [categorySummary.categories],
  );
  const defaultCategoryId = manageableCategories.some(
    ({ id }) => id === selectedCategory,
  )
    ? selectedCategory
    : null;
  const categoryMutationLoading =
    categoryCreating || categoryRenaming || categoryDeleting || datasetMoving;

  return (
    <>
      <article className="size-full flex flex-col" data-testid="datasets-list">
        <header className="px-5 pt-8 mb-4">
          <ListFilterBar
            title={t('header.dataset')}
            searchString={searchString}
            onSearchChange={handleInputChange}
            value={filterValue}
            filters={owners}
            onChange={handleFilterSubmit}
            icon={'datasets'}
          >
            <Button onClick={showModal}>
              <Plus className="size-[1em]" />
              {t('knowledgeList.createKnowledgeBase')}
            </Button>
          </ListFilterBar>
        </header>

        <div className="flex min-h-0 flex-1 border-t border-border-button">
          <DatasetCategorySidebar
            selected={selectedCategory}
            summary={categorySummary}
            loading={categoriesLoading || categoryMutationLoading}
            onSelect={handleCategorySelect}
            onCreate={createCategory}
            onRename={handleRenameCategory}
            onDelete={handleDeleteCategory}
          />
          <section className="flex min-w-0 flex-1 flex-col pt-4">
            {kbs?.length ? (
              <>
                <CardContainer className="flex-1 overflow-auto px-5">
                  {kbs.map((dataset) => (
                    <DatasetCard
                      dataset={dataset}
                      key={dataset.id}
                      categories={categorySummary.categories}
                      canMove={dataset.tenant_id === userInfo.id}
                      onMove={handleMoveDataset}
                      showDatasetRenameModal={showDatasetRenameModal}
                    />
                  ))}
                </CardContainer>

                <footer className="mt-4 px-5 pb-5">
                  <RAGFlowPagination
                    {...pick(pagination, 'current', 'pageSize')}
                    total={total_datasets}
                    onChange={handlePageChange}
                  />
                </footer>
              </>
            ) : (
              <div className="flex-1 flex items-center justify-center">
                <EmptyAppCard
                  showIcon
                  size="large"
                  className="w-[480px] p-14"
                  isSearch={Boolean(
                    searchString || selectedCategory !== ALL_CATEGORY,
                  )}
                  type={EmptyCardType.Dataset}
                  onClick={() => showModal()}
                />
              </div>
            )}
          </section>
        </div>
      </article>
      {visible && (
        <DatasetCreatingDialog
          hideModal={hideModal}
          onOk={onCreateOk}
          loading={creatingLoading}
          categories={manageableCategories}
          defaultCategoryId={defaultCategoryId}
        ></DatasetCreatingDialog>
      )}
      {datasetRenameVisible && (
        <RenameDialog
          hideModal={hideDatasetRenameModal}
          onOk={onDatasetRenameOk}
          initialName={initialDatasetName}
          loading={datasetRenameLoading}
        ></RenameDialog>
      )}
    </>
  );
}
