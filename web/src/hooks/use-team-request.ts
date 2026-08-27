import {
  ITeamListResponse,
  ITeamMember,
  TeamInvitationAction,
} from '@/interfaces/database/user-setting';
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
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

export const teamKeys = {
  all: ['teams'] as const,
  members: (teamId: string) => ['teams', teamId, 'members'] as const,
};

export const enum TeamApiAction {
  CreateTeam = 'createTeam',
  RenameTeam = 'renameTeam',
  DeleteTeam = 'deleteTeam',
  InviteTeamMember = 'inviteTeamMember',
  RemoveTeamMember = 'removeTeamMember',
  UpdateTeamInvitation = 'updateTeamInvitation',
  LeaveTeam = 'leaveTeam',
}

export const useTeams = () => {
  const {
    data,
    error,
    isFetching: loading,
    refetch,
  } = useQuery<ITeamListResponse, Error>({
    queryKey: teamKeys.all,
    initialData: [],
    gcTime: 0,
    queryFn: async () => {
      const { data: response } = await listTeams();
      if (response.code !== 0) {
        throw new Error(response.message || 'Failed to fetch teams');
      }
      return response.data ?? [];
    },
  });

  return { data, error, loading, refetch };
};

export const useTeamMembers = (teamId: string) => {
  const {
    data,
    error,
    isFetching: loading,
    refetch,
  } = useQuery<ITeamMember[], Error>({
    queryKey: teamKeys.members(teamId),
    initialData: [],
    gcTime: 0,
    enabled: !!teamId,
    queryFn: async () => {
      const { data: response } = await listTeamMembers(teamId);
      if (response.code !== 0) {
        throw new Error(response.message || 'Failed to fetch team members');
      }
      return response.data ?? [];
    },
  });

  return { data, error, loading, refetch };
};

export const useCreateTeam = () => {
  const queryClient = useQueryClient();
  const {
    data,
    isPending: loading,
    mutateAsync,
  } = useMutation({
    mutationKey: [TeamApiAction.CreateTeam],
    mutationFn: async (name: string) => {
      const { data } = await createTeam(name);
      if (data.code === 0) {
        await queryClient.invalidateQueries({ queryKey: teamKeys.all });
      }
      return data;
    },
  });

  return { data, loading, createTeam: mutateAsync };
};

export const useRenameTeam = () => {
  const queryClient = useQueryClient();
  const {
    data,
    isPending: loading,
    mutateAsync,
  } = useMutation({
    mutationKey: [TeamApiAction.RenameTeam],
    mutationFn: async ({ teamId, name }: { teamId: string; name: string }) => {
      const { data } = await renameTeam(teamId, name);
      if (data.code === 0) {
        await queryClient.invalidateQueries({ queryKey: teamKeys.all });
      }
      return data;
    },
  });

  return { data, loading, renameTeam: mutateAsync };
};

export const useDeleteTeam = () => {
  const queryClient = useQueryClient();
  const {
    data,
    isPending: loading,
    mutateAsync,
  } = useMutation({
    mutationKey: [TeamApiAction.DeleteTeam],
    mutationFn: async (teamId: string) => {
      const { data } = await deleteTeam(teamId);
      if (data.code === 0) {
        await queryClient.invalidateQueries({ queryKey: teamKeys.all });
      }
      return data;
    },
  });

  return { data, loading, deleteTeam: mutateAsync };
};

export const useInviteTeamMember = () => {
  const queryClient = useQueryClient();
  const {
    data,
    isPending: loading,
    mutateAsync,
  } = useMutation({
    mutationKey: [TeamApiAction.InviteTeamMember],
    mutationFn: async ({
      teamId,
      email,
    }: {
      teamId: string;
      email: string;
    }) => {
      const { data } = await inviteTeamMember(teamId, email);
      if (data.code === 0) {
        await Promise.all([
          queryClient.invalidateQueries({ queryKey: teamKeys.all }),
          queryClient.invalidateQueries({
            queryKey: teamKeys.members(teamId),
          }),
        ]);
      }
      return data;
    },
  });

  return { data, loading, inviteTeamMember: mutateAsync };
};

export const useRemoveTeamMember = () => {
  const queryClient = useQueryClient();
  const {
    data,
    isPending: loading,
    mutateAsync,
  } = useMutation({
    mutationKey: [TeamApiAction.RemoveTeamMember],
    mutationFn: async ({
      teamId,
      userId,
    }: {
      teamId: string;
      userId: string;
    }) => {
      const { data } = await removeTeamMember(teamId, userId);
      if (data.code === 0) {
        await Promise.all([
          queryClient.invalidateQueries({ queryKey: teamKeys.all }),
          queryClient.invalidateQueries({
            queryKey: teamKeys.members(teamId),
          }),
        ]);
      }
      return data;
    },
  });

  return { data, loading, removeTeamMember: mutateAsync };
};

export const useUpdateTeamInvitation = () => {
  const queryClient = useQueryClient();
  const {
    data,
    isPending: loading,
    mutateAsync,
  } = useMutation({
    mutationKey: [TeamApiAction.UpdateTeamInvitation],
    mutationFn: async ({
      teamId,
      action,
    }: {
      teamId: string;
      action: TeamInvitationAction;
    }) => {
      const { data } = await updateTeamInvitation(teamId, action);
      if (data.code === 0) {
        await queryClient.invalidateQueries({ queryKey: teamKeys.all });
      }
      return data;
    },
  });

  return { data, loading, updateInvitation: mutateAsync };
};

export const useLeaveTeam = () => {
  const queryClient = useQueryClient();
  const {
    data,
    isPending: loading,
    mutateAsync,
  } = useMutation({
    mutationKey: [TeamApiAction.LeaveTeam],
    mutationFn: async ({
      teamId,
      userId,
    }: {
      teamId: string;
      userId: string;
    }) => {
      const { data } = await removeTeamMember(teamId, userId);
      if (data.code === 0) {
        await queryClient.invalidateQueries({ queryKey: teamKeys.all });
      }
      return data;
    },
  });

  return { data, loading, leaveTeam: mutateAsync };
};
