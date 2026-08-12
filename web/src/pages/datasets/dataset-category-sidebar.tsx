import { ConfirmDeleteDialog } from '@/components/confirm-delete-dialog';
import { MoreButton } from '@/components/more-button';
import { Button } from '@/components/ui/button';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { ScrollArea } from '@/components/ui/scroll-area';
import {
  IDatasetCategory,
  IDatasetCategorySummary,
} from '@/interfaces/database/dataset';
import { cn } from '@/lib/utils';
import {
  Folder,
  FolderOpen,
  LibraryBig,
  PenLine,
  Plus,
  Trash2,
} from 'lucide-react';
import React, { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ALL_CATEGORY, UNCATEGORIZED_CATEGORY } from './category-constants';
import { DatasetCategoryDialog } from './dataset-category-dialog';

type DatasetCategorySidebarProps = {
  selected: string;
  summary: IDatasetCategorySummary;
  loading?: boolean;
  onSelect: (categoryId: string) => void;
  onCreate: (name: string) => Promise<unknown>;
  onRename: (id: string, name: string) => Promise<unknown>;
  onDelete: (id: string) => Promise<unknown>;
};

type CategoryRowProps = {
  label: string;
  count: number;
  selected: boolean;
  icon: React.ReactNode;
  onSelect: () => void;
  category?: IDatasetCategory;
  onRename?: () => void;
  onDelete?: () => Promise<unknown>;
};

function CategoryRow({
  label,
  count,
  selected,
  icon,
  onSelect,
  category,
  onRename,
  onDelete,
}: CategoryRowProps) {
  const { t } = useTranslation();

  return (
    <div
      className={cn(
        'group flex h-9 items-center rounded-md text-sm text-text-secondary',
        selected && 'bg-bg-card text-text-primary',
      )}
    >
      <button
        type="button"
        className="flex min-w-0 flex-1 items-center gap-2 px-2 text-left"
        onClick={onSelect}
        aria-current={selected ? 'page' : undefined}
      >
        <span className="shrink-0">{icon}</span>
        <span className="min-w-0 flex-1 truncate">{label}</span>
        <span className="shrink-0 text-xs text-text-tertiary">{count}</span>
      </button>
      {category?.can_manage && onRename && onDelete && (
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <MoreButton
              className="mr-2 size-5 shrink-0"
              aria-label={t('knowledgeList.categoryActions', { name: label })}
            />
          </DropdownMenuTrigger>
          <DropdownMenuContent align="start">
            <DropdownMenuItem onSelect={onRename}>
              {t('common.rename')} <PenLine />
            </DropdownMenuItem>
            <DropdownMenuSeparator />
            <ConfirmDeleteDialog
              title={t('knowledgeList.deleteCategory')}
              content={{
                title: t('knowledgeList.deleteCategoryDescription', {
                  count,
                }),
              }}
              onOk={onDelete}
            >
              <DropdownMenuItem
                className="text-state-error"
                onSelect={(event) => event.preventDefault()}
              >
                {t('common.delete')} <Trash2 />
              </DropdownMenuItem>
            </ConfirmDeleteDialog>
          </DropdownMenuContent>
        </DropdownMenu>
      )}
    </div>
  );
}

export function DatasetCategorySidebar({
  selected,
  summary,
  loading = false,
  onSelect,
  onCreate,
  onRename,
  onDelete,
}: DatasetCategorySidebarProps) {
  const { t } = useTranslation();
  const [creating, setCreating] = useState(false);
  const [renaming, setRenaming] = useState<IDatasetCategory>();

  return (
    <aside className="flex h-full w-60 shrink-0 flex-col border-r border-border-button bg-bg-base px-3 py-4">
      <div className="mb-2 flex h-8 items-center justify-between px-2">
        <span className="text-sm font-medium text-text-primary">
          {t('knowledgeList.categories')}
        </span>
        <Button
          variant="ghost"
          size="icon-xs"
          aria-label={t('knowledgeList.createCategory')}
          onClick={() => setCreating(true)}
        >
          <Plus />
        </Button>
      </div>

      <ScrollArea className={cn('min-h-0 flex-1', loading && 'opacity-60')}>
        <div className="space-y-1 pr-1">
          <CategoryRow
            label={t('knowledgeList.all')}
            count={summary.total_count}
            selected={selected === ALL_CATEGORY}
            icon={<LibraryBig className="size-4" />}
            onSelect={() => onSelect(ALL_CATEGORY)}
          />
          <CategoryRow
            label={t('knowledgeList.uncategorized')}
            count={summary.uncategorized_count}
            selected={selected === UNCATEGORIZED_CATEGORY}
            icon={<FolderOpen className="size-4" />}
            onSelect={() => onSelect(UNCATEGORIZED_CATEGORY)}
          />
          <div className="my-2 h-px bg-border-button" />
          {summary.categories.map((category) => (
            <CategoryRow
              key={category.id}
              label={category.name}
              count={category.count}
              selected={selected === category.id}
              icon={<Folder className="size-4" />}
              category={category}
              onSelect={() => onSelect(category.id)}
              onRename={() => setRenaming(category)}
              onDelete={() => onDelete(category.id)}
            />
          ))}
        </div>
      </ScrollArea>

      <DatasetCategoryDialog
        open={creating}
        title={t('knowledgeList.createCategory')}
        loading={loading}
        onOpenChange={setCreating}
        onSubmit={onCreate}
      />
      <DatasetCategoryDialog
        open={Boolean(renaming)}
        title={t('knowledgeList.renameCategory')}
        initialName={renaming?.name}
        loading={loading}
        onOpenChange={(open) => !open && setRenaming(undefined)}
        onSubmit={(name) => onRename(renaming!.id, name)}
      />
    </aside>
  );
}
