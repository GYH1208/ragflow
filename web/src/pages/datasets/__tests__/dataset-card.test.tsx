import { render, screen } from '@testing-library/react';

import { DatasetCard } from '../dataset-card';

const React = jest.requireActual<typeof import('react')>('react');
(globalThis as any).React = React;

jest.mock('@/hooks/logic-hooks/navigate-hooks', () => ({
  useNavigatePage: () => ({ navigateToDataset: jest.fn() }),
}));

jest.mock('@/components/home-card', () => ({
  HomeCard: ({ moreDropdown }: { moreDropdown: React.ReactNode }) => (
    <div>{moreDropdown}</div>
  ),
}));

jest.mock('@/components/more-button', () => ({
  MoreButton: () => <button>menu</button>,
}));

jest.mock('@/components/shared-badge', () => ({
  SharedBadge: () => null,
}));

jest.mock('../dataset-dropdown', () => ({
  DatasetDropdown: ({
    children,
    categories,
    canMove,
    onMove,
  }: React.PropsWithChildren<{
    categories?: unknown[];
    canMove?: boolean;
    onMove?: unknown;
  }>) => (
    <div
      data-testid="dataset-dropdown"
      data-category-count={categories?.length ?? -1}
      data-can-move={String(canMove)}
      data-has-move-handler={String(typeof onMove === 'function')}
    >
      {children}
    </div>
  ),
}));

describe('DatasetCard', () => {
  it('keeps homepage cards usable without category controls', () => {
    render(
      <DatasetCard
        {...({
          dataset: {
            id: 'dataset-1',
            document_count: 0,
          },
          showDatasetRenameModal: jest.fn(),
        } as any)}
      />,
    );

    expect(screen.getByTestId('dataset-dropdown')).toHaveAttribute(
      'data-category-count',
      '0',
    );
    expect(screen.getByTestId('dataset-dropdown')).toHaveAttribute(
      'data-can-move',
      'false',
    );
    expect(screen.getByTestId('dataset-dropdown')).toHaveAttribute(
      'data-has-move-handler',
      'true',
    );
  });
});
