import { fireEvent, render, screen, waitFor } from '@testing-library/react';

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
  'setting.memberCount': '成员数',
  'setting.ownedTeams': '我的团队',
  'setting.rejectInvitation': '拒绝',
  'setting.removeMember': '移除成员',
  'setting.renameTeam': '重命名团队',
  'setting.role': '状态',
  'setting.selectTeam': '请选择团队',
  'setting.teamMembers': '团队成员',
  'setting.teamName': '团队名称',
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

const successfulMutation = (input?: unknown) => {
  void input;
  return Promise.resolve({ code: 0, data: true, message: '', status: 200 });
};
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

beforeEach(() => {
  jest.clearAllMocks();
  teams = [ownedTeam, activeTeam, invitedTeam];
  members = [member];

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
  mockUseDeleteTeam.mockReturnValue({
    data: undefined,
    loading: false,
    deleteTeam: mockDeleteTeam as never,
  });
  mockUseInviteTeamMember.mockReturnValue({
    data: undefined,
    loading: false,
    inviteTeamMember: mockInviteTeamMember as never,
  });
  mockUseRemoveTeamMember.mockReturnValue({
    data: undefined,
    loading: false,
    removeTeamMember: mockRemoveTeamMember as never,
  });
  mockUseUpdateTeamInvitation.mockReturnValue({
    data: undefined,
    loading: false,
    updateInvitation: mockUpdateInvitation as never,
  });
  mockUseLeaveTeam.mockReturnValue({
    data: undefined,
    loading: false,
    leaveTeam: mockLeaveTeam as never,
  });
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

test('loads members only after selecting a manageable owned team', async () => {
  render(<UserSettingTeam />);

  expect(mockUseTeamMembers).toHaveBeenLastCalledWith('');
  fireEvent.click(screen.getByRole('button', { name: 'HR 团队' }));

  await waitFor(() =>
    expect(mockUseTeamMembers).toHaveBeenLastCalledWith('team-hr'),
  );
  expect(screen.getByText('alice@example.com')).toBeInTheDocument();
});

test('accepts and rejects an invited team and leaves an active joined team as the current user', () => {
  render(<UserSettingTeam />);

  fireEvent.click(screen.getByRole('button', { name: '接受 法务团队' }));
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

test('renders loading, empty, and business error states without assuming arrays', () => {
  mockUseTeams.mockReturnValue({
    data: undefined as unknown as import('@/interfaces/database/user-setting').ITeam[],
    error: new Error('加载团队失败'),
    loading: false,
    refetch: jest.fn(),
  });

  render(<UserSettingTeam />);

  expect(screen.getByRole('alert')).toHaveTextContent('加载团队失败');
});
