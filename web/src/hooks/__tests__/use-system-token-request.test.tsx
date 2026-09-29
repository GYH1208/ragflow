import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, renderHook } from '@testing-library/react';
import { createElement } from 'react';

import request from '@/utils/request';
import {
  useRemoveSystemToken,
  useUpdateSystemToken,
} from '../use-user-setting-request';

jest.mock('@/utils/request', () => ({
  __esModule: true,
  default: jest.fn(),
  post: jest.fn(),
}));

jest.mock('@/components/ui/message', () => ({
  __esModule: true,
  default: { success: jest.fn() },
}));

jest.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));

jest.mock('@/locales/config', () => ({
  __esModule: true,
  default: { t: (key: string) => key },
  DEFAULT_LANGUAGE_CODE: 'en',
  supportedLanguages: [{ code: 'en' }],
}));

jest.mock('../use-warn-empty-model', () => ({
  useWarnEmptyModel: () => ({ warnIfEmpty: jest.fn() }),
}));

const mockRequest = jest.mocked(request);

const renderMutation = <T,>(hook: () => T) => {
  const queryClient = new QueryClient({
    defaultOptions: { mutations: { retry: false }, queries: { retry: false } },
  });
  const wrapper = ({ children }: { children?: any }) =>
    createElement(QueryClientProvider, { client: queryClient }, children);
  return renderHook(hook, { wrapper });
};

beforeEach(() => {
  mockRequest.mockReset();
  mockRequest.mockResolvedValue({ data: { code: 0, data: true } });
});

it('sends PATCH token management through a fixed URL with the token in JSON', async () => {
  const rawKey = 'ragflow-patch-body-only-secret';
  const { result } = renderMutation(() => useUpdateSystemToken());

  await act(async () => {
    await result.current.updateToken({ token: rawKey, name: 'Renamed' });
  });

  expect(mockRequest).toHaveBeenCalledWith('/api/v1/system/tokens', {
    method: 'patch',
    data: { token: rawKey, name: 'Renamed' },
  });
  expect(mockRequest.mock.calls[0][0]).not.toContain(rawKey);
});

it('sends DELETE token management through a fixed URL with the token in JSON', async () => {
  const rawKey = 'ragflow-delete-body-only-secret';
  const { result } = renderMutation(() => useRemoveSystemToken());

  await act(async () => {
    await result.current.removeToken(rawKey);
  });

  expect(mockRequest).toHaveBeenCalledWith('/api/v1/system/tokens', {
    method: 'delete',
    data: { token: rawKey },
  });
  expect(mockRequest.mock.calls[0][0]).not.toContain(rawKey);
});
