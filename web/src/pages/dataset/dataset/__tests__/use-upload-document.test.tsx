import { startBackgroundKnowledgeUpload } from '@/utils/background-knowledge-upload';
import { act, renderHook } from '@testing-library/react';
import { useHandleUploadDocument } from '../use-upload-document';

const mockHideModal = jest.fn();
const mockShowModal = jest.fn();
const mockInvalidate = jest.fn();
let mockDataset = 'dataset-1';
jest.mock('@/hooks/common-hooks', () => ({
  useSetModalState: () => ({
    visible: true,
    hideModal: mockHideModal,
    showModal: mockShowModal,
  }),
}));
jest.mock('@tanstack/react-query', () => ({
  useQueryClient: () => ({ invalidateQueries: mockInvalidate }),
}));
jest.mock('react-router', () => ({ useParams: () => ({ id: mockDataset }) }));
jest.mock('@/hooks/use-document-request', () => ({
  DocumentApiAction: { FetchDocumentList: 'fetchDocumentList' },
}));
jest.mock('@/hooks/use-knowledge-file-request', () => ({
  KnowledgeFileApiAction: {
    List: 'listKnowledgeEntries',
    Ancestors: 'listKnowledgeFolderAncestors',
  },
}));
jest.mock('@/utils/background-knowledge-upload', () => ({
  startBackgroundKnowledgeUpload: jest.fn(() => new Promise(() => {})),
}));
const mockStart = startBackgroundKnowledgeUpload as jest.Mock;
const uploadValues = {
  fileList: Array.from({ length: 100 }, (_, i) => new File(['x'], `${i}.txt`)),
  parseOnCreation: true,
  tableColumnMode: 'manual' as const,
  tableColumnRoles: { name: 'indexing' as const },
};
beforeEach(() => {
  jest.clearAllMocks();
  mockDataset = 'dataset-1';
});

test('accepts 100 files and closes immediately while the background task is still pending', async () => {
  const { result } = renderHook(() => useHandleUploadDocument('folder-1'));
  await act(async () => {
    expect(await result.current.onDocumentUploadOk(uploadValues)).toBe(0);
  });
  expect(mockHideModal).toHaveBeenCalledTimes(1);
  expect(result.current.documentUploadLoading).toBe(false);
  expect(mockStart).toHaveBeenCalledTimes(1);
  expect(mockStart.mock.calls[0][0].files).toHaveLength(100);
  mockStart.mock.calls[0][0].files.forEach((file: File, index: number) =>
    expect(file).toBe(uploadValues.fileList[index]),
  );
  expect(mockStart.mock.calls[0][0]).toMatchObject({
    datasetId: 'dataset-1',
    parentFolderId: 'folder-1',

    parseOnCreation: true,
    parserConfig: {
      table_column_mode: 'manual',
      table_column_roles: { name: 'indexing' },
    },
  });
});

test('retains the original destination when navigating or unmounting and refreshes results on completion', async () => {
  const refresh = jest.fn();
  const { result, rerender, unmount } = renderHook(
    ({ folder }) => useHandleUploadDocument(folder, refresh),
    { initialProps: { folder: 'folder-1' } },
  );
  await act(async () => {
    await result.current.onDocumentUploadOk(uploadValues);
  });
  const job = mockStart.mock.calls[0][0];
  mockDataset = 'dataset-2';
  rerender({ folder: 'folder-2' });
  unmount();
  expect(job.datasetId).toBe('dataset-1');
  expect(job.parentFolderId).toBe('folder-1');
  await job.onSettled();
  expect(mockInvalidate).toHaveBeenCalledTimes(3);
  expect(refresh).toHaveBeenCalledTimes(1);
});

test('ignores a double submit but allows a new selection after reopening', async () => {
  const { result } = renderHook(() => useHandleUploadDocument());
  await act(async () => {
    await Promise.all([
      result.current.onDocumentUploadOk(uploadValues),
      result.current.onDocumentUploadOk(uploadValues),
    ]);
  });
  expect(mockStart).toHaveBeenCalledTimes(1);
  act(() => result.current.showDocumentUploadModal());
  await act(async () => {
    await result.current.onDocumentUploadOk({
      ...uploadValues,
      fileList: [new File(['new'], 'new.txt')],
    });
  });
  expect(mockStart).toHaveBeenCalledTimes(2);
});
