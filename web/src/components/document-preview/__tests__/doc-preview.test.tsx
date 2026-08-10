import { render, screen, waitFor } from '@testing-library/react';
import * as React from 'react';

Object.assign(globalThis, { React });

jest.mock('@/components/ui/message', () => ({
  __esModule: true,
  default: { error: jest.fn() },
}));
jest.mock('@/components/ui/spin', () => ({ Spin: () => null }));
jest.mock('@/utils/request', () => ({
  __esModule: true,
  default: jest.fn().mockResolvedValue({
    data: new Blob([new Uint8Array([0x50, 0x4b])]),
  }),
}));
jest.mock('../hooks', () => ({
  isZipLikeBlob: jest.fn().mockResolvedValue(true),
  useDocumentResizeObserver: () => ({
    containerWidth: 1000,
    setContainerRef: jest.fn(),
  }),
  useDocxPreviewZoom: () => ({
    zoomScale: 100,
    minZoom: 25,
    maxZoom: 200,
    handleZoomIn: jest.fn(),
    handleZoomOut: jest.fn(),
  }),
}));
jest.mock('@extend-ai/react-docx', () => {
  const React = jest.requireActual('react');
  const editor = {
    importDocxFile: jest.fn().mockResolvedValue(undefined),
    status: 'Ready',
    totalPages: 2,
  };

  return {
    DocxEditorViewer: ({
      pageVirtualization,
    }: {
      pageVirtualization?: { enabled?: boolean };
    }) =>
      React.createElement('div', {
        'data-testid': 'docx-editor-viewer',
        'data-page-virtualization-enabled': String(pageVirtualization?.enabled),
      }),
    useDocxEditor: () => editor,
    useDocxPageLayout: () => ({ layout: { pageWidthPx: 816 } }),
  };
});

import { DocPreviewer } from '../doc-preview';

describe('DocPreviewer', () => {
  it('disables library page virtualization to avoid its layout update loop', async () => {
    render(
      React.createElement(DocPreviewer, {
        className: 'h-full',
        url: '/api/v1/documents/finance/preview',
      }),
    );

    await waitFor(() => {
      expect(screen.getByTestId('docx-editor-viewer')).toHaveAttribute(
        'data-page-virtualization-enabled',
        'false',
      );
    });
  });
});
