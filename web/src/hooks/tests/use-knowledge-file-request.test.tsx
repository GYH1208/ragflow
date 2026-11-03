import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, renderHook, waitFor } from '@testing-library/react';
import * as React from 'react';
import { useKnowledgeEntries } from '../use-knowledge-file-request';

Object.assign(globalThis, { React });

const mockListKnowledgeEntries = jest.fn();

jest.mock('@/services/knowledge-service', () => ({
  listKnowledgeEntries: (...args: unknown[]) =>
    mockListKnowledgeEntries(...args),
}));

jest.mock('@/hooks/use-document-request', () => ({
  DocumentApiAction: { FetchDocumentList: 'fetchDocumentList' },
}));

afterEach(() => {
  jest.useRealTimers();
  mockListKnowledgeEntries.mockReset();
});

test.each(['1', '5', 'RUNNING', 'SCHEDULE'])(
  'refreshes active parsing status %s until it completes, then stops polling',
  async (run) => {
    jest.useFakeTimers();
    let currentRun = run;
    mockListKnowledgeEntries.mockImplementation(async () => ({
      data: {
        data: {
          entries: [{ entry_type: 'document', id: 'doc-1', run: currentRun }],
          parent_folder: { id: 'root', name: 'Knowledge' },
          total: 1,
        },
      },
    }));
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const { result, unmount } = renderHook(
      () => useKnowledgeEntries({ datasetId: 'kb-1' }),
      {
        wrapper: ({ children }: { children: import('react').ReactNode }) => (
          <QueryClientProvider client={client}>{children}</QueryClientProvider>
        ),
      },
    );

    try {
      await waitFor(() =>
        expect(result.current.data?.entries[0]).toMatchObject({ run }),
      );
      currentRun = '3';
      await act(async () => {
        await jest.advanceTimersByTimeAsync(5000);
      });
      await waitFor(() =>
        expect(result.current.data?.entries[0]).toMatchObject({ run: '3' }),
      );
      const completedCallCount = mockListKnowledgeEntries.mock.calls.length;
      await act(async () => {
        await jest.advanceTimersByTimeAsync(10000);
      });
      expect(mockListKnowledgeEntries).toHaveBeenCalledTimes(
        completedCallCount,
      );
    } finally {
      unmount();
      client.clear();
    }
  },
);
