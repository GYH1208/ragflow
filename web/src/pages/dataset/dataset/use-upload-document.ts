import { UploadFormSchemaType } from '@/components/file-upload-dialog';
import { useSetModalState } from '@/hooks/common-hooks';
import {
  useRunDocument,
  useUploadDocument,
} from '@/hooks/use-document-request';
import { getUnSupportedFilesCount } from '@/utils/document-util';
import { useCallback, useRef, useState } from 'react';

const ParseSubmissionBatchSize = 50;

export const useHandleUploadDocument = (
  parentFolderId?: string,
  onUploadComplete?: () => Promise<unknown> | unknown,
) => {
  const {
    visible: documentUploadVisible,
    hideModal: hideDocumentUploadModal,
    showModal: showDocumentUploadModal,
  } = useSetModalState();
  const { uploadDocument, loading } = useUploadDocument();
  const { runDocumentByIds } = useRunDocument();
  const submittingRef = useRef(false);
  const [submitting, setSubmitting] = useState(false);

  const onDocumentUploadOk = useCallback(
    async ({
      fileList,
      parseOnCreation,
      tableColumnMode,
      tableColumnRoles,
    }: UploadFormSchemaType) => {
      if (submittingRef.current || fileList.length === 0) return;

      submittingRef.current = true;
      setSubmitting(true);
      try {
        // Build parser_config if column roles are configured
        let parserConfig: Record<string, any> | undefined;
        if (
          tableColumnMode === 'manual' &&
          tableColumnRoles &&
          Object.keys(tableColumnRoles).length > 0
        ) {
          parserConfig = {
            table_column_mode: 'manual',
            table_column_roles: tableColumnRoles,
          };
        }

        const ret = await uploadDocument(
          fileList as File[],
          parserConfig,
          parentFolderId,
        );

        // Check for success (code === 0) or partial success (code === 500 with some files)
        const isSuccess = ret?.code === 0;
        const isPartialSuccess = ret?.code === 500 && ret?.message;

        if (!isSuccess && !isPartialSuccess) {
          return;
        }

        if (isSuccess && parseOnCreation) {
          const documentIds = ret.data.map((x: any) => x.id);
          let parseCode = 0;
          for (
            let offset = 0;
            offset < documentIds.length;
            offset += ParseSubmissionBatchSize
          ) {
            parseCode = await runDocumentByIds({
              documentIds: documentIds.slice(
                offset,
                offset + ParseSubmissionBatchSize,
              ),
              run: 1,
            });
            if (parseCode !== 0) break;
          }
          await onUploadComplete?.();
          if (parseCode !== 0) return parseCode;
        }

        if (isSuccess) {
          if (!parseOnCreation) await onUploadComplete?.();
          hideDocumentUploadModal();
          return 0;
        }

        // For partial success (code 500), check if any files were uploaded
        const count = getUnSupportedFilesCount(ret?.message);
        if (count !== fileList.length) {
          await onUploadComplete?.();
          hideDocumentUploadModal();
          return 0;
        }

        return ret?.code;
      } finally {
        submittingRef.current = false;
        setSubmitting(false);
      }
    },
    [
      uploadDocument,
      runDocumentByIds,
      onUploadComplete,
      hideDocumentUploadModal,
      parentFolderId,
    ],
  );

  return {
    documentUploadLoading: loading || submitting,
    onDocumentUploadOk,
    documentUploadVisible,
    hideDocumentUploadModal,
    showDocumentUploadModal,
  };
};
