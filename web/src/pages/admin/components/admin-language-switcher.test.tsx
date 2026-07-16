import { fireEvent, render, screen } from '@testing-library/react';

import AdminLanguageSwitcher from './admin-language-switcher';

const React = jest.requireActual<typeof import('react')>('react');
const mockChangeLanguageAsync = jest.fn().mockResolvedValue(undefined);

jest.mock('@/locales/config', () => ({
  supportedLanguages: [
    { code: 'en', displayName: 'English' },
    { code: 'zh', displayName: '中文' },
  ],
  changeLanguageAsync: (lng: string) => mockChangeLanguageAsync(lng),
}));

jest.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) => key,
    i18n: { resolvedLanguage: 'en', language: 'en' },
  }),
}));

jest.mock('@/components/ui/button', () => ({
  Button: ({
    children,
    variant,
    ...props
  }: React.ButtonHTMLAttributes<HTMLButtonElement> & { variant?: string }) => {
    void variant;
    return <button {...props}>{children}</button>;
  },
}));

jest.mock('@/components/ui/dropdown-menu', () => ({
  DropdownMenu: ({ children }: React.PropsWithChildren) => (
    <div>{children}</div>
  ),
  DropdownMenuTrigger: ({ children }: React.PropsWithChildren) => (
    <>{children}</>
  ),
  DropdownMenuContent: ({ children }: React.PropsWithChildren) => (
    <div>{children}</div>
  ),
  DropdownMenuItem: ({
    children,
    onClick,
  }: React.PropsWithChildren<{ onClick?: () => void }>) => (
    <button onClick={onClick}>{children}</button>
  ),
}));

describe('AdminLanguageSwitcher', () => {
  it('shows the current language and switches to the selected language', () => {
    render(React.createElement(AdminLanguageSwitcher));

    expect(
      screen.getByRole('button', { name: 'admin.language' }),
    ).toHaveTextContent('English');

    fireEvent.click(screen.getByRole('button', { name: '中文' }));

    expect(mockChangeLanguageAsync).toHaveBeenCalledWith('zh');
  });
});
