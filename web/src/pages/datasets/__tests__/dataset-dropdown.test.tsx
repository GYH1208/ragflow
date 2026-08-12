import { fireEvent, render, screen } from '@testing-library/react';

import { DatasetDropdown } from '../dataset-dropdown';

const React = jest.requireActual<typeof import('react')>('react');
void React;

jest.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));

jest.mock('@/hooks/use-knowledge-request', () => ({
  useDeleteKnowledge: () => ({ deleteKnowledge: jest.fn() }),
}));

jest.mock('@/components/confirm-delete-dialog', () => ({
  ConfirmDeleteDialog: ({ children }: React.PropsWithChildren) => (
    <>{children}</>
  ),
  ConfirmDeleteDialogNode: () => null,
}));

jest.mock('@/components/ui/dropdown-menu', () => ({
  DropdownMenu: ({ children }: React.PropsWithChildren) => <>{children}</>,
  DropdownMenuTrigger: ({ children }: React.PropsWithChildren) => (
    <>{children}</>
  ),
  DropdownMenuContent: ({ children }: React.PropsWithChildren) => (
    <div>{children}</div>
  ),
  DropdownMenuSub: ({ children }: React.PropsWithChildren) => <>{children}</>,
  DropdownMenuSubTrigger: ({ children }: React.PropsWithChildren) => (
    <div>{children}</div>
  ),
  DropdownMenuSubContent: ({ children }: React.PropsWithChildren) => (
    <div>{children}</div>
  ),
  DropdownMenuSeparator: () => null,
  DropdownMenuItem: ({
    children,
    onSelect,
    onClick,
  }: React.PropsWithChildren<{
    onSelect?: (event: Event) => void;
    onClick?: React.MouseEventHandler<HTMLButtonElement>;
  }>) => (
    <button
      onClick={(event) => {
        onClick?.(event);
        onSelect?.(
          new Event('menu.itemSelect', {
            bubbles: true,
            cancelable: true,
          }),
        );
      }}
    >
      {children}
    </button>
  ),
}));

describe('DatasetDropdown', () => {
  it('moves a dataset without bubbling to the card navigation handler', () => {
    const onMove = jest.fn().mockResolvedValue(undefined);
    const onCardClick = jest.fn();

    render(
      <div onClick={onCardClick}>
        <DatasetDropdown
          dataset={
            {
              id: 'dataset-1',
              tenant_id: 'tenant-1',
              category_id: null,
            } as any
          }
          categories={[
            {
              id: 'cat-1',
              tenant_id: 'tenant-1',
              name: '人事',
              count: 1,
              can_manage: true,
            },
          ]}
          canMove
          onMove={onMove}
          showDatasetRenameModal={jest.fn()}
        >
          <button>menu</button>
        </DatasetDropdown>
      </div>,
    );

    fireEvent.click(screen.getByRole('button', { name: '人事' }));

    expect(onMove).toHaveBeenCalledWith('dataset-1', 'cat-1');
    expect(onCardClick).not.toHaveBeenCalled();
  });
});
