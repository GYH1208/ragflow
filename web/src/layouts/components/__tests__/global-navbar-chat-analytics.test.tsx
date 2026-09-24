import { render, screen } from '@testing-library/react';
import GlobalNavbar from '../global-navbar';

const React = jest.requireActual<typeof import('react')>('react');
(globalThis as typeof globalThis & { React: typeof React }).React = React;

jest.mock('@/utils/css-support', () => ({ supportsCssAnchor: false }));
jest.mock('@/routes', () => ({
  Routes: {
    Root: '/',
    Datasets: '/datasets',
    DatasetBase: '/dataset',
    Chats: '/chats',
    Chat: '/chat',
    ChatAnalytics: '/chat-analytics',
    Searches: '/searches',
    Search: '/search',
    Agents: '/agents',
    AgentTemplates: '/agent-templates',
    Memories: '/memories',
    Memory: '/memory',
    MemoryMessage: '/memory-message',
    Files: '/files',
  },
}));
jest.mock('react-router', () => ({
  Link: ({ children, to, ...props }: { children: any; to: string }) => (
    <a href={to} {...props}>
      {children}
    </a>
  ),
  useLocation: () => ({ pathname: '/chat-analytics' }),
}));
jest.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) =>
      key === 'header.chatAnalytics' ? 'Q&A Analytics' : key,
  }),
}));

it('shows the analytics destination as active on its route', () => {
  render(<GlobalNavbar />);

  expect(
    screen.getByRole('link', { name: 'Q&A Analytics' }),
  ).toHaveAttribute('href', '/chat-analytics');
  expect(
    screen.getByRole('link', { name: 'Q&A Analytics' }),
  ).toHaveAttribute('aria-current', 'page');
});
