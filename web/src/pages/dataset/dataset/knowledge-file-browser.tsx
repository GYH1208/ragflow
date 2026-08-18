import { BulkOperateBar } from '@/components/bulk-operate-bar';
import { EmptyType } from '@/components/empty/constant';
import Empty from '@/components/empty/empty';
import { FileUploadDialog } from '@/components/file-upload-dialog';
import { FileIcon } from '@/components/icon-font';
import { RenameDialog } from '@/components/rename-dialog';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader } from '@/components/ui/card';
import { Checkbox } from '@/components/ui/checkbox';
import { Input } from '@/components/ui/input';
import { RAGFlowPagination } from '@/components/ui/ragflow-pagination';
import { Switch } from '@/components/ui/switch';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { useNavigatePage } from '@/hooks/logic-hooks/navigate-hooks';
import {
  useRunDocument,
  useSetDocumentStatus,
} from '@/hooks/use-document-request';
import {
  useCreateKnowledgeFolder,
  useDeleteKnowledgeEntries,
  useKnowledgeEntries,
  useKnowledgeFolderAncestors,
  useMoveKnowledgeEntries,
  usePreviewDeleteKnowledgeEntries,
  useRenameKnowledgeEntry,
} from '@/hooks/use-knowledge-file-request';
import { KnowledgeEntry } from '@/interfaces/database/knowledge-file';
import { formatDate } from '@/utils/date';
import {
  Folder,
  FolderInput,
  Play,
  Search,
  Trash2,
  Upload,
} from 'lucide-react';
import { ChangeEvent, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useParams } from 'react-router';
import { toast } from 'sonner';
import { useKnowledgeBaseContext } from '../contexts/knowledge-base-context';
import { KnowledgeEntryActionCell } from './knowledge-entry-action-cell';
import { KnowledgeFileBreadcrumb } from './knowledge-file-breadcrumb';
import { KnowledgeMoveDialog } from './knowledge-move-dialog';
import { useHandleUploadDocument } from './use-upload-document';

export function KnowledgeFileBrowser() {
  const { t } = useTranslation();
  const { id: datasetId = '' } = useParams();
  const { knowledgeBase } = useKnowledgeBaseContext();
  const [folderId, setFolderId] = useState('');
  const [keywords, setKeywords] = useState('');
  const folderBeforeSearch = useRef('');
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [renameEntry, setRenameEntry] = useState<KnowledgeEntry>();
  const [creatingFolder, setCreatingFolder] = useState(false);
  const [moveIds, setMoveIds] = useState<string[]>([]);

  const query = useKnowledgeEntries({
    datasetId,
    folderId,
    page,
    pageSize,
    keywords,
  });
  const ancestors = useKnowledgeFolderAncestors(datasetId, folderId);
  const entries = useMemo(
    () => query.data?.entries || [],
    [query.data?.entries],
  );
  const currentFolderId = folderId || query.data?.parent_folder.id || '';

  const createFolder = useCreateKnowledgeFolder(datasetId);
  const rename = useRenameKnowledgeEntry(datasetId);
  const move = useMoveKnowledgeEntries(datasetId);
  const previewDelete = usePreviewDeleteKnowledgeEntries(datasetId);
  const remove = useDeleteKnowledgeEntries(datasetId);
  const { runDocumentByIds } = useRunDocument();
  const { setDocumentStatus } = useSetDocumentStatus();
  const { navigateToChunkParsedResult } = useNavigatePage();
  const {
    documentUploadVisible,
    showDocumentUploadModal,
    hideDocumentUploadModal,
    onDocumentUploadOk,
    documentUploadLoading,
  } = useHandleUploadDocument(currentFolderId, query.refetch);

  const selectedEntries = useMemo(
    () => entries.filter((entry) => selectedIds.includes(entry.file_id)),
    [entries, selectedIds],
  );
  const selectedContainsFolder = selectedEntries.some(
    (entry) => entry.entry_type === 'folder',
  );

  const navigateFolder = (nextFolderId: string) => {
    setFolderId(nextFolderId);
    setSelectedIds([]);
    setPage(1);
  };

  const handleSearch = (event: ChangeEvent<HTMLInputElement>) => {
    const value = event.target.value;
    if (value && !keywords) folderBeforeSearch.current = folderId;
    setKeywords(value);
    setPage(1);
    if (!value) setFolderId(folderBeforeSearch.current);
  };

  const toggleSelection = (fileId: string, checked: boolean) => {
    setSelectedIds((current) =>
      checked
        ? Array.from(new Set([...current, fileId]))
        : current.filter((id) => id !== fileId),
    );
  };

  const handleDelete = async (ids: string[]) => {
    const documentCount = await previewDelete.mutateAsync(ids);
    const confirmed = window.confirm(
      t('knowledgeFolder.deleteConfirmWithCount', { count: documentCount }),
    );
    if (!confirmed) return;
    const result = await remove.mutateAsync(ids);
    setSelectedIds([]);
    if (result.failed.length) {
      toast.error(
        t('knowledgeFolder.partialDeleteFailed', {
          deleted: result.deleted,
          failed: result.failed.length,
        }),
      );
    }
  };

  const bulkActions = [
    {
      id: 'move',
      label: t('common.move'),
      icon: <FolderInput />,
      onClick: () => setMoveIds(selectedIds),
    },
    {
      id: 'delete',
      label: t('common.delete'),
      icon: <Trash2 />,
      onClick: () => handleDelete(selectedIds),
    },
    ...(!selectedContainsFolder && selectedEntries.length
      ? [
          {
            id: 'parse',
            label: t('knowledgeDetails.run'),
            icon: <Play />,
            onClick: async () => {
              await runDocumentByIds({
                documentIds: selectedEntries.map((entry) => entry.id),
                run: 1,
              });
              query.refetch();
            },
          },
        ]
      : []),
  ];

  return (
    <Card
      as="article"
      className="mb-5 mr-5 min-w-[880px] bg-transparent shadow-none"
    >
      <CardHeader className="p-5 space-y-4">
        <div className="flex items-start justify-between gap-4">
          <div>
            <h1 className="font-medium">{t('knowledgeDetails.subbarFiles')}</h1>
            <p className="text-sm text-text-secondary">
              {t('knowledgeDetails.datasetDescription')}
            </p>
          </div>
          <div className="flex gap-2">
            <Button variant="outline" onClick={() => setCreatingFolder(true)}>
              <Folder className="size-4" />
              {t('knowledgeFolder.newFolder')}
            </Button>
            <Button onClick={showDocumentUploadModal}>
              <Upload className="size-4" />
              {t('fileManager.uploadFile')}
            </Button>
          </div>
        </div>
        <div className="flex items-center justify-between gap-4">
          <KnowledgeFileBreadcrumb
            ancestors={ancestors.data || []}
            rootName={query.data?.parent_folder.name}
            onNavigate={navigateFolder}
          />
          <div className="relative w-72">
            <Search className="absolute left-3 top-2.5 size-4 text-text-disabled" />
            <Input
              role="searchbox"
              value={keywords}
              onChange={handleSearch}
              className="pl-9"
              placeholder={t('common.search')}
            />
          </div>
        </div>
        {selectedIds.length > 0 && (
          <BulkOperateBar list={bulkActions} count={selectedIds.length} />
        )}
      </CardHeader>

      <CardContent className="px-5 py-0">
        <Table rootClassName="max-h-[calc(100vh-250px)]">
          <TableHeader>
            <TableRow>
              <TableHead className="w-10">
                <Checkbox
                  aria-label="Select all"
                  checked={
                    entries.length > 0 && selectedIds.length === entries.length
                  }
                  onCheckedChange={(checked) =>
                    setSelectedIds(
                      checked ? entries.map((entry) => entry.file_id) : [],
                    )
                  }
                />
              </TableHead>
              <TableHead>{t('knowledgeDetails.name')}</TableHead>
              <TableHead>{t('knowledgeDetails.uploadDate')}</TableHead>
              <TableHead>{t('knowledgeDetails.enabled')}</TableHead>
              <TableHead>{t('knowledgeDetails.chunkNumber')}</TableHead>
              <TableHead>{t('knowledgeDetails.Parse')}</TableHead>
              <TableHead>{t('common.action')}</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {entries.length ? (
              entries.map((entry) => (
                <TableRow key={entry.file_id} className="group">
                  <TableCell>
                    <Checkbox
                      aria-label="Select row"
                      checked={selectedIds.includes(entry.file_id)}
                      onCheckedChange={(checked) =>
                        toggleSelection(entry.file_id, Boolean(checked))
                      }
                    />
                  </TableCell>
                  <TableCell>
                    <button
                      type="button"
                      className="flex max-w-[32vw] items-center gap-2 text-left"
                      onClick={
                        entry.entry_type === 'folder'
                          ? () => navigateFolder(entry.id)
                          : navigateToChunkParsedResult(
                              entry.id,
                              entry.dataset_id,
                            )
                      }
                    >
                      {entry.entry_type === 'folder' ? (
                        <Folder className="size-5 shrink-0 text-amber-500" />
                      ) : (
                        <FileIcon name={entry.name} />
                      )}
                      <span className="min-w-0">
                        <span className="block truncate">{entry.name}</span>
                        {keywords && (
                          <span
                            className="block truncate text-xs text-text-secondary"
                            onClick={(event) => {
                              event.stopPropagation();
                              setKeywords('');
                              navigateFolder(entry.parent_id);
                            }}
                          >
                            {entry.relative_path}
                          </span>
                        )}
                      </span>
                    </button>
                  </TableCell>
                  <TableCell>{formatDate(entry.create_time)}</TableCell>
                  <TableCell>
                    {entry.entry_type === 'document' && (
                      <Switch
                        checked={entry.status === '1'}
                        onCheckedChange={async (status) => {
                          await setDocumentStatus({
                            status,
                            documentId: entry.id,
                            datasetId,
                          });
                          query.refetch();
                        }}
                      />
                    )}
                  </TableCell>
                  <TableCell>
                    {entry.entry_type === 'document' ? entry.chunk_count : null}
                  </TableCell>
                  <TableCell>
                    {entry.entry_type === 'document' && (
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={async () => {
                          await runDocumentByIds({
                            documentIds: [entry.id],
                            run: 1,
                          });
                          query.refetch();
                        }}
                      >
                        <Play className="size-4" />
                        {t('knowledgeDetails.run')}
                      </Button>
                    )}
                  </TableCell>
                  <TableCell>
                    <KnowledgeEntryActionCell
                      entry={entry}
                      onRename={setRenameEntry}
                      onMove={(item) => setMoveIds([item.file_id])}
                      onDelete={(item) => handleDelete([item.file_id])}
                    />
                  </TableCell>
                </TableRow>
              ))
            ) : (
              <TableRow>
                <TableCell colSpan={7} className="h-24 text-center">
                  <Empty type={EmptyType.Data} />
                </TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
        <div className="flex justify-end py-4">
          <RAGFlowPagination
            current={page}
            pageSize={pageSize}
            total={query.data?.total || 0}
            onChange={(nextPage, nextPageSize) => {
              setPage(nextPage);
              setPageSize(nextPageSize);
            }}
          />
        </div>
      </CardContent>

      {creatingFolder && (
        <RenameDialog
          hideModal={() => setCreatingFolder(false)}
          loading={createFolder.isPending}
          title={t('knowledgeFolder.newFolder')}
          onOk={async (name: string) => {
            await createFolder.mutateAsync({
              parent_id: currentFolderId,
              name,
            });
            setCreatingFolder(false);
            return true;
          }}
        />
      )}
      {renameEntry && (
        <RenameDialog
          initialName={renameEntry.name}
          hideModal={() => setRenameEntry(undefined)}
          loading={rename.isPending}
          onOk={async (name: string) => {
            await rename.mutateAsync({ entryId: renameEntry.file_id, name });
            setRenameEntry(undefined);
            return true;
          }}
        />
      )}
      {moveIds.length > 0 && (
        <KnowledgeMoveDialog
          datasetId={datasetId}
          loading={move.isPending}
          onClose={() => setMoveIds([])}
          onMove={async (destination_id) => {
            await move.mutateAsync({ ids: moveIds, destination_id });
            setMoveIds([]);
            setSelectedIds([]);
          }}
        />
      )}
      {documentUploadVisible && (
        <FileUploadDialog
          hideModal={hideDocumentUploadModal}
          onOk={onDocumentUploadOk}
          loading={documentUploadLoading}
          showParseOnCreation
          isTableParser={knowledgeBase?.chunk_method === 'table'}
        />
      )}
    </Card>
  );
}
