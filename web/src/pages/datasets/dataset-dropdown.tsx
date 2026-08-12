import {
  ConfirmDeleteDialog,
  ConfirmDeleteDialogNode,
} from '@/components/confirm-delete-dialog';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuSub,
  DropdownMenuSubContent,
  DropdownMenuSubTrigger,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { useDeleteKnowledge } from '@/hooks/use-knowledge-request';
import { IDataset, IDatasetCategory } from '@/interfaces/database/dataset';
import { Check, FolderInput, PenLine, Trash2 } from 'lucide-react';
import React, { MouseEventHandler, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import { buildDatasetCategoryOptions } from './category-options';
import { useRenameDataset } from './use-rename-dataset';

export function DatasetDropdown({
  children,
  showDatasetRenameModal,
  dataset,
  categories,
  canMove,
  onMove,
}: React.PropsWithChildren &
  Pick<ReturnType<typeof useRenameDataset>, 'showDatasetRenameModal'> & {
    dataset: IDataset;
    categories: IDatasetCategory[];
    canMove: boolean;
    onMove: (datasetId: string, categoryId: string | null) => Promise<unknown>;
  }) {
  const { t } = useTranslation();
  const { deleteKnowledge } = useDeleteKnowledge();

  const handleShowDatasetRenameModal: MouseEventHandler<HTMLDivElement> =
    useCallback(
      (e) => {
        e.stopPropagation();
        showDatasetRenameModal(dataset);
      },
      [dataset, showDatasetRenameModal],
    );

  const handleDelete: MouseEventHandler<HTMLDivElement> = useCallback(() => {
    deleteKnowledge(dataset.id);
  }, [dataset.id, deleteKnowledge]);
  const categoryOptions = buildDatasetCategoryOptions(
    categories,
    dataset.tenant_id,
    t('knowledgeList.uncategorized'),
  );

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>{children}</DropdownMenuTrigger>
      <DropdownMenuContent>
        <DropdownMenuItem onClick={handleShowDatasetRenameModal}>
          {t('common.rename')} <PenLine />
        </DropdownMenuItem>
        {canMove && (
          <DropdownMenuSub>
            <DropdownMenuSubTrigger>
              {t('knowledgeList.moveToCategory')} <FolderInput />
            </DropdownMenuSubTrigger>
            <DropdownMenuSubContent>
              {categoryOptions.map((option) => (
                <DropdownMenuItem
                  key={option.value ?? 'uncategorized'}
                  onClick={(event) => {
                    event.stopPropagation();
                  }}
                  onSelect={(event) => {
                    event.stopPropagation();
                    void onMove(dataset.id, option.value).catch(
                      () => undefined,
                    );
                  }}
                >
                  <span>{option.label}</span>
                  {(dataset.category_id ?? null) === option.value && <Check />}
                </DropdownMenuItem>
              ))}
            </DropdownMenuSubContent>
          </DropdownMenuSub>
        )}
        <DropdownMenuSeparator />
        <ConfirmDeleteDialog
          onOk={handleDelete}
          title={t('deleteModal.delDataset')}
          content={{
            node: (
              <ConfirmDeleteDialogNode
                avatar={{ avatar: dataset.avatar, name: dataset.name }}
                name={dataset.name}
              />
            ),
          }}
        >
          <DropdownMenuItem
            className="text-state-error"
            onSelect={(e) => {
              e.preventDefault();
            }}
            onClick={(e) => {
              e.stopPropagation();
            }}
          >
            {t('common.delete')} <Trash2 />
          </DropdownMenuItem>
        </ConfirmDeleteDialog>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
