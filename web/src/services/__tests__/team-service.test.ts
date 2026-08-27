import request from '@/utils/request';

import {
  createTeam,
  deleteTeam,
  inviteTeamMember,
  listTeamMembers,
  listTeams,
  removeTeamMember,
  renameTeam,
  updateTeamInvitation,
} from '../team-service';

jest.mock('@/utils/request', () => ({
  __esModule: true,
  default: {
    delete: jest.fn(),
    get: jest.fn(),
    patch: jest.fn(),
    post: jest.fn(),
  },
}));

const mockDelete = jest.mocked(request.delete);
const mockGet = jest.mocked(request.get);
const mockPatch = jest.mocked(request.patch);
const mockPost = jest.mocked(request.post);

beforeEach(() => {
  jest.clearAllMocks();
});

test('lists teams with the teams GET endpoint', () => {
  listTeams();

  expect(mockGet).toHaveBeenCalledWith('/api/v1/teams');
});

test('creates a team with the teams POST endpoint and name body', () => {
  createTeam('HR 团队');

  expect(mockPost).toHaveBeenCalledWith('/api/v1/teams', {
    data: { name: 'HR 团队' },
  });
});

test('renames a team with its PATCH endpoint and name body', () => {
  renameTeam('team-1', '财务团队');

  expect(mockPatch).toHaveBeenCalledWith('/api/v1/teams/team-1', {
    data: { name: '财务团队' },
  });
});

test('deletes a team with its DELETE endpoint', () => {
  deleteTeam('team-1');

  expect(mockDelete).toHaveBeenCalledWith('/api/v1/teams/team-1');
});

test('lists members with the team members GET endpoint', () => {
  listTeamMembers('team-1');

  expect(mockGet).toHaveBeenCalledWith('/api/v1/teams/team-1/members');
});

test('invites a member with the team members POST endpoint and email body', () => {
  inviteTeamMember('team-1', 'member@example.com');

  expect(mockPost).toHaveBeenCalledWith('/api/v1/teams/team-1/members', {
    data: { email: 'member@example.com' },
  });
});

test('removes a member with the team and user DELETE endpoint', () => {
  removeTeamMember('team-1', 'user-2');

  expect(mockDelete).toHaveBeenCalledWith(
    '/api/v1/teams/team-1/members/user-2',
  );
});

test.each(['accept', 'reject'] as const)(
  'updates an invitation with the team PATCH endpoint and %s body',
  (action) => {
    updateTeamInvitation('team-1', action);

    expect(mockPatch).toHaveBeenCalledWith('/api/v1/teams/team-1/invitation', {
      data: { action },
    });
  },
);
