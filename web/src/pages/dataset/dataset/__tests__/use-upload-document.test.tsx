import { act, renderHook, waitFor } from '@testing-library/react';

import { useHandleUploadDocument } from '../use-upload-document';

const mockHideModal = jest.fn();
const mockUploadDocument = jest.fn();
const mockRunDocumentByIds = jest.fn();

jest.mock('@/hooks/common-hooks', () => ({
  useSetModalState: () => ({
    visible: true,
    hideModal: mockHideModal,
    showModal: jest.fn(),
  }),
}));

jest.mock('@/hooks/use-document-request', () => ({
  useUploadDocument: () => ({
    uploadDocument: mockUploadDocument,
    loading: false,
  }),
  useRunDocument: () => ({ runDocumentByIds: mockRunDocumentByIds }),
}));

const uploadValues = {
  fileList: [new File(['content'], '目录/A.docx')],
  parseOnCreation: true,
  tableColumnMode: 'auto' as const,
  tableColumnRoles: {},
};

beforeEach(() => {
  jest.clearAllMocks();
});

test('prevents duplicate submissions while a folder upload is in progress', async () => {
  let resolveUpload!: (value: unknown) => void;
  mockUploadDocument.mockReturnValue(
    new Promise((resolve) => {
      resolveUpload = resolve;
    }),
  );
  mockRunDocumentByIds.mockResolvedValue(0);

  const { result } = renderHook(() => useHandleUploadDocument('folder-1'));

  let first!: Promise<unknown>;
  let second!: Promise<unknown>;
  act(() => {
    first = result.current.onDocumentUploadOk(uploadValues);
    second = result.current.onDocumentUploadOk(uploadValues);
  });

  expect(mockUploadDocument).toHaveBeenCalledTimes(1);

  resolveUpload({ code: 0, data: [{ id: 'doc-1' }] });
  await act(async () => {
    await Promise.all([first, second]);
  });
});

test('waits for parsing and refreshes the current folder before closing', async () => {
  let resolveParsing!: (value: number) => void;
  const refresh = jest.fn().mockResolvedValue(undefined);
  mockUploadDocument.mockResolvedValue({ code: 0, data: [{ id: 'doc-1' }] });
  mockRunDocumentByIds.mockReturnValue(
    new Promise((resolve) => {
      resolveParsing = resolve;
    }),
  );

  const { result } = renderHook(() =>
    useHandleUploadDocument('folder-1', refresh),
  );

  let submission!: Promise<unknown>;
  act(() => {
    submission = result.current.onDocumentUploadOk(uploadValues);
  });

  await waitFor(() => expect(mockRunDocumentByIds).toHaveBeenCalledTimes(1));
  expect(mockHideModal).not.toHaveBeenCalled();
  expect(refresh).not.toHaveBeenCalled();

  resolveParsing(0);
  await act(async () => {
    await submission;
  });

  expect(refresh).toHaveBeenCalledTimes(1);
  expect(mockHideModal).toHaveBeenCalledTimes(1);
});

test('keeps the dialog open when automatic parsing submission fails', async () => {
  const refresh = jest.fn();
  mockUploadDocument.mockResolvedValue({ code: 0, data: [{ id: 'doc-1' }] });
  mockRunDocumentByIds.mockResolvedValue(500);

  const { result } = renderHook(() =>
    useHandleUploadDocument('folder-1', refresh),
  );

  let code: unknown;
  await act(async () => {
    code = await result.current.onDocumentUploadOk(uploadValues);
  });

  expect(code).toBe(500);
  expect(refresh).toHaveBeenCalledTimes(1);
  expect(mockHideModal).not.toHaveBeenCalled();
});

test('submits large folder parsing requests in bounded batches', async () => {
  const documents = Array.from({ length: 101 }, (_, index) => ({
    id: `doc-${index}`,
  }));
  mockUploadDocument.mockResolvedValue({ code: 0, data: documents });
  mockRunDocumentByIds.mockResolvedValue(0);

  const { result } = renderHook(() => useHandleUploadDocument('folder-1'));

  await act(async () => {
    await result.current.onDocumentUploadOk(uploadValues);
  });

  expect(mockRunDocumentByIds).toHaveBeenCalledTimes(3);
  expect(
    mockRunDocumentByIds.mock.calls.map(
      ([request]) => request.documentIds.length,
    ),
  ).toEqual([50, 50, 1]);
});
