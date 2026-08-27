import message from '@/components/ui/message';
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';

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
} from '@/hooks/use-team-request';
import { useFetchUserInfo } from '@/hooks/use-user-setting-request';

import UserSettingTeam from '../index';

const React = jest.requireActual<typeof import('react')>('react');
(globalThis as any).React = React;

jest.mock('@/hooks/use-team-request', () => ({
  useCreateTeam: jest.fn(),
  useDeleteTeam: jest.fn(),
  useInviteTeamMember: jest.fn(),
  useLeaveTeam: jest.fn(),
  useRemoveTeamMember: jest.fn(),
  useRenameTeam: jest.fn(),
  useTeamMembers: jest.fn(),
  useTeams: jest.fn(),
  useUpdateTeamInvitation: jest.fn(),
}));

jest.mock('@/hooks/use-user-setting-request', () => ({
  useFetchUserInfo: jest.fn(),
}));

jest.mock('@/components/ui/message', () => ({
  __esModule: true,
  default: { error: jest.fn() },
}));

jest.mock('@/components/spotlight', () => () => null);

jest.mock('react-router', () => ({ Link: 'a' }));

jest.mock('../../components/user-setting-header', () => ({
  ProfileSettingWrapperCard: ({
    children,
    header,
  }: {
    children: any;
    header: any;
  }) => (
    <div>
      {header}
      {children}
    </div>
  ),
}));

const translations: Record<string, string> = {
  'common.action': '操作',
  'common.cancel': '取消',
  'common.delete': '删除',
  'common.name': '名称',
  'common.noData': '暂无数据',
  'common.ok': '确定',
  'common.required': '必填',
  'common.search': '搜索',
  'setting.acceptInvitation': '接受',
  'setting.activeTeams': '已加入',
  'setting.createTeam': '创建团队',
  'setting.datasetCount': '知识库数',
  'setting.deleteTeam': '删除团队',
  'setting.deleteTeamWarning': '知识库不会删除，将自动变为只有我',
  'setting.email': '邮箱',
  'setting.invite': '邀请成员',
  'setting.invitedTeams': '待接受邀请',
  'setting.joinedTeams': '加入的团队',
  'setting.leaveTeam': '退出团队',
  'setting.loadingMembers': '正在加载成员……',
  'setting.loadingTeams': '正在加载团队……',
  'setting.memberCount': '成员数',
  'setting.ownedTeams': '我的团队',
  'setting.rejectInvitation': '拒绝',
  'setting.removeMember': '移除成员',
  'setting.renameTeam': '重命名团队',
  'setting.role': '状态',
  'setting.selectTeam': '请选择团队',
  'setting.teamMembers': '团队成员',
  'setting.teamName': '团队名称',
  'setting.teamOperationFailed': '团队操作失败',
  'setting.workspace': '工作区',
};

jest.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) => translations[key] ?? key,
  }),
}));

const mockUseCreateTeam = jest.mocked(useCreateTeam);
const mockUseDeleteTeam = jest.mocked(useDeleteTeam);
const mockUseInviteTeamMember = jest.mocked(useInviteTeamMember);
const mockUseLeaveTeam = jest.mocked(useLeaveTeam);
const mockUseRemoveTeamMember = jest.mocked(useRemoveTeamMember);
const mockUseRenameTeam = jest.mocked(useRenameTeam);
const mockUseTeamMembers = jest.mocked(useTeamMembers);
const mockUseTeams = jest.mocked(useTeams);
const mockUseUpdateTeamInvitation = jest.mocked(useUpdateTeamInvitation);
const mockUseFetchUserInfo = jest.mocked(useFetchUserInfo);
const mockMessageError = jest.mocked(message.error);

const createDeferred = <T,>() => {
  let resolve!: (value: T | PromiseLike<T>) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });

  return { promise, reject, resolve };
};

const successfulMutation = (input?: unknown) => {
  void input;
  return Promise.resolve({ code: 0, data: true, message: '', status: 200 });
};
type MutationResponse = Awaited<ReturnType<typeof successfulMutation>>;

const failedMutationResponse = (messageText: string): MutationResponse => ({
  code: 100,
  data: true,
  message: messageText,
  status: 200,
});
const mockCreateTeam = jest.fn(successfulMutation);
const mockDeleteTeam = jest.fn(successfulMutation);
const mockInviteTeamMember = jest.fn(successfulMutation);
const mockLeaveTeam = jest.fn(successfulMutation);
const mockRemoveTeamMember = jest.fn(successfulMutation);
const mockRenameTeam = jest.fn(successfulMutation);
const mockUpdateInvitation = jest.fn(successfulMutation);

const ownedTeam = {
  id: 'team-hr',
  tenant_id: 'tenant-admin',
  name: 'HR 团队',
  created_by: 'admin-1',
  status: '1',
  create_date: '2026-08-27T00:00:00Z',
  create_time: 1,
  update_date: '2026-08-27T00:00:00Z',
  update_time: 1,
  membership_state: 'owner',
  member_count: 2,
  dataset_count: 3,
  can_manage: true,
} satisfies import('@/interfaces/database/user-setting').ITeam;

const financeTeam = {
  ...ownedTeam,
  id: 'team-finance',
  name: '财务团队',
  member_count: 1,
  dataset_count: 1,
} satisfies import('@/interfaces/database/user-setting').ITeam;

const activeTeam = {
  ...ownedTeam,
  id: 'team-active',
  name: '产品团队',
  membership_state: 'active',
  can_manage: false,
} satisfies import('@/interfaces/database/user-setting').ITeam;

const invitedTeam = {
  ...ownedTeam,
  id: 'team-invited',
  name: '法务团队',
  membership_state: 'invited',
  can_manage: false,
} satisfies import('@/interfaces/database/user-setting').ITeam;

const member = {
  id: 'member-1',
  email: 'alice@example.com',
  nickname: 'Alice',
  avatar: null,
  state: 'active',
} satisfies import('@/interfaces/database/user-setting').ITeamMember;

const userInfo = {
  id: 'admin-1',
  nickname: 'Admin',
  is_superuser: true,
} as import('@/interfaces/database/user-setting').IUserInfo;

let teams: import('@/interfaces/database/user-setting').ITeam[];
let members: import('@/interfaces/database/user-setting').ITeamMember[];
let deletingTeam = false;
let invitationLoading = false;
let leavingTeam = false;
let removingMember = false;

beforeEach(() => {
  jest.clearAllMocks();
  teams = [ownedTeam, activeTeam, invitedTeam];
  members = [member];
  deletingTeam = false;
  invitationLoading = false;
  leavingTeam = false;
  removingMember = false;

  mockUseFetchUserInfo.mockReturnValue({ data: userInfo, loading: false });
  mockUseTeams.mockImplementation(() => ({
    data: teams,
    error: null,
    loading: false,
    refetch: jest.fn(),
  }));
  mockUseTeamMembers.mockImplementation((teamId) => ({
    data: teamId ? members : [],
    error: null,
    loading: false,
    refetch: jest.fn(),
  }));
  mockUseCreateTeam.mockReturnValue({
    data: undefined,
    loading: false,
    createTeam: mockCreateTeam as never,
  });
  mockUseRenameTeam.mockReturnValue({
    data: undefined,
    loading: false,
    renameTeam: mockRenameTeam as never,
  });
  mockUseDeleteTeam.mockImplementation(() => ({
    data: undefined,
    loading: deletingTeam,
    deleteTeam: mockDeleteTeam as never,
  }));
  mockUseInviteTeamMember.mockReturnValue({
    data: undefined,
    loading: false,
    inviteTeamMember: mockInviteTeamMember as never,
  });
  mockUseRemoveTeamMember.mockImplementation(() => ({
    data: undefined,
    loading: removingMember,
    removeTeamMember: mockRemoveTeamMember as never,
  }));
  mockUseUpdateTeamInvitation.mockImplementation(() => ({
    data: undefined,
    loading: invitationLoading,
    updateInvitation: mockUpdateInvitation as never,
  }));
  mockUseLeaveTeam.mockImplementation(() => ({
    data: undefined,
    loading: leavingTeam,
    leaveTeam: mockLeaveTeam as never,
  }));
});

test('shows creation and governance controls only to a superuser managing an owned team', () => {
  const { rerender } = render(<UserSettingTeam />);

  expect(screen.getByRole('button', { name: '创建团队' })).toBeInTheDocument();
  expect(
    screen.getByRole('button', { name: '重命名团队 HR 团队' }),
  ).toBeInTheDocument();
  expect(
    screen.getByRole('button', { name: '删除团队 HR 团队' }),
  ).toBeInTheDocument();

  mockUseFetchUserInfo.mockReturnValue({
    data: { ...userInfo, is_superuser: false },
    loading: false,
  });
  rerender(<UserSettingTeam />);

  expect(
    screen.queryByRole('button', { name: '创建团队' }),
  ).not.toBeInTheDocument();
  expect(
    screen.queryByRole('button', { name: '重命名团队 HR 团队' }),
  ).not.toBeInTheDocument();
  expect(
    screen.queryByRole('button', { name: '删除团队 HR 团队' }),
  ).not.toBeInTheDocument();
});

test('does not show governance controls when an owned team is not manageable', () => {
  teams = [{ ...ownedTeam, can_manage: false }];
  render(<UserSettingTeam />);

  expect(screen.getByRole('button', { name: '创建团队' })).toBeInTheDocument();
  expect(
    screen.queryByRole('button', { name: '重命名团队 HR 团队' }),
  ).not.toBeInTheDocument();
  expect(
    screen.queryByRole('button', { name: '删除团队 HR 团队' }),
  ).not.toBeInTheDocument();
});

test('closes an open create dialog when the current user loses superuser authority', async () => {
  const { rerender } = render(<UserSettingTeam />);

  fireEvent.click(screen.getByRole('button', { name: '创建团队' }));
  expect(screen.getByRole('dialog', { name: '创建团队' })).toBeInTheDocument();

  mockUseFetchUserInfo.mockReturnValue({
    data: { ...userInfo, is_superuser: false },
    loading: false,
  });
  rerender(<UserSettingTeam />);

  await waitFor(() =>
    expect(
      screen.queryByRole('dialog', { name: '创建团队' }),
    ).not.toBeInTheDocument(),
  );
  expect(mockCreateTeam).not.toHaveBeenCalled();
});

test.each([
  {
    reason: '目标团队消失',
    revoke: () => {
      teams = [];
    },
  },
  {
    reason: '目标团队不再是 owner',
    revoke: () => {
      teams = [{ ...ownedTeam, membership_state: 'active' }];
    },
  },
  {
    reason: '目标团队 can_manage 变为 false',
    revoke: () => {
      teams = [{ ...ownedTeam, can_manage: false }];
    },
  },
  {
    reason: '当前用户丢失超级管理员权限',
    revoke: () => {
      mockUseFetchUserInfo.mockReturnValue({
        data: { ...userInfo, is_superuser: false },
        loading: false,
      });
    },
  },
])('closes an open rename dialog when $reason', async ({ revoke }) => {
  const { rerender } = render(<UserSettingTeam />);

  fireEvent.click(screen.getByRole('button', { name: '重命名团队 HR 团队' }));
  expect(
    screen.getByRole('dialog', { name: '重命名团队' }),
  ).toBeInTheDocument();

  revoke();
  rerender(<UserSettingTeam />);

  await waitFor(() =>
    expect(
      screen.queryByRole('dialog', { name: '重命名团队' }),
    ).not.toBeInTheDocument(),
  );
  expect(mockRenameTeam).not.toHaveBeenCalled();
});

test('loads members only after selecting a manageable owned team', async () => {
  render(<UserSettingTeam />);

  expect(mockUseTeamMembers).toHaveBeenLastCalledWith('');
  fireEvent.click(screen.getByRole('button', { name: 'HR 团队' }));

  await waitFor(() =>
    expect(mockUseTeamMembers).toHaveBeenLastCalledWith('team-hr'),
  );
  expect(screen.getByText('alice@example.com')).toBeInTheDocument();
});

test('accepts and rejects an invited team and leaves an active joined team as the current user', async () => {
  render(<UserSettingTeam />);

  fireEvent.click(screen.getByRole('button', { name: '接受 法务团队' }));
  await waitFor(() => expect(mockUpdateInvitation).toHaveBeenCalledTimes(1));
  fireEvent.click(screen.getByRole('button', { name: '拒绝 法务团队' }));
  fireEvent.click(screen.getByRole('button', { name: '退出团队 产品团队' }));

  expect(mockUpdateInvitation).toHaveBeenNthCalledWith(1, {
    teamId: 'team-invited',
    action: 'accept',
  });
  expect(mockUpdateInvitation).toHaveBeenNthCalledWith(2, {
    teamId: 'team-invited',
    action: 'reject',
  });
  expect(mockLeaveTeam).toHaveBeenCalledWith({
    teamId: 'team-active',
    userId: 'admin-1',
  });
  expect(
    screen.queryByRole('button', { name: '退出团队 HR 团队' }),
  ).not.toBeInTheDocument();
});

test('blocks conflicting invitation actions while an invitation request is pending', async () => {
  const request = createDeferred<MutationResponse>();
  mockUpdateInvitation.mockImplementationOnce(() => request.promise as never);
  const { rerender } = render(<UserSettingTeam />);

  const acceptButton = screen.getByRole('button', {
    name: '接受 法务团队',
  });
  const rejectButton = screen.getByRole('button', {
    name: '拒绝 法务团队',
  });
  fireEvent.click(acceptButton);
  fireEvent.click(rejectButton);

  expect(mockUpdateInvitation).toHaveBeenCalledTimes(1);
  invitationLoading = true;
  rerender(<UserSettingTeam />);
  expect(acceptButton).toBeDisabled();
  expect(rejectButton).toBeDisabled();
  fireEvent.click(rejectButton);
  expect(mockUpdateInvitation).toHaveBeenCalledTimes(1);

  await act(async () => {
    request.resolve(await successfulMutation());
    await request.promise;
  });
});

test('blocks duplicate leave requests while leaving a team', async () => {
  const request = createDeferred<MutationResponse>();
  mockLeaveTeam.mockImplementationOnce(() => request.promise as never);
  const { rerender } = render(<UserSettingTeam />);

  const leaveButton = screen.getByRole('button', {
    name: '退出团队 产品团队',
  });
  fireEvent.click(leaveButton);
  fireEvent.click(leaveButton);

  expect(mockLeaveTeam).toHaveBeenCalledTimes(1);
  leavingTeam = true;
  rerender(<UserSettingTeam />);
  expect(leaveButton).toBeDisabled();
  fireEvent.click(leaveButton);
  expect(mockLeaveTeam).toHaveBeenCalledTimes(1);

  await act(async () => {
    request.resolve(await successfulMutation());
    await request.promise;
  });
});

test('invites and removes members only for the selected manageable owned team', async () => {
  render(<UserSettingTeam />);
  fireEvent.click(screen.getByRole('button', { name: 'HR 团队' }));

  fireEvent.click(screen.getByRole('button', { name: '邀请成员' }));
  fireEvent.change(screen.getByPlaceholderText('邮箱'), {
    target: { value: 'new@example.com' },
  });
  fireEvent.click(screen.getByRole('button', { name: '确定' }));

  await waitFor(() =>
    expect(mockInviteTeamMember).toHaveBeenCalledWith({
      teamId: 'team-hr',
      email: 'new@example.com',
    }),
  );

  fireEvent.click(screen.getByRole('button', { name: '移除成员 Alice' }));
  fireEvent.click(screen.getByTestId('confirm-delete-dialog-confirm-btn'));

  expect(mockRemoveTeamMember).toHaveBeenCalledWith({
    teamId: 'team-hr',
    userId: 'member-1',
  });
  await waitFor(() =>
    expect(
      screen.queryByTestId('confirm-delete-dialog'),
    ).not.toBeInTheDocument(),
  );
});

test('warns that datasets remain before deleting and repairs selection after refresh', async () => {
  teams = [ownedTeam, financeTeam];
  const { rerender } = render(<UserSettingTeam />);
  fireEvent.click(screen.getByRole('button', { name: 'HR 团队' }));

  fireEvent.click(screen.getByRole('button', { name: '删除团队 HR 团队' }));
  expect(
    screen.getByText('知识库不会删除，将自动变为只有我'),
  ).toBeInTheDocument();
  fireEvent.click(screen.getByTestId('confirm-delete-dialog-confirm-btn'));
  expect(mockDeleteTeam).toHaveBeenCalledWith('team-hr');

  teams = [financeTeam];
  rerender(<UserSettingTeam />);

  await waitFor(() =>
    expect(mockUseTeamMembers).toHaveBeenLastCalledWith('team-finance'),
  );
});

test('keeps team deletion controlled until the request succeeds', async () => {
  const failedRequest = createDeferred<MutationResponse>();
  mockDeleteTeam.mockImplementationOnce(() => failedRequest.promise as never);
  const { rerender } = render(<UserSettingTeam />);
  fireEvent.click(screen.getByRole('button', { name: 'HR 团队' }));
  fireEvent.click(screen.getByRole('button', { name: '删除团队 HR 团队' }));
  fireEvent.click(screen.getByTestId('confirm-delete-dialog-confirm-btn'));

  deletingTeam = true;
  rerender(<UserSettingTeam />);
  expect(screen.getByTestId('confirm-delete-dialog')).toBeInTheDocument();
  const confirmButton = screen.getByTestId('confirm-delete-dialog-confirm-btn');
  expect(confirmButton).toBeDisabled();
  fireEvent.click(confirmButton);
  expect(mockDeleteTeam).toHaveBeenCalledTimes(1);

  await act(async () => {
    failedRequest.resolve(failedMutationResponse('删除团队失败'));
    await failedRequest.promise;
  });
  deletingTeam = false;
  rerender(<UserSettingTeam />);

  expect(mockMessageError).toHaveBeenCalledWith('删除团队失败');
  expect(screen.getByTestId('confirm-delete-dialog')).toBeInTheDocument();
  expect(mockUseTeamMembers).toHaveBeenLastCalledWith('team-hr');

  const successfulRequest = createDeferred<MutationResponse>();
  mockDeleteTeam.mockImplementationOnce(
    () => successfulRequest.promise as never,
  );
  fireEvent.click(screen.getByTestId('confirm-delete-dialog-confirm-btn'));
  await act(async () => {
    successfulRequest.resolve(await successfulMutation());
    await successfulRequest.promise;
  });

  await waitFor(() =>
    expect(
      screen.queryByTestId('confirm-delete-dialog'),
    ).not.toBeInTheDocument(),
  );
  expect(mockUseTeamMembers).toHaveBeenLastCalledWith('');
});

test('keeps member removal controlled across pending, business failure, and success', async () => {
  const failedRequest = createDeferred<MutationResponse>();
  mockRemoveTeamMember.mockImplementationOnce(
    () => failedRequest.promise as never,
  );
  const { rerender } = render(<UserSettingTeam />);
  fireEvent.click(screen.getByRole('button', { name: 'HR 团队' }));
  fireEvent.click(screen.getByRole('button', { name: '移除成员 Alice' }));
  fireEvent.click(screen.getByTestId('confirm-delete-dialog-confirm-btn'));

  removingMember = true;
  rerender(<UserSettingTeam />);
  expect(screen.getByTestId('confirm-delete-dialog')).toBeInTheDocument();
  const confirmButton = screen.getByTestId('confirm-delete-dialog-confirm-btn');
  expect(confirmButton).toBeDisabled();
  fireEvent.click(confirmButton);
  expect(mockRemoveTeamMember).toHaveBeenCalledTimes(1);

  await act(async () => {
    failedRequest.resolve(failedMutationResponse('移除成员失败'));
    await failedRequest.promise;
  });
  removingMember = false;
  rerender(<UserSettingTeam />);

  expect(mockMessageError).toHaveBeenCalledWith('移除成员失败');
  expect(screen.getByTestId('confirm-delete-dialog')).toBeInTheDocument();

  const successfulRequest = createDeferred<MutationResponse>();
  mockRemoveTeamMember.mockImplementationOnce(
    () => successfulRequest.promise as never,
  );
  fireEvent.click(screen.getByTestId('confirm-delete-dialog-confirm-btn'));
  await act(async () => {
    successfulRequest.resolve(await successfulMutation());
    await successfulRequest.promise;
  });

  await waitFor(() =>
    expect(
      screen.queryByTestId('confirm-delete-dialog'),
    ).not.toBeInTheDocument(),
  );
});

test('reports a member-removal network error and keeps the confirmation open', async () => {
  mockRemoveTeamMember.mockRejectedValueOnce(new Error('网络断开'));
  render(<UserSettingTeam />);
  fireEvent.click(screen.getByRole('button', { name: 'HR 团队' }));
  fireEvent.click(screen.getByRole('button', { name: '移除成员 Alice' }));
  fireEvent.click(screen.getByTestId('confirm-delete-dialog-confirm-btn'));

  await waitFor(() =>
    expect(mockMessageError).toHaveBeenCalledWith('网络断开'),
  );
  expect(screen.getByTestId('confirm-delete-dialog')).toBeInTheDocument();
});

test('resets the invitation email after a successful invite and reopen', async () => {
  render(<UserSettingTeam />);
  fireEvent.click(screen.getByRole('button', { name: 'HR 团队' }));
  fireEvent.click(screen.getByRole('button', { name: '邀请成员' }));
  fireEvent.change(screen.getByPlaceholderText('邮箱'), {
    target: { value: 'new@example.com' },
  });
  fireEvent.click(screen.getByRole('button', { name: '确定' }));

  await waitFor(() =>
    expect(
      screen.queryByRole('dialog', { name: '邀请成员' }),
    ).not.toBeInTheDocument(),
  );
  fireEvent.click(screen.getByRole('button', { name: '邀请成员' }));

  expect(screen.getByPlaceholderText('邮箱')).toHaveValue('');
});

test('keeps member querying disabled for an ordinary joined-team member', () => {
  teams = [activeTeam, invitedTeam];
  mockUseFetchUserInfo.mockReturnValue({
    data: { ...userInfo, is_superuser: false },
    loading: false,
  });

  render(<UserSettingTeam />);

  expect(mockUseTeamMembers).toHaveBeenLastCalledWith('');
  expect(screen.queryByText('alice@example.com')).not.toBeInTheDocument();
  expect(
    screen.queryByRole('button', { name: '邀请成员' }),
  ).not.toBeInTheDocument();
});

test('shows the team loading state', () => {
  mockUseTeams.mockReturnValue({
    data: [],
    error: null,
    loading: true,
    refetch: jest.fn(),
  });

  render(<UserSettingTeam />);

  expect(screen.getByText('正在加载团队……')).toBeInTheDocument();
});

test('shows each empty team group without assuming the API returned an array', () => {
  mockUseTeams.mockReturnValue({
    data: undefined as unknown as import('@/interfaces/database/user-setting').ITeam[],
    error: null,
    loading: false,
    refetch: jest.fn(),
  });

  render(<UserSettingTeam />);

  expect(screen.getAllByText('暂无数据')).toHaveLength(3);
});

test('shows the team business error', () => {
  mockUseTeams.mockReturnValue({
    data: undefined as unknown as import('@/interfaces/database/user-setting').ITeam[],
    error: new Error('加载团队失败'),
    loading: false,
    refetch: jest.fn(),
  });

  render(<UserSettingTeam />);

  expect(screen.getByRole('alert')).toHaveTextContent('加载团队失败');
});

test('shows the member loading state for the selected team', () => {
  mockUseTeamMembers.mockImplementation((teamId) => ({
    data: teamId ? members : [],
    error: null,
    loading: Boolean(teamId),
    refetch: jest.fn(),
  }));
  render(<UserSettingTeam />);

  fireEvent.click(screen.getByRole('button', { name: 'HR 团队' }));

  expect(screen.getByText('正在加载成员……')).toBeInTheDocument();
});

test('shows the member empty state for the selected team', () => {
  members = [];
  render(<UserSettingTeam />);

  fireEvent.click(screen.getByRole('button', { name: 'HR 团队' }));

  expect(screen.getByText('暂无数据')).toBeInTheDocument();
});

test('shows the member business error for the selected team', () => {
  mockUseTeamMembers.mockImplementation((teamId) => ({
    data: [],
    error: teamId ? new Error('加载成员失败') : null,
    loading: false,
    refetch: jest.fn(),
  }));
  render(<UserSettingTeam />);

  fireEvent.click(screen.getByRole('button', { name: 'HR 团队' }));

  expect(screen.getByRole('alert')).toHaveTextContent('加载成员失败');
});
