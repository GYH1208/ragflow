import { fireEvent, render, screen } from '@testing-library/react';
import i18n from 'i18next';
import { initReactI18next } from 'react-i18next';

import { useFetchKnowledgeList } from '@/hooks/use-knowledge-request';
import translationEn from '@/locales/en';

let ApiKeyForm: typeof import('./api-key-form').default;

jest.mock('@/hooks/use-knowledge-request', () => ({
  useFetchKnowledgeList: jest.fn(),
}));

const React = jest.requireActual<typeof import('react')>('react');
const mockUseFetchKnowledgeList = jest.mocked(useFetchKnowledgeList);

const knowledgeList = [
  { id: 'dataset-1', name: 'Product docs' },
  { id: 'dataset-2', name: 'Support handbook' },
];

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
  ApiKeyForm = jest.requireActual('./api-key-form').default;
});

beforeEach(() => {
  mockUseFetchKnowledgeList.mockReturnValue({
    list: knowledgeList as any,
    loading: false,
  });
});

const fillNameAndType = (name: string, keyType: 'full_access' | 'retrieval') => {
  fireEvent.change(screen.getByLabelText('Key name'), {
    target: { value: name },
  });
  fireEvent.change(screen.getByLabelText('Access type'), {
    target: { value: keyType },
  });
};

const selectDataset = (datasetName: string) => {
  fireEvent.click(screen.getByRole('button', { name: 'Datasets' }));
  fireEvent.click(screen.getByText(datasetName));
};

describe('ApiKeyForm', () => {
  it('requires a name and access type and rejects names longer than 64 characters', () => {
    const onSubmit = jest.fn();
    const { rerender } = render(<ApiKeyForm onSubmit={onSubmit} />);

    fireEvent.click(screen.getByRole('button', { name: 'Create key' }));

    expect(screen.getByText('Key name is required.')).toBeInTheDocument();
    expect(screen.getByText('Access type is required.')).toBeInTheDocument();
    expect(onSubmit).not.toHaveBeenCalled();

    rerender(<ApiKeyForm onSubmit={onSubmit} />);
    fillNameAndType('x'.repeat(65), 'full_access');
    fireEvent.click(screen.getByRole('button', { name: 'Create key' }));

    expect(
      screen.getByText('Key name must be 64 characters or fewer.'),
    ).toBeInTheDocument();
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it('requires at least one dataset for retrieval and defaults expiry to 90 days', () => {
    const onSubmit = jest.fn();
    render(<ApiKeyForm onSubmit={onSubmit} />);

    fillNameAndType('Search integration', 'retrieval');

    expect(screen.getByLabelText('Datasets')).toBeInTheDocument();
    expect(screen.getByLabelText('Expiry')).toHaveValue('90');

    fireEvent.click(screen.getByRole('button', { name: 'Create key' }));
    expect(
      screen.getByText('Select at least one dataset.'),
    ).toBeInTheDocument();

    selectDataset('Product docs');
    fireEvent.click(screen.getByRole('button', { name: 'Create key' }));

    expect(onSubmit).toHaveBeenCalledWith({
      name: 'Search integration',
      key_type: 'retrieval',
      allowed_dataset_ids: ['dataset-1'],
      expires_in_days: 90,
      enabled: true,
    });
  });

  it.each([
    ['30 days', '30', 30],
    ['90 days', '90', 90],
    ['180 days', '180', 180],
    ['365 days', '365', 365],
    ['Never expires', 'forever', null],
  ])('submits the approved %s expiry choice', (_, selectValue, expected) => {
    const onSubmit = jest.fn();
    render(<ApiKeyForm onSubmit={onSubmit} />);
    fillNameAndType('Retrieval client', 'retrieval');
    selectDataset('Support handbook');

    fireEvent.change(screen.getByLabelText('Expiry'), {
      target: { value: selectValue },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Create key' }));

    expect(onSubmit).toHaveBeenCalledWith({
      name: 'Retrieval client',
      key_type: 'retrieval',
      allowed_dataset_ids: ['dataset-2'],
      expires_in_days: expected,
      enabled: true,
    });
  });

  it('clears retrieval scope and expiry when switched to full access and warns about privilege', () => {
    const onSubmit = jest.fn();
    render(<ApiKeyForm onSubmit={onSubmit} />);
    fillNameAndType('Automation', 'retrieval');
    selectDataset('Product docs');

    fireEvent.change(screen.getByLabelText('Access type'), {
      target: { value: 'full_access' },
    });

    expect(screen.queryByLabelText('Datasets')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('Expiry')).not.toBeInTheDocument();
    expect(
      screen.getByText(
        'Full-access keys are plaintext administrator credentials with broad API privileges. Never distribute them to employees or configure them in WorkBuddy.',
      ),
    ).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Create key' }));
    expect(onSubmit).toHaveBeenCalledWith({
      name: 'Automation',
      key_type: 'full_access',
      enabled: true,
    });
  });

  it('locks the type while editing and limits full-access edits to name and enabled', () => {
    const onSubmit = jest.fn();
    render(
      <ApiKeyForm
        onSubmit={onSubmit}
        token={{
          token: 'ragflow-full',
          name: 'Production',
          key_type: 'full_access',
          legacy: false,
          allowed_dataset_ids: [],
          expires_at: null,
          enabled: true,
          total_calls: 2,
          retrieval_calls: 0,
          last_used_at: null,
          last_result: null,
          create_date: '2026-09-28 10:00:00',
          create_time: 1,
          update_date: null,
          update_time: null,
        }}
      />,
    );

    expect(screen.getByLabelText('Access type')).toBeDisabled();
    expect(screen.queryByLabelText('Datasets')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('Expiry')).not.toBeInTheDocument();

    fireEvent.change(screen.getByLabelText('Key name'), {
      target: { value: 'Production renamed' },
    });
    fireEvent.click(screen.getByLabelText('Enabled'));
    fireEvent.click(screen.getByRole('button', { name: 'Save changes' }));

    expect(onSubmit).toHaveBeenCalledWith({
      name: 'Production renamed',
      enabled: false,
    });
  });

  it('keeps a finite edit expiry unchanged until an approved replacement is selected', () => {
    const onSubmit = jest.fn();
    render(
      <ApiKeyForm
        onSubmit={onSubmit}
        token={{
          token: 'ragflow-retrieval',
          name: 'Search client',
          key_type: 'retrieval',
          legacy: false,
          allowed_dataset_ids: ['dataset-1'],
          expires_at: '2099-01-01T00:00:00Z',
          enabled: true,
          total_calls: 2,
          retrieval_calls: 2,
          last_used_at: null,
          last_result: null,
          create_date: '2026-09-28 10:00:00',
          create_time: 1,
          update_date: null,
          update_time: null,
        }}
      />,
    );

    expect(screen.getByLabelText('Expiry')).toHaveValue('');
    expect(
      screen.getByRole('option', {
        name: 'Keep current expiry (01/01/2099 00:00:00)',
      }),
    ).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText('Key name'), {
      target: { value: 'Search client renamed' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Save changes' }));

    expect(onSubmit).toHaveBeenLastCalledWith({
      name: 'Search client renamed',
      allowed_dataset_ids: ['dataset-1'],
      enabled: true,
    });

    fireEvent.change(screen.getByLabelText('Expiry'), {
      target: { value: 'forever' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Save changes' }));

    expect(onSubmit).toHaveBeenLastCalledWith({
      name: 'Search client renamed',
      allowed_dataset_ids: ['dataset-1'],
      expires_in_days: null,
      enabled: true,
    });
  });
});
