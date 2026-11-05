import { UploadFormSchemaType } from '@/components/file-upload-dialog';
import { useSetModalState } from '@/hooks/common-hooks';
import { DocumentApiAction } from '@/hooks/use-document-request';
import { KnowledgeFileApiAction } from '@/hooks/use-knowledge-file-request';
import { startBackgroundKnowledgeUpload } from '@/utils/background-knowledge-upload';
import { useQueryClient } from '@tanstack/react-query';
import { useCallback, useRef } from 'react';
import { useParams } from 'react-router';

export const useHandleUploadDocument = (
  parentFolderId?: string,
  onUploadComplete?: () => Promise<unknown> | unknown,
) => {
  const {
    visible: documentUploadVisible,
    hideModal,
    showModal,
  } = useSetModalState();
  const { id: datasetId } = useParams();
  const queryClient = useQueryClient();
  const submittedRef = useRef(false);
  const showDocumentUploadModal = useCallback(() => {
    submittedRef.current = false;
    showModal();
  }, [showModal]);

  const onDocumentUploadOk = useCallback(
    async ({
      fileList,
      parseOnCreation,
      tableColumnMode,
      tableColumnRoles,
    }: UploadFormSchemaType) => {
      if (submittedRef.current || fileList.length === 0 || !datasetId) return;
      submittedRef.current = true;
      const parserConfig =
        tableColumnMode === 'manual' &&
        tableColumnRoles &&
        Object.keys(tableColumnRoles).length
          ? {
              table_column_mode: 'manual',
              table_column_roles: { ...tableColumnRoles },
            }
          : undefined;
      // Capture the destination now. React Query mutation observers can change
      // their options when navigating to another dataset during an upload.
      void startBackgroundKnowledgeUpload({
        datasetId,
        parentFolderId,
        files: [...fileList],
        parserConfig,
        parseOnCreation,
        onSettled: async () => {
          await Promise.all([
            queryClient.invalidateQueries({
              queryKey: [DocumentApiAction.FetchDocumentList],
            }),
            queryClient.invalidateQueries({
              queryKey: [KnowledgeFileApiAction.List],
            }),
            queryClient.invalidateQueries({
              queryKey: [KnowledgeFileApiAction.Ancestors],
            }),
          ]);
          await onUploadComplete?.();
        },
      });
      hideModal();
      return 0;
    },
    [datasetId, parentFolderId, queryClient, onUploadComplete, hideModal],
  );

  return {
    documentUploadLoading: false,
    onDocumentUploadOk,
    documentUploadVisible,
    hideDocumentUploadModal: hideModal,
    showDocumentUploadModal,
  };
};
