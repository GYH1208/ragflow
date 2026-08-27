import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, renderHook, waitFor } from '@testing-library/react';
import { createElement } from 'react';

import {
  createTeam,
  deleteTeam,
  inviteTeamMember,
  listTeamMembers,
  listTeams,
  removeTeamMember,
  renameTeam,
  updateTeamInvitation,
} from '@/services/team-service';

import {
  useCreateTeam,
  useDeleteTeam,
  useInviteTeamMember,
  useLeaveTeam,
  useRemoveTeamMember,
  useRenameTeam,
  useTeamMembers,
  useTeams,
  useUpdateTeamInvitation,
} from '../use-team-request';

jest.mock('@/services/team-service', () => ({
  createTeam: jest.fn(),
  deleteTeam: jest.fn(),
  inviteTeamMember: jest.fn(),
  listTeamMembers: jest.fn(),
  listTeams: jest.fn(),
  removeTeamMember: jest.fn(),
  renameTeam: jest.fn(),
  updateTeamInvitation: jest.fn(),
}));

const mockCreateTeam = jest.mocked(createTeam);
const mockDeleteTeam = jest.mocked(deleteTeam);
const mockInviteTeamMember = jest.mocked(inviteTeamMember);
const mockListTeamMembers = jest.mocked(listTeamMembers);
const mockListTeams = jest.mocked(listTeams);
const mockRemoveTeamMember = jest.mocked(removeTeamMember);
const mockRenameTeam = jest.mocked(renameTeam);
const mockUpdateTeamInvitation = jest.mocked(updateTeamInvitation);

const ownedTeam = {
  id: 'team-1',
  tenant_id: 'admin-1',
  name: 'HR 团队',
  created_by: 'admin-1',
  status: '1',
  create_date: '2026-08-27T00:00:00Z',
  create_time: 1,
  update_date: '2026-08-27T00:00:00Z',
  update_time: 1,
  membership_state: 'owner' as const,
  member_count: 2,
  dataset_count: 3,
  can_manage: true,
};

const invitedMember = {
  id: 'user-2',
  email: 'member@example.com',
  nickname: 'Member',
  avatar: null,
  state: 'invited' as const,
};

const response = <T,>(data: T) => ({
  data: {
    code: 0,
    data,
    message: '',
    status: 200,
  },
});

const renderTeamHook = <T,>(hook: () => T) => {
  const queryClient = new QueryClient({
    defaultOptions: {
      mutations: { retry: false },
      queries: { retry: false },
    },
  });
  const invalidateQueries = jest.spyOn(queryClient, 'invalidateQueries');
  const wrapper = ({ children }: { children?: any }) =>
    createElement(QueryClientProvider, { client: queryClient }, children);

  return {
    ...renderHook(hook, { wrapper }),
    invalidateQueries,
    queryClient,
  };
};

beforeEach(() => {
  jest.clearAllMocks();
});

test('loads the flat owned, active, and invited team list returned by the service', async () => {
  mockListTeams.mockResolvedValue(response([ownedTeam]) as never);
  const { result } = renderTeamHook(() => useTeams());

  await waitFor(() => expect(result.current.loading).toBe(false));

  expect(mockListTeams).toHaveBeenCalledWith();
  expect(result.current.data).toEqual([ownedTeam]);
});

test('loads members for the requested team', async () => {
  mockListTeamMembers.mockResolvedValue(response([invitedMember]) as never);
  const { result } = renderTeamHook(() => useTeamMembers('team-1'));

  await waitFor(() => expect(result.current.loading).toBe(false));

  expect(mockListTeamMembers).toHaveBeenCalledWith('team-1');
  expect(result.current.data).toEqual([invitedMember]);
});

test('creates a team with its name and invalidates all team queries', async () => {
  mockCreateTeam.mockResolvedValue(response(ownedTeam) as never);
  const { result, invalidateQueries } = renderTeamHook(() => useCreateTeam());

  await act(() => result.current.createTeam('HR 团队'));

  expect(mockCreateTeam).toHaveBeenCalledWith('HR 团队');
  expect(invalidateQueries).toHaveBeenCalledWith({ queryKey: ['teams'] });
});

test('renames the requested team and invalidates all team queries', async () => {
  mockRenameTeam.mockResolvedValue(
    response({ ...ownedTeam, name: '财务团队' }) as never,
  );
  const { result, invalidateQueries } = renderTeamHook(() => useRenameTeam());

  await act(() =>
    result.current.renameTeam({ teamId: 'team-1', name: '财务团队' }),
  );

  expect(mockRenameTeam).toHaveBeenCalledWith('team-1', '财务团队');
  expect(invalidateQueries).toHaveBeenCalledWith({ queryKey: ['teams'] });
});

test('deletes the requested team and invalidates all team queries', async () => {
  mockDeleteTeam.mockResolvedValue(
    response({ unassigned_dataset_count: 3 }) as never,
  );
  const { result, invalidateQueries } = renderTeamHook(() => useDeleteTeam());

  await act(() => result.current.deleteTeam('team-1'));

  expect(mockDeleteTeam).toHaveBeenCalledWith('team-1');
  expect(invalidateQueries).toHaveBeenCalledWith({ queryKey: ['teams'] });
});

test('invites by team and email and invalidates team and member queries', async () => {
  mockInviteTeamMember.mockResolvedValue(response(invitedMember) as never);
  const { result, invalidateQueries } = renderTeamHook(() =>
    useInviteTeamMember(),
  );

  await act(() =>
    result.current.inviteTeamMember({
      teamId: 'team-1',
      email: 'member@example.com',
    }),
  );

  expect(mockInviteTeamMember).toHaveBeenCalledWith(
    'team-1',
    'member@example.com',
  );
  expect(invalidateQueries).toHaveBeenCalledWith({ queryKey: ['teams'] });
  expect(invalidateQueries).toHaveBeenCalledWith({
    queryKey: ['teams', 'team-1', 'members'],
  });
});

test('removes the requested user and invalidates team and member queries', async () => {
  mockRemoveTeamMember.mockResolvedValue(response(true) as never);
  const { result, invalidateQueries } = renderTeamHook(() =>
    useRemoveTeamMember(),
  );

  await act(() =>
    result.current.removeTeamMember({ teamId: 'team-1', userId: 'user-2' }),
  );

  expect(mockRemoveTeamMember).toHaveBeenCalledWith('team-1', 'user-2');
  expect(invalidateQueries).toHaveBeenCalledWith({ queryKey: ['teams'] });
  expect(invalidateQueries).toHaveBeenCalledWith({
    queryKey: ['teams', 'team-1', 'members'],
  });
});

test.each(['accept', 'reject'] as const)(
  '%ss an invitation and invalidates all team queries',
  async (action) => {
    mockUpdateTeamInvitation.mockResolvedValue(response(true) as never);
    const { result, invalidateQueries } = renderTeamHook(() =>
      useUpdateTeamInvitation(),
    );

    await act(() =>
      result.current.updateInvitation({ teamId: 'team-1', action }),
    );

    expect(mockUpdateTeamInvitation).toHaveBeenCalledWith('team-1', action);
    expect(invalidateQueries).toHaveBeenCalledWith({ queryKey: ['teams'] });
  },
);

test('leaves as the current member and invalidates all team queries', async () => {
  mockRemoveTeamMember.mockResolvedValue(response(true) as never);
  const { result, invalidateQueries } = renderTeamHook(() => useLeaveTeam());

  await act(() =>
    result.current.leaveTeam({ teamId: 'team-1', userId: 'user-1' }),
  );

  expect(mockRemoveTeamMember).toHaveBeenCalledWith('team-1', 'user-1');
  expect(invalidateQueries).toHaveBeenCalledWith({ queryKey: ['teams'] });
  expect(invalidateQueries).not.toHaveBeenCalledWith({
    queryKey: ['teams', 'team-1', 'members'],
  });
});
