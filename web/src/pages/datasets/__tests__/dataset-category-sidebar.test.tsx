import { fireEvent, render, screen } from '@testing-library/react';

import { DatasetCategorySidebar } from '../dataset-category-sidebar';

const React = jest.requireActual<typeof import('react')>('react');
void React;

jest.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));

jest.mock('@/components/ui/button', () => ({
  Button: ({
    children,
    variant,
    size,
    ...props
  }: React.ButtonHTMLAttributes<HTMLButtonElement> & {
    variant?: string;
    size?: string;
  }) => {
    void variant;
    void size;
    return <button {...props}>{children}</button>;
  },
}));

jest.mock('@/components/more-button', () => ({
  MoreButton: (props: React.ButtonHTMLAttributes<HTMLButtonElement>) => (
    <button {...props} />
  ),
}));

jest.mock('@/components/ui/scroll-area', () => ({
  ScrollArea: ({ children }: React.PropsWithChildren) => <div>{children}</div>,
}));

jest.mock('@/components/ui/dropdown-menu', () => ({
  DropdownMenu: ({ children }: React.PropsWithChildren) => <>{children}</>,
  DropdownMenuContent: ({ children }: React.PropsWithChildren) => (
    <div>{children}</div>
  ),
  DropdownMenuItem: ({ children }: React.PropsWithChildren) => (
    <div>{children}</div>
  ),
  DropdownMenuSeparator: () => null,
  DropdownMenuTrigger: ({ children }: React.PropsWithChildren) => (
    <>{children}</>
  ),
}));

jest.mock('@/components/confirm-delete-dialog', () => ({
  ConfirmDeleteDialog: ({ children }: React.PropsWithChildren) => (
    <>{children}</>
  ),
}));

jest.mock('../dataset-category-dialog', () => ({
  DatasetCategoryDialog: () => null,
}));

describe('DatasetCategorySidebar', () => {
  it('shows counts and selects virtual or custom categories', () => {
    const onSelect = jest.fn();

    render(
      <DatasetCategorySidebar
        selected="all"
        summary={{
          total_count: 8,
          uncategorized_count: 3,
          categories: [
            {
              id: 'cat-1',
              tenant_id: 'tenant-1',
              name: '研发',
              count: 5,
              can_manage: true,
            },
          ],
        }}
        onSelect={onSelect}
        onCreate={jest.fn()}
        onRename={jest.fn()}
        onDelete={jest.fn()}
      />,
    );

    expect(
      screen.getByRole('button', { name: /knowledgeList\.all/ }),
    ).toHaveTextContent('8');
    expect(
      screen.getByRole('button', { name: /knowledgeList\.uncategorized/ }),
    ).toHaveTextContent('3');
    expect(screen.getByRole('button', { name: /研发/ })).toHaveTextContent('5');

    fireEvent.click(screen.getByRole('button', { name: /研发/ }));
    expect(onSelect).toHaveBeenCalledWith('cat-1');
  });
});
