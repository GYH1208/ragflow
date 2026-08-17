import { DocumentApiAction } from '@/hooks/use-document-request';
import {
  KnowledgeDeleteResult,
  KnowledgeEntriesResult,
  KnowledgeFolderAncestor,
} from '@/interfaces/database/knowledge-file';
import {
  createKnowledgeFolder,
  deleteKnowledgeEntries,
  listKnowledgeEntries,
  listKnowledgeFolderAncestors,
  moveKnowledgeEntries,
  previewDeleteKnowledgeEntries,
  renameKnowledgeEntry,
} from '@/services/knowledge-service';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

export const enum KnowledgeFileApiAction {
  List = 'listKnowledgeEntries',
  Ancestors = 'listKnowledgeFolderAncestors',
  CreateFolder = 'createKnowledgeFolder',
  Rename = 'renameKnowledgeEntry',
  Move = 'moveKnowledgeEntries',
  DeletePreview = 'previewDeleteKnowledgeEntries',
  Delete = 'deleteKnowledgeEntries',
}

export interface KnowledgeEntriesQuery {
  datasetId: string;
  folderId?: string;
  page?: number;
  pageSize?: number;
  orderby?: string;
  desc?: boolean;
  keywords?: string;
  runStatus?: string[];
  types?: string[];
  suffix?: string[];
}

export function useKnowledgeEntries(params: KnowledgeEntriesQuery) {
  const { datasetId, folderId, page = 1, pageSize = 20 } = params;
  return useQuery<KnowledgeEntriesResult>({
    queryKey: [KnowledgeFileApiAction.List, params],
    enabled: Boolean(datasetId),
    queryFn: async () => {
      const response = await listKnowledgeEntries(datasetId, {
        parent_id: folderId,
        page,
        page_size: pageSize,
        orderby: params.orderby || 'create_time',
        desc: params.desc ?? true,
        keywords: params.keywords || '',
        run_status: params.runStatus,
        types: params.types,
        suffix: params.suffix,
      });
      return response.data.data;
    },
  });
}

export function useKnowledgeFolderAncestors(
  datasetId: string,
  folderId?: string,
) {
  return useQuery<KnowledgeFolderAncestor[]>({
    queryKey: [KnowledgeFileApiAction.Ancestors, datasetId, folderId],
    enabled: Boolean(datasetId && folderId),
    queryFn: async () => {
      const response = await listKnowledgeFolderAncestors(
        datasetId,
        folderId as string,
      );
      return response.data.data;
    },
  });
}

function useInvalidateKnowledgeEntries() {
  const queryClient = useQueryClient();
  return async () => {
    await Promise.all([
      queryClient.invalidateQueries({
        queryKey: [KnowledgeFileApiAction.List],
      }),
      queryClient.invalidateQueries({
        queryKey: [KnowledgeFileApiAction.Ancestors],
      }),
      queryClient.invalidateQueries({
        queryKey: [DocumentApiAction.FetchDocumentList],
      }),
    ]);
  };
}

export function useCreateKnowledgeFolder(datasetId: string) {
  const invalidate = useInvalidateKnowledgeEntries();
  return useMutation({
    mutationKey: [KnowledgeFileApiAction.CreateFolder, datasetId],
    mutationFn: (data: { parent_id: string; name: string }) =>
      createKnowledgeFolder(datasetId, data),
    onSuccess: invalidate,
  });
}

export function useRenameKnowledgeEntry(datasetId: string) {
  const invalidate = useInvalidateKnowledgeEntries();
  return useMutation({
    mutationKey: [KnowledgeFileApiAction.Rename, datasetId],
    mutationFn: ({ entryId, name }: { entryId: string; name: string }) =>
      renameKnowledgeEntry(datasetId, entryId, name),
    onSuccess: invalidate,
  });
}

export function useMoveKnowledgeEntries(datasetId: string) {
  const invalidate = useInvalidateKnowledgeEntries();
  return useMutation({
    mutationKey: [KnowledgeFileApiAction.Move, datasetId],
    mutationFn: (data: { ids: string[]; destination_id: string }) =>
      moveKnowledgeEntries(datasetId, data),
    onSuccess: invalidate,
  });
}

export function usePreviewDeleteKnowledgeEntries(datasetId: string) {
  return useMutation<number, Error, string[]>({
    mutationKey: [KnowledgeFileApiAction.DeletePreview, datasetId],
    mutationFn: async (ids) => {
      const response = await previewDeleteKnowledgeEntries(datasetId, ids);
      return response.data.data.document_count;
    },
  });
}

export function useDeleteKnowledgeEntries(datasetId: string) {
  const invalidate = useInvalidateKnowledgeEntries();
  return useMutation<KnowledgeDeleteResult, Error, string[]>({
    mutationKey: [KnowledgeFileApiAction.Delete, datasetId],
    mutationFn: async (ids) => {
      const response = await deleteKnowledgeEntries(datasetId, ids);
      return response.data.data;
    },
    onSuccess: invalidate,
  });
}
