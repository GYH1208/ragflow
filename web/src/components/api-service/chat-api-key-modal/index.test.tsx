import { render, screen } from '@testing-library/react';

import ChatApiKeyModal from './index';

const React = jest.requireActual<typeof import('react')>('react');
const mockCreateToken = jest.fn();

jest.mock('../hooks', () => ({
  useOperateApiKey: () => ({
    createToken: mockCreateToken,
    removeToken: jest.fn(),
    tokenList: [
      {
        beta: 'beta-token',
        create_date: '2026-09-24 00:00:00',
        create_time: 1,
        tenant_id: 'tenant-1',
        token: 'ragflow-existing-token',
        update_date: null,
        update_time: null,
      },
    ],
    listLoading: false,
    creatingLoading: false,
  }),
}));

jest.mock('@/hooks/common-hooks', () => ({
  useTranslate: () => ({ t: (key: string) => key }),
}));

jest.mock('@/components/copy-to-clipboard', () => () => null);

jest.mock('@/components/ui/dialog', () => ({
  Dialog: ({ children }: React.PropsWithChildren) => <div>{children}</div>,
  DialogContent: ({ children }: React.PropsWithChildren) => (
    <div>{children}</div>
  ),
  DialogHeader: ({ children }: React.PropsWithChildren) => (
    <div>{children}</div>
  ),
  DialogTitle: ({ children }: React.PropsWithChildren) => <h2>{children}</h2>,
}));

jest.mock('@/components/ui/table', () => ({
  Table: ({ children }: React.PropsWithChildren) => <table>{children}</table>,
  TableBody: ({ children }: React.PropsWithChildren) => (
    <tbody>{children}</tbody>
  ),
  TableCell: ({ children }: React.PropsWithChildren) => <td>{children}</td>,
  TableHead: ({ children }: React.PropsWithChildren) => <th>{children}</th>,
  TableHeader: ({ children }: React.PropsWithChildren) => (
    <thead>{children}</thead>
  ),
  TableRow: ({ children }: React.PropsWithChildren) => <tr>{children}</tr>,
}));

it('keeps create new key enabled when a token already exists', () => {
  render(
    <ChatApiKeyModal hideModal={jest.fn()} idKey="dialog_id" />,
  );

  expect(
    screen.getByRole('button', { name: 'createNewKey' }),
  ).toBeEnabled();
});
