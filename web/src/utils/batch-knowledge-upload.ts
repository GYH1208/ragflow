const BatchFileLimit = 8;
const BatchByteLimit = 32 * 1024 * 1024;

export function partitionUploadFiles(files: File[]): File[][] {
  const batches: File[][] = [];
  let batch: File[] = [];
  let bytes = 0;
  for (const file of files) {
    if (
      batch.length &&
      (batch.length >= BatchFileLimit || bytes + file.size > BatchByteLimit)
    ) {
      batches.push(batch);
      batch = [];
      bytes = 0;
    }
    batch.push(file);
    bytes += file.size;
  }
  if (batch.length) batches.push(batch);
  return batches;
}

export async function uploadKnowledgeFilesInBatches<T extends { id: string }>(
  files: File[],
  upload: (files: File[]) => Promise<{ code?: number; data?: T[] } | undefined>,
  onProgress: (completed: number) => void,
) {
  const batches = partitionUploadFiles(files);
  // The server allocates duplicate names before saving. Even different input
  // names can collide after renaming, so preserve sequential writes.
  let next = 0;
  let failed = false;
  let completed = 0;
  const documents: T[] = [];
  async function worker() {
    while (!failed && next < batches.length) {
      const batch = batches[next++];
      try {
        const response = await upload(batch);
        if (
          response?.code !== 0 ||
          !Array.isArray(response.data) ||
          response.data.length !== batch.length ||
          response.data.some((doc) => !doc || typeof doc.id !== 'string')
        ) {
          failed = true;
          return;
        }
        documents.push(...response.data);
        completed += batch.length;
        onProgress(completed);
      } catch {
        // A lost response can still mean files were persisted. Never retry an
        // upload automatically; drain other requests before returning failure.
        failed = true;
      }
    }
  }
  await worker();
  return { documents, completed, failed };
}
