import kbService, { uploadDocument } from '@/services/knowledge-service';
import { toast } from 'sonner';
import { startBackgroundKnowledgeUpload } from '../background-knowledge-upload';

jest.mock('sonner', () => ({
  toast: { message: jest.fn(), success: jest.fn(), error: jest.fn() },
}));
jest.mock('@/locales/config', () => ({
  __esModule: true,
  default: { t: (key: string) => key },
}));
jest.mock('@/services/knowledge-service', () => ({
  __esModule: true,
  uploadDocument: jest.fn(),
  default: { documentIngest: jest.fn() },
}));
const mockUpload = uploadDocument as jest.Mock;
const mockIngest = kbService.documentIngest as jest.Mock;
const files = (count: number) =>
  Array.from({ length: count }, (_, i) => {
    const file = new File(['x'], `${i}.txt`);
    Object.defineProperty(file, 'webkitRelativePath', {
      value: `folder/${i}.txt`,
    });
    return file;
  });
const success = (_id: string, body: FormData) =>
  Promise.resolve({
    code: 0,
    data: body.getAll('file').map((file) => ({ id: (file as File).name })),
  });
beforeEach(() => {
  jest.clearAllMocks();
  mockUpload.mockImplementation(success);
  mockIngest.mockResolvedValue({ data: { code: 0 } });
});
test('accepts 100 files as one background job and preserves paths, folder, parser config and parsing', async () => {
  const onSettled = jest.fn();
  const input = files(100);
  await startBackgroundKnowledgeUpload({
    datasetId: 'original',
    parentFolderId: 'parent',
    files: input,
    parserConfig: { table_column_mode: 'manual' },
    parseOnCreation: true,
    onSettled,
  });
  expect(mockUpload).toHaveBeenCalledTimes(13);
  expect(mockUpload.mock.calls.every(([id]) => id === 'original')).toBe(true);
  const bodies = mockUpload.mock.calls.map(([, body]) => body as FormData);
  expect(bodies.flatMap((body) => body.getAll('relative_path'))).toEqual(
    input.map((file) => file.webkitRelativePath),
  );
  expect(
    bodies.every(
      (body) =>
        body.get('parent_id') === 'parent' &&
        body.get('parser_config') === '{"table_column_mode":"manual"}',
    ),
  ).toBe(true);
  expect(
    mockIngest.mock.calls.flatMap(([request]) => request.doc_ids),
  ).toHaveLength(100);
  expect(toast.success).toHaveBeenCalledTimes(1);
  expect(onSettled).toHaveBeenCalledTimes(1);
});
test('protects pending files on unload and serializes jobs without blocking the caller', async () => {
  let finish!: (value: unknown) => void;
  mockUpload.mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        finish = resolve;
      }),
  );
  const first = startBackgroundKnowledgeUpload({
    datasetId: 'one',
    files: files(1),
  });
  const second = startBackgroundKnowledgeUpload({
    datasetId: 'two',
    files: files(1),
  });
  await Promise.resolve();
  expect(mockUpload).toHaveBeenCalledTimes(1);
  const during = new Event('beforeunload', { cancelable: true });
  window.dispatchEvent(during);
  expect(during.defaultPrevented).toBe(true);
  finish({ code: 0, data: [{ id: 'first' }] });
  await Promise.all([first, second]);
  expect(mockUpload.mock.calls.map(([id]) => id)).toEqual(['one', 'two']);
  const after = new Event('beforeunload', { cancelable: true });
  window.dispatchEvent(after);
  expect(after.defaultPrevented).toBe(false);
});
test('reports partial failures, refreshes results, does not retry, and allows the next job to run', async () => {
  mockUpload.mockResolvedValueOnce({ code: 500 });
  const onSettled = jest.fn();
  await startBackgroundKnowledgeUpload({
    datasetId: 'bad',
    files: files(24),
    onSettled,
  });
  expect(mockUpload).toHaveBeenCalledTimes(1);
  expect(toast.error).toHaveBeenCalledTimes(1);
  expect(toast.success).not.toHaveBeenCalled();
  expect(onSettled).toHaveBeenCalledTimes(1);
  await startBackgroundKnowledgeUpload({ datasetId: 'good', files: files(1) });
  expect(toast.success).toHaveBeenCalledTimes(1);
});
test('reports parsing submission failure without retrying uploaded files', async () => {
  mockIngest.mockResolvedValue({ data: { code: 500 } });
  await startBackgroundKnowledgeUpload({
    datasetId: 'one',
    files: files(1),
    parseOnCreation: true,
  });
  expect(mockUpload).toHaveBeenCalledTimes(1);
  expect(toast.error).toHaveBeenCalledTimes(1);
  expect(toast.success).not.toHaveBeenCalled();
});
