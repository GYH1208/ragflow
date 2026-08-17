import { fireEvent, render, screen } from '@testing-library/react';
import * as React from 'react';

Object.assign(globalThis, { React });

import { KnowledgeFileBrowser } from '../knowledge-file-browser';

const mockQueryParams: Array<Record<string, unknown>> = [];
const mockEntries = [
  {
    entry_type: 'folder' as const,
    id: 'folder-1',
    file_id: 'folder-1',
    parent_id: 'root',
    name: '制度文件',
    type: 'folder' as const,
    relative_path: '制度文件',
    has_child_folder: false,
    size: 0,
    create_time: 1,
    update_time: 1,
  },
  {
    entry_type: 'document' as const,
    id: 'doc-1',
    file_id: 'file-1',
    parent_id: 'root',
    name: 'A.docx',
    dataset_id: 'kb-1',
    type: 'doc',
    status: '1',
    run: '3',
    chunk_count: 2,
    create_time: 1,
    relative_path: 'A.docx',
  },
];

jest.mock('@/hooks/use-knowledge-file-request', () => ({
  useKnowledgeEntries: (params: Record<string, unknown>) => {
    mockQueryParams.push(params);
    return {
      data: {
        entries: mockEntries,
        parent_folder: { id: 'root', name: '知识库' },
        total: 2,
      },
      refetch: jest.fn(),
    };
  },
  useKnowledgeFolderAncestors: () => ({ data: [] }),
  useCreateKnowledgeFolder: () => ({ mutateAsync: jest.fn(), isPending: false }),
  useRenameKnowledgeEntry: () => ({ mutateAsync: jest.fn(), isPending: false }),
  useMoveKnowledgeEntries: () => ({ mutateAsync: jest.fn(), isPending: false }),
  usePreviewDeleteKnowledgeEntries: () => ({ mutateAsync: jest.fn() }),
  useDeleteKnowledgeEntries: () => ({ mutateAsync: jest.fn() }),
}));

jest.mock('@/hooks/use-document-request', () => ({
  useRunDocument: () => ({ runDocumentByIds: jest.fn() }),
  useSetDocumentStatus: () => ({ setDocumentStatus: jest.fn() }),
}));

jest.mock('@/hooks/logic-hooks/navigate-hooks', () => ({
  useNavigatePage: () => ({ navigateToChunkParsedResult: () => jest.fn() }),
}));

jest.mock('../../contexts/knowledge-base-context', () => ({
  useKnowledgeBaseContext: () => ({ knowledgeBase: {} }),
}));

jest.mock('../use-upload-document', () => ({
  useHandleUploadDocument: () => ({
    documentUploadVisible: false,
    showDocumentUploadModal: jest.fn(),
    hideDocumentUploadModal: jest.fn(),
    onDocumentUploadOk: jest.fn(),
    documentUploadLoading: false,
  }),
}));

jest.mock('react-router', () => ({
  useParams: () => ({ id: 'kb-1' }),
}));

jest.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));

jest.mock('@/components/bulk-operate-bar', () => ({
  BulkOperateBar: ({ list }: { list: Array<{ label: string }> }) => (
    <div data-testid="bulk-actions">
      {list.map((item) => (
        <span key={item.label}>{item.label}</span>
      ))}
    </div>
  ),
}));

jest.mock('@/components/empty/constant', () => ({
  EmptyType: { Data: 'data' },
}));

jest.mock('@/components/empty/empty', () => ({
  __esModule: true,
  default: () => <div>No data</div>,
}));

jest.mock('@/components/icon-font', () => ({
  FileIcon: () => <span>file</span>,
}));

jest.mock('@/components/ui/ragflow-pagination', () => ({
  RAGFlowPagination: () => <div>pagination</div>,
}));

jest.mock('../knowledge-entry-action-cell', () => ({
  KnowledgeEntryActionCell: () => <div>actions</div>,
}));

test('opens folders and only exposes move/delete when a folder is selected', () => {
  render(React.createElement(KnowledgeFileBrowser));

  expect(screen.getByText('制度文件')).toBeInTheDocument();
  expect(screen.getByText('A.docx')).toBeInTheDocument();
  expect(screen.getAllByRole('switch')).toHaveLength(1);

  fireEvent.click(screen.getByText('制度文件'));
  expect(mockQueryParams.at(-1)).toEqual(
    expect.objectContaining({ datasetId: 'kb-1', folderId: 'folder-1' }),
  );

  fireEvent.click(screen.getAllByLabelText('Select row')[0]);
  const actions = screen.getByTestId('bulk-actions');
  expect(actions).toHaveTextContent('common.move');
  expect(actions).toHaveTextContent('common.delete');
  expect(actions).not.toHaveTextContent('knowledgeDetails.run');
});
