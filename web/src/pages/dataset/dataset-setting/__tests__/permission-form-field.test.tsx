import { Form } from '@/components/ui/form';
import { TooltipProvider } from '@/components/ui/tooltip';
import { formSchema } from '@/pages/dataset/dataset-setting/form-schema';
import { zodResolver } from '@hookform/resolvers/zod';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { useEffect } from 'react';
import { useForm } from 'react-hook-form';

import { PermissionFormField } from '../permission-form-field';

const React = jest.requireActual<typeof import('react')>('react');
(globalThis as any).React = React;

jest.mock('@/hooks/use-team-request', () => ({ useTeams: jest.fn() }));
jest.mock('@/hooks/use-user-setting-request', () => ({
  useFetchUserInfo: jest.fn(),
}));

jest.mock('@/components/originui/select-with-search', () => {
  const { forwardRef } = jest.requireActual<typeof import('react')>('react');
  return {
    SelectWithSearch: forwardRef<
      HTMLSelectElement,
      {
        options?: Array<{ label: string; value?: string }>;
        value?: string;
        onChange?: (nextValue: string) => void;
        testId?: string;
      }
    >(({ options = [], value, onChange, testId }, ref) => (
      <select
        ref={ref}
        data-testid={testId}
        value={value ?? ''}
        onChange={(event) => onChange?.(event.target.value)}
      >
        <option value="">请选择</option>
        {options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
    )),
  };
});

jest.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) =>
      ({
        'knowledgeConfiguration.permissions': '权限',
        'knowledgeConfiguration.permissionsTip': '权限说明',
        'knowledgeConfiguration.me': '只有我',
        'knowledgeConfiguration.team': '团队',
        'knowledgeConfiguration.teamPlaceholder': '请选择团队',
      })[key] ?? key,
  }),
}));

jest.mock('i18next', () => ({
  t: (key: string) =>
    key === 'knowledgeConfiguration.teamPlaceholder' ? '请选择团队' : key,
}));

const { useTeams } = jest.requireMock('@/hooks/use-team-request') as {
  useTeams: jest.Mock;
};
const { useFetchUserInfo } = jest.requireMock(
  '@/hooks/use-user-setting-request',
) as { useFetchUserInfo: jest.Mock };

type FormValues = Record<string, any>;

const defaultValues: FormValues = {
  parse_type: 1,
  name: '知识库',
  chunk_method: 'naive',
  embedding_model: 'embedding',
  pagerank: 0,
  permission: 'me',
  team_id: null,
};

function PermissionFormHarness({
  values,
  onReady,
}: {
  values?: Partial<FormValues>;
  onReady?: (form: any) => void;
}) {
  const form = useForm<FormValues>({
    resolver: zodResolver(formSchema),
    defaultValues: { ...defaultValues, ...values },
  });

  useEffect(() => onReady?.(form), [form, onReady]);

  return (
    <TooltipProvider>
      <Form {...form}>
        <form>
          <PermissionFormField />
          <button type="button" onClick={() => void form.trigger()}>
            保存
          </button>
        </form>
      </Form>
    </TooltipProvider>
  );
}

describe('PermissionFormField', () => {
  beforeEach(() => {
    useFetchUserInfo.mockReturnValue({
      data: { is_superuser: true },
      loading: false,
    });
    useTeams.mockReturnValue({
      data: [
        {
          id: 'team-hr',
          name: 'HR 团队',
          membership_state: 'owner',
          can_manage: true,
        },
        {
          id: 'team-product',
          name: '产品团队',
          membership_state: 'active',
          can_manage: false,
        },
      ],
      loading: false,
    });
  });

  it('requires a team when permission is team', () => {
    const result = formSchema.safeParse({
      ...defaultValues,
      permission: 'team',
      team_id: null,
    });

    expect(result.success).toBe(false);
    if (!result.success) {
      expect(result.error.issues).toEqual(
        expect.arrayContaining([
          expect.objectContaining({
            path: ['team_id'],
            message: '请选择团队',
          }),
        ]),
      );
    }
  });

  it('clears team_id when switching to me', async () => {
    let form: any;
    render(
      <PermissionFormHarness
        values={{ permission: 'team', team_id: 'team-hr' }}
        onReady={(nextForm) => {
          form = nextForm;
        }}
      />,
    );

    fireEvent.change(
      screen.getByTestId('ds-settings-basic-permissions-select'),
      { target: { value: 'me' } },
    );

    await waitFor(() => {
      expect(form?.getValues()).toMatchObject({
        permission: 'me',
        team_id: null,
      });
    });
  });
});
