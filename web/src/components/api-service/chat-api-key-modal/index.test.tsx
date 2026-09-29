import {
  act,
  fireEvent,
  render,
  renderHook,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import i18n from 'i18next';
import { initReactI18next } from 'react-i18next';

import { useFetchKnowledgeList } from '@/hooks/use-knowledge-request';
import {
  useCreateSystemToken,
  useFetchSystemTokenList,
  useRemoveSystemToken,
  useUpdateSystemToken,
} from '@/hooks/use-user-setting-request';
import translationEn from '@/locales/en';
import { useOperateApiKey } from '../hooks';

jest.mock('../hooks', () => ({
  useOperateApiKey: jest.fn(),
}));

jest.mock('@/hooks/use-knowledge-request', () => ({
  useFetchKnowledgeList: jest.fn(),
}));

jest.mock('@/hooks/use-user-setting-request', () => ({
  useCreateSystemToken: jest.fn(),
  useFetchManualSystemTokenList: jest.fn(),
  useFetchSystemTokenList: jest.fn(),
  useRemoveSystemToken: jest.fn(),
  useUpdateSystemToken: jest.fn(),
}));

const React = jest.requireActual<typeof import('react')>('react');
const mockUseOperateApiKey = jest.mocked(useOperateApiKey);
const mockUseFetchKnowledgeList = jest.mocked(useFetchKnowledgeList);
const mockUseCreateSystemToken = jest.mocked(useCreateSystemToken);
const mockUseFetchSystemTokenList = jest.mocked(useFetchSystemTokenList);
const mockUseRemoveSystemToken = jest.mocked(useRemoveSystemToken);
const mockUseUpdateSystemToken = jest.mocked(useUpdateSystemToken);
const createToken = jest.fn().mockResolvedValue(true);
const updateToken = jest.fn().mockResolvedValue(true);
const removeToken = jest.fn().mockResolvedValue(true);

let ChatApiKeyModal: typeof import('./index').default;
let BackendServiceApi: typeof import('../chat-overview-modal/backend-service-api').default;
let canManageApiKeys: typeof import('../chat-overview-modal/backend-service-api').canManageApiKeys;
let TooltipProvider: typeof import('@/components/ui/tooltip').TooltipProvider;
let useOperateApiKeyActual: () => any;

const tokenList = [
  {
    token: 'ragflow-legacy',
    tenant_id: 'tenant-1',
    create_date: '2026-09-20 08:00:00',
    create_time: 1,
    beta: 'legacy-beta',
  },
  {
    token: 'ragflow-full',
    name: 'Admin CLI',
    key_type: 'full_access' as const,
    legacy: false,
    allowed_dataset_ids: [],
    expires_at: null,
    enabled: true,
    total_calls: 4,
    retrieval_calls: 0,
    last_used_at: '2026-09-27T11:00:00Z',
    last_result: 'success',
    create_date: '2026-09-21 09:00:00',
    create_time: 2,
    update_date: null,
    update_time: null,
  },
  {
    token: 'ragflow-retrieval',
    name: 'Search client',
    key_type: 'retrieval' as const,
    legacy: false,
    allowed_dataset_ids: ['dataset-1', 'dataset-2'],
    expires_at: '2099-01-01T00:00:00Z',
    enabled: true,
    total_calls: 12,
    retrieval_calls: 7,
    last_used_at: '2026-09-28T10:00:00Z',
    last_result: 'success',
    create_date: '2026-09-22 10:00:00',
    create_time: 3,
    update_date: null,
    update_time: null,
  },
  {
    token: 'ragflow-disabled',
    name: 'Disabled client',
    key_type: 'retrieval' as const,
    legacy: false,
    allowed_dataset_ids: ['dataset-1'],
    expires_at: null,
    enabled: false,
    total_calls: 0,
    retrieval_calls: 0,
    last_used_at: null,
    last_result: null,
    create_date: '2026-09-23 10:00:00',
    create_time: 4,
    update_date: null,
    update_time: null,
  },
  {
    token: 'ragflow-expired',
    name: 'Expired client',
    key_type: 'retrieval' as const,
    legacy: false,
    allowed_dataset_ids: ['dataset-1'],
    expires_at: '2020-01-01T00:00:00Z',
    enabled: true,
    total_calls: 1,
    retrieval_calls: 1,
    last_used_at: null,
    last_result: 'denied',
    create_date: '2026-09-24 10:00:00',
    create_time: 5,
    update_date: null,
    update_time: null,
  },
];

const hookState = {
  createToken,
  updateToken,
  removeToken,
  tokenList,
  listLoading: false,
  operationLoading: false,
  error: null as Error | null,
  operationError: null as Error | null,
};

void i18n.use(initReactI18next).init({
  lng: 'en',
  resources: { en: translationEn },
  interpolation: { escapeValue: false },
});

beforeAll(() => {
  const { TextDecoder, TextEncoder } = jest.requireActual('util');
  class ResizeObserver {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
  Object.assign(globalThis, {
    React,
    ResizeObserver,
    TextDecoder,
    TextEncoder,
  });
  HTMLElement.prototype.scrollIntoView = jest.fn();
  HTMLElement.prototype.hasPointerCapture = jest.fn();
  HTMLElement.prototype.releasePointerCapture = jest.fn();
  ChatApiKeyModal = jest.requireActual('./index').default;
  TooltipProvider = jest.requireActual('@/components/ui/tooltip').TooltipProvider;
  const backendModule = jest.requireActual(
    '../chat-overview-modal/backend-service-api',
  );
  BackendServiceApi = backendModule.default;
  canManageApiKeys = backendModule.canManageApiKeys;
  useOperateApiKeyActual = jest.requireActual('../hooks').useOperateApiKey;
});

beforeEach(() => {
  jest.clearAllMocks();
  Object.assign(hookState, {
    tokenList,
    listLoading: false,
    operationLoading: false,
    error: null,
    operationError: null,
  });
  mockUseOperateApiKey.mockImplementation(() => hookState);
  mockUseFetchKnowledgeList.mockReturnValue({
    list: [
      { id: 'dataset-1', name: 'Product docs' },
      { id: 'dataset-2', name: 'Support handbook' },
    ] as any,
    loading: false,
  });
  mockUseCreateSystemToken.mockReturnValue({
    createToken: jest.fn().mockResolvedValue({ code: 0 }),
    data: undefined,
    loading: false,
  } as any);
  mockUseUpdateSystemToken.mockReturnValue({
    updateToken: jest.fn().mockResolvedValue({ code: 0 }),
    data: undefined,
    loading: false,
  } as any);
  mockUseRemoveSystemToken.mockReturnValue({
    removeToken: jest.fn().mockResolvedValue({ code: 0 }),
    data: undefined,
    loading: false,
  } as any);
  mockUseFetchSystemTokenList.mockReturnValue({
    data: [],
    error: null,
    loading: false,
    refetch: jest.fn().mockResolvedValue(undefined),
  } as any);
});

const Provider = ({ children }: React.PropsWithChildren) => (
  <TooltipProvider>{children}</TooltipProvider>
);

const renderWithProviders = (ui: React.ReactElement) =>
  render(ui, { wrapper: Provider });

const renderModal = () =>
  renderWithProviders(
    <ChatApiKeyModal hideModal={jest.fn()} idKey="dialog_id" />,
  );

describe('ChatApiKeyModal', () => {
  it('renders the unified plaintext list, scope, lifecycle, usage, and security labels', () => {
    renderModal();

    expect(screen.getByText('Admin CLI')).toBeInTheDocument();
    expect(screen.getByText('Search client')).toBeInTheDocument();
    expect(screen.getByText('ragflow-full')).toBeInTheDocument();
    expect(screen.getAllByText('Full access')).not.toHaveLength(0);
    expect(screen.getAllByText('Retrieval')).not.toHaveLength(0);
    expect(screen.getByText('Legacy')).toBeInTheDocument();
    expect(screen.getAllByText('Product docs')).not.toHaveLength(0);
    expect(screen.getByText(/Support handbook/)).toBeInTheDocument();
    expect(screen.getByText('2 knowledge bases')).toBeInTheDocument();
    expect(screen.getAllByText('Active')).not.toHaveLength(0);
    expect(screen.getByText('Disabled')).toBeInTheDocument();
    expect(screen.getByText('Expired')).toBeInTheDocument();
    expect(screen.getByText('12 total / 7 retrieval')).toBeInTheDocument();
    expect(screen.getAllByText('Success')).not.toHaveLength(0);
    expect(screen.getByText('22/09/2026 10:00:00')).toBeInTheDocument();
    expect(screen.getByText('01/01/2099 00:00:00')).toBeInTheDocument();
    expect(
      screen.getAllByText(
        'Full-access keys are plaintext administrator credentials with broad API privileges. Never distribute them to employees or configure them in WorkBuddy.',
      ),
    ).not.toHaveLength(0);
    expect(
      screen.getByRole('button', { name: 'Create new key' }),
    ).toBeEnabled();

    const fullAccessRow = screen.getByText('Admin CLI').closest('tr');
    expect(fullAccessRow).not.toBeNull();
    expect(within(fullAccessRow!).getAllByText('—')).toHaveLength(2);

    expect(
      within(fullAccessRow!).getByRole('button', {
        name: 'Copy ragflow-full',
      }),
    ).toBeEnabled();
  });

  it('opens create/edit forms and sends exact create and update payloads', async () => {
    renderModal();

    fireEvent.click(screen.getByRole('button', { name: 'Create new key' }));
    fireEvent.change(screen.getByLabelText('Key name'), {
      target: { value: 'Full automation' },
    });
    fireEvent.change(screen.getByLabelText('Access type'), {
      target: { value: 'full_access' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Create key' }));

    await waitFor(() =>
      expect(createToken).toHaveBeenCalledWith({
        name: 'Full automation',
        key_type: 'full_access',
      }),
    );

    fireEvent.click(screen.getByRole('button', { name: 'Edit Search client' }));
    fireEvent.change(screen.getByLabelText('Key name'), {
      target: { value: 'Search client renamed' },
    });
    fireEvent.change(screen.getByLabelText('Expiry'), {
      target: { value: '30' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Save changes' }));

    await waitFor(() =>
      expect(updateToken).toHaveBeenCalledWith('ragflow-retrieval', {
        name: 'Search client renamed',
        allowed_dataset_ids: ['dataset-1', 'dataset-2'],
        expires_in_days: 30,
        enabled: true,
      }),
    );
  });

  it('supports enable, disable, and delete actions', () => {
    renderModal();

    fireEvent.click(
      screen.getByRole('button', { name: 'Disable Search client' }),
    );
    expect(updateToken).toHaveBeenCalledWith('ragflow-retrieval', {
      enabled: false,
    });

    fireEvent.click(
      screen.getByRole('button', { name: 'Enable Disabled client' }),
    );
    expect(updateToken).toHaveBeenCalledWith('ragflow-disabled', {
      enabled: true,
    });

    fireEvent.click(
      screen.getByRole('button', { name: 'Delete Expired client' }),
    );
    expect(removeToken).toHaveBeenCalledWith('ragflow-expired');
  });

  it('renders loading, empty, failure, and mutation-pending states', () => {
    Object.assign(hookState, { listLoading: true, tokenList: [] });
    const { rerender } = renderModal();
    expect(screen.getByText('Loading API keys…')).toBeInTheDocument();

    Object.assign(hookState, { listLoading: false, tokenList: [] });
    rerender(<ChatApiKeyModal hideModal={jest.fn()} idKey="dialog_id" />);
    expect(screen.getByText('No API keys yet.')).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: 'Create new key' }),
    ).toBeEnabled();

    Object.assign(hookState, { error: new Error('network unavailable') });
    rerender(<ChatApiKeyModal hideModal={jest.fn()} idKey="dialog_id" />);
    expect(
      screen.getByText('API keys could not be loaded. Try again.'),
    ).toBeInTheDocument();

    Object.assign(hookState, {
      error: null,
      tokenList,
      operationLoading: true,
    });
    rerender(<ChatApiKeyModal hideModal={jest.fn()} idKey="dialog_id" />);
    expect(screen.getByText('Saving API key…')).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: 'Create new key' }),
    ).toBeDisabled();
  });

  it('keeps a failed create form open with its input and visible error', async () => {
    createToken.mockResolvedValueOnce(false);
    const { rerender } = renderModal();

    fireEvent.click(screen.getByRole('button', { name: 'Create new key' }));
    fireEvent.change(screen.getByLabelText('Key name'), {
      target: { value: 'Keep this value' },
    });
    fireEvent.change(screen.getByLabelText('Access type'), {
      target: { value: 'full_access' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Create key' }));

    await waitFor(() =>
      expect(screen.getByLabelText('Key name')).toHaveValue('Keep this value'),
    );

    const operationError = new Error('create denied');
    Object.assign(hookState, { error: operationError, operationError });
    rerender(<ChatApiKeyModal hideModal={jest.fn()} idKey="dialog_id" />);
    expect(screen.getByLabelText('Key name')).toHaveValue('Keep this value');
    expect(
      screen.getByText('API key operation failed. Try again.'),
    ).toBeInTheDocument();
  });

  it('keeps a failed update form open with its edited input', async () => {
    updateToken.mockResolvedValueOnce(false);
    renderModal();

    fireEvent.click(screen.getByRole('button', { name: 'Edit Search client' }));
    fireEvent.change(screen.getByLabelText('Key name'), {
      target: { value: 'Preserve failed rename' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Save changes' }));

    await waitFor(() =>
      expect(screen.getByLabelText('Key name')).toHaveValue(
        'Preserve failed rename',
      ),
    );
    expect(screen.getByLabelText('Access type')).toBeDisabled();
  });
});

describe('useOperateApiKey mutation outcomes', () => {
  it('returns success and refetches after a code-zero create response', async () => {
    const createMutation = jest.fn().mockResolvedValue({ code: 0 });
    const refetch = jest.fn().mockResolvedValue(undefined);
    mockUseCreateSystemToken.mockReturnValue({
      createToken: createMutation,
      data: undefined,
      loading: false,
    } as any);
    mockUseFetchSystemTokenList.mockReturnValue({
      data: [],
      error: null,
      loading: false,
      refetch,
    } as any);
    const { result } = renderHook(() => useOperateApiKeyActual());
    let succeeded: boolean | undefined;

    await act(async () => {
      succeeded = await result.current.createToken({
        name: 'Created key',
        key_type: 'full_access',
      });
    });

    expect(succeeded).toBe(true);
    expect(refetch).toHaveBeenCalledTimes(1);
    expect(result.current.error).toBeNull();
  });

  it('returns failure and skips refetch when create rejects', async () => {
    const createError = new Error('create unavailable');
    const createMutation = jest.fn().mockRejectedValue(createError);
    const refetch = jest.fn();
    mockUseCreateSystemToken.mockReturnValue({
      createToken: createMutation,
      data: undefined,
      loading: false,
    } as any);
    mockUseFetchSystemTokenList.mockReturnValue({
      data: [],
      error: null,
      loading: false,
      refetch,
    } as any);
    const { result } = renderHook(() => useOperateApiKeyActual());
    let succeeded: boolean | undefined;

    await act(async () => {
      succeeded = await result.current.createToken({
        name: 'Rejected key',
        key_type: 'full_access',
      });
    });

    expect(succeeded).toBe(false);
    expect(refetch).not.toHaveBeenCalled();
    expect(result.current.error).toBe(createError);
  });

  it('returns failure and skips refetch for a resolved nonzero update response', async () => {
    const updateMutation = jest.fn().mockResolvedValue({
      code: 403,
      message: 'update denied',
    });
    const refetch = jest.fn();
    mockUseUpdateSystemToken.mockReturnValue({
      updateToken: updateMutation,
      data: undefined,
      loading: false,
    } as any);
    mockUseFetchSystemTokenList.mockReturnValue({
      data: [],
      error: null,
      loading: false,
      refetch,
    } as any);
    const { result } = renderHook(() => useOperateApiKeyActual());
    let succeeded: boolean | undefined;

    await act(async () => {
      succeeded = await result.current.updateToken('ragflow-retrieval', {
        name: 'Denied rename',
      });
    });

    expect(succeeded).toBe(false);
    expect(refetch).not.toHaveBeenCalled();
    expect(result.current.error).toEqual(new Error('update denied'));
  });
});

describe('API key management visibility', () => {
  it.each([
    ['owner', false],
    ['admin', false],
    ['member', true],
  ])('shows key management for %s or superuser identities', (role, isSuperuser) => {
    renderWithProviders(
      <BackendServiceApi
        show={jest.fn()}
        canManageKeys={canManageApiKeys(
          { role },
          { is_superuser: isSuperuser },
        )}
      />,
    );

    expect(screen.getByText('RAGFlow API')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'API KEY' })).toBeInTheDocument();
  });

  it('keeps API documentation visible but hides key management from members', () => {
    renderWithProviders(
      <BackendServiceApi
        show={jest.fn()}
        canManageKeys={canManageApiKeys(
          { role: 'member' },
          { is_superuser: false },
        )}
      />,
    );

    expect(screen.getByText('RAGFlow API')).toBeInTheDocument();
    expect(screen.getByText('API server')).toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: 'API KEY' }),
    ).not.toBeInTheDocument();
  });
});
