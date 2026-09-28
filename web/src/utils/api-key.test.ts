import type { IToken } from '@/interfaces/database/chat';
import { selectFullAccessToken } from './api-key';

describe('selectFullAccessToken', () => {
  it('skips a retrieval key when it is first in the server ordering', () => {
    const tokens = [
      {
        token: 'ragflow-rk-retrieval-first',
        name: 'Newest retrieval key',
        key_type: 'retrieval' as const,
        legacy: false,
        allowed_dataset_ids: ['dataset-1'],
        expires_at: null,
        enabled: true,
        total_calls: 0,
        retrieval_calls: 0,
        last_used_at: null,
        last_result: null,
        create_time: 200,
        create_date: '2026-09-28 10:00:00',
        update_time: null,
        update_date: null,
      },
      {
        token: 'ragflow-full-access',
        name: 'Full access key',
        key_type: 'full_access' as const,
        legacy: false,
        allowed_dataset_ids: [],
        expires_at: null,
        enabled: true,
        total_calls: 2,
        retrieval_calls: 1,
        last_used_at: null,
        last_result: null,
        create_time: 100,
        create_date: '2026-09-28 09:00:00',
        update_time: null,
        update_date: null,
        beta: 'beta-full',
      },
    ];

    expect(selectFullAccessToken(tokens)).toMatchObject({
      token: 'ragflow-full-access',
    });
  });

  it('prefers an explicit full-access key over a legacy key', () => {
    const tokens = [
      {
        token: 'ragflow-legacy',
        name: 'Legacy key',
        legacy: true,
        allowed_dataset_ids: [],
        expires_at: null,
        enabled: true,
        total_calls: 0,
        retrieval_calls: 0,
        last_used_at: null,
        last_result: null,
        create_time: 200,
        create_date: '2026-09-28 10:00:00',
        update_time: null,
        update_date: null,
        beta: 'legacy-beta',
      },
      {
        token: 'ragflow-explicit-full',
        name: 'Explicit full access key',
        key_type: 'full_access' as const,
        legacy: false,
        allowed_dataset_ids: [],
        expires_at: null,
        enabled: true,
        total_calls: 0,
        retrieval_calls: 0,
        last_used_at: null,
        last_result: null,
        create_time: 100,
        create_date: '2026-09-28 09:00:00',
        update_time: null,
        update_date: null,
        beta: 'explicit-beta',
      },
    ];

    expect(selectFullAccessToken(tokens)).toMatchObject({
      token: 'ragflow-explicit-full',
    });
  });

  it('treats a missing key_type as a selectable legacy full-access key', () => {
    const tokens = [
      {
        token: 'ragflow-legacy-only',
        name: 'Legacy full access key',
        legacy: true,
        allowed_dataset_ids: [],
        expires_at: null,
        enabled: true,
        total_calls: 0,
        retrieval_calls: 0,
        last_used_at: null,
        last_result: null,
        create_time: 100,
        create_date: '2026-09-28 09:00:00',
        update_time: null,
        update_date: null,
        beta: 'legacy-beta',
      },
    ];

    expect(selectFullAccessToken(tokens)).toMatchObject({
      token: 'ragflow-legacy-only',
    });
  });

  it('treats a realistic old-server token row as an active full-access key', () => {
    const tokens: IToken[] = [
      {
        token: 'ragflow-old-server-token',
        tenant_id: 'tenant-1',
        create_date: '2026-09-28 09:00:00',
        create_time: 100,
        update_date: undefined,
        update_time: undefined,
        beta: 'legacy-beta',
      },
    ];

    expect(selectFullAccessToken(tokens)).toMatchObject({
      token: 'ragflow-old-server-token',
    });
  });

  it('skips disabled and expired full-access keys', () => {
    const tokens = [
      {
        token: 'ragflow-disabled-full',
        name: 'Disabled full access key',
        key_type: 'full_access' as const,
        legacy: false,
        allowed_dataset_ids: [],
        expires_at: null,
        enabled: false,
        total_calls: 0,
        retrieval_calls: 0,
        last_used_at: null,
        last_result: null,
        create_time: 300,
        create_date: '2026-09-28 11:00:00',
        update_time: null,
        update_date: null,
        beta: 'disabled-beta',
      },
      {
        token: 'ragflow-expired-full',
        name: 'Expired full access key',
        key_type: 'full_access' as const,
        legacy: false,
        allowed_dataset_ids: [],
        expires_at: '2020-01-01T00:00:00Z',
        enabled: true,
        total_calls: 0,
        retrieval_calls: 0,
        last_used_at: null,
        last_result: null,
        create_time: 200,
        create_date: '2026-09-28 10:00:00',
        update_time: null,
        update_date: null,
        beta: 'expired-beta',
      },
      {
        token: 'ragflow-active-full',
        name: 'Active full access key',
        key_type: 'full_access' as const,
        legacy: false,
        allowed_dataset_ids: [],
        expires_at: null,
        enabled: true,
        total_calls: 0,
        retrieval_calls: 0,
        last_used_at: null,
        last_result: null,
        create_time: 100,
        create_date: '2026-09-28 09:00:00',
        update_time: null,
        update_date: null,
        beta: 'active-beta',
      },
    ];

    expect(selectFullAccessToken(tokens)).toMatchObject({
      token: 'ragflow-active-full',
    });
  });

  it('returns undefined when no active full-access key is available', () => {
    const tokens = [
      {
        token: 'ragflow-rk-only',
        name: 'Retrieval key',
        key_type: 'retrieval' as const,
        legacy: false,
        allowed_dataset_ids: ['dataset-1'],
        expires_at: null,
        enabled: true,
        total_calls: 0,
        retrieval_calls: 0,
        last_used_at: null,
        last_result: null,
        create_time: 200,
        create_date: '2026-09-28 10:00:00',
        update_time: null,
        update_date: null,
      },
      {
        token: 'ragflow-disabled-only',
        name: 'Disabled full access key',
        key_type: 'full_access' as const,
        legacy: false,
        allowed_dataset_ids: [],
        expires_at: null,
        enabled: false,
        total_calls: 0,
        retrieval_calls: 0,
        last_used_at: null,
        last_result: null,
        create_time: 100,
        create_date: '2026-09-28 09:00:00',
        update_time: null,
        update_date: null,
        beta: 'disabled-beta',
      },
    ];

    expect(selectFullAccessToken(tokens)).toBeUndefined();
  });
});
