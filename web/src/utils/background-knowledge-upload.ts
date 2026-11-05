import i18n from '@/locales/config';
import kbService, { uploadDocument } from '@/services/knowledge-service';
import { toast } from 'sonner';
import { uploadKnowledgeFilesInBatches } from './batch-knowledge-upload';
import { buildKnowledgeUploadFormData } from './knowledge-file-upload';

type BackgroundUpload = {
  datasetId: string;
  parentFolderId?: string;
  files: File[];
  parserConfig?: Record<string, unknown>;
  parseOnCreation?: boolean;
  onSettled?: () => Promise<unknown> | unknown;
};

// Owned by the page, not a dialog or route. Jobs survive React unmounts and
// navigation; only one job runs at a time to bound load across submissions.
let queue = Promise.resolve();
let pending = 0;
let sequence = 0;
const protectPendingFiles = (event: BeforeUnloadEvent) => {
  event.preventDefault();
  event.returnValue = '';
};

export function startBackgroundKnowledgeUpload(options: BackgroundUpload) {
  const id = `knowledge-upload-${++sequence}`;
  const total = options.files.length;
  const title = i18n.t('fileManager.backgroundUploadTitle', { count: total });
  const progress = (description: string) =>
    toast.message(title, {
      id,
      description,
      duration: Infinity,
    });
  progress(i18n.t('fileManager.backgroundUploadQueued'));
  if (pending++ === 0)
    window.addEventListener('beforeunload', protectPendingFiles);

  const run = async () => {
    let uploaded = 0;
    let uploadFailed = false;
    try {
      progress(
        i18n.t('fileManager.backgroundUploadProgress', { completed: 0, total }),
      );
      const result = await uploadKnowledgeFilesInBatches(
        options.files,
        (files) =>
          uploadDocument(
            options.datasetId,
            buildKnowledgeUploadFormData(
              files,
              options.parserConfig,
              options.parentFolderId,
            ),
          ),
        (completed) => {
          uploaded = completed;
          progress(
            i18n.t('fileManager.backgroundUploadProgress', {
              completed,
              total,
            }),
          );
        },
      );
      uploadFailed = result.failed;
      // Even if a later upload failed, honor automatic parsing for confirmed
      // documents. Never retry ambiguous requests that may have been saved.
      if (options.parseOnCreation && result.documents.length) {
        const ids = result.documents.map((doc) => doc.id);
        for (let offset = 0; offset < ids.length; offset += 50) {
          progress(
            i18n.t('fileManager.backgroundParseProgress', {
              completed: offset,
              total: ids.length,
            }),
          );
          const response = await kbService.documentIngest({
            doc_ids: ids.slice(offset, offset + 50),
            run: 1,
          });
          if (response?.data?.code !== 0)
            throw new Error('Parsing submission failed');
        }
      }
      if (uploadFailed) throw new Error('Upload incomplete');
      toast.success(title, {
        id,
        duration: 8000,
        description: i18n.t(
          options.parseOnCreation
            ? 'fileManager.backgroundUploadParsing'
            : 'fileManager.backgroundUploadComplete',
        ),
      });
    } catch {
      toast.error(title, {
        id,
        duration: Infinity,
        description: i18n.t(
          uploaded === total && !uploadFailed
            ? 'fileManager.backgroundParseFailed'
            : 'fileManager.backgroundUploadFailed',
          { completed: uploaded, total },
        ),
      });
    } finally {
      try {
        await options.onSettled?.();
      } catch {
        /* Saved files must never be uploaded again because a list refresh failed. */
      }
    }
  };
  const done = queue.then(run).finally(() => {
    if (--pending === 0)
      window.removeEventListener('beforeunload', protectPendingFiles);
  });
  queue = done.catch(() => undefined);
  return done;
}
