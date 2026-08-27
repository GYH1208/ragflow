import message from '@/components/ui/message';
import {
  useCreateTeam,
  useDeleteTeam,
  useInviteTeamMember,
  useLeaveTeam,
  useRemoveTeamMember,
  useRenameTeam,
  useUpdateTeamInvitation,
} from '@/hooks/use-team-request';
import type {
  ITeam,
  ITeamMember,
  IUserInfo,
  TeamInvitationAction,
} from '@/interfaces/database/user-setting';
import { useRef } from 'react';
import { useTranslation } from 'react-i18next';

export type TeamDialogState =
  | { mode: 'create' }
  | { mode: 'rename'; teamId: string }
  | null;

export const canManageTeam = (
  userInfo: Partial<IUserInfo> | undefined,
  team: ITeam | undefined,
) =>
  Boolean(
    userInfo?.is_superuser &&
    team?.membership_state === 'owner' &&
    team.can_manage,
  );

export const groupTeams = (teams: ITeam[]) => ({
  owned: teams.filter((team) => team.membership_state === 'owner'),
  active: teams.filter((team) => team.membership_state === 'active'),
  invited: teams.filter((team) => team.membership_state === 'invited'),
});

interface UseTeamActionsOptions {
  currentUserId?: string;
  isSuperuser: boolean;
  manageableOwnedTeams: ITeam[];
  selectedTeam?: ITeam;
  selectedTeamId: string;
  onMemberInvited: () => void;
  onTeamDeleted: (nextTeamId: string) => void;
  onTeamSaved: () => void;
}

export const useTeamActions = ({
  currentUserId,
  isSuperuser,
  manageableOwnedTeams,
  selectedTeam,
  selectedTeamId,
  onMemberInvited,
  onTeamDeleted,
  onTeamSaved,
}: UseTeamActionsOptions) => {
  const { t } = useTranslation();
  const { createTeam, loading: creatingTeam } = useCreateTeam();
  const { renameTeam, loading: renamingTeam } = useRenameTeam();
  const { deleteTeam, loading: deletingTeam } = useDeleteTeam();
  const { inviteTeamMember, loading: invitingMember } = useInviteTeamMember();
  const { removeTeamMember, loading: removingMember } = useRemoveTeamMember();
  const { loading: invitationLoading, updateInvitation } =
    useUpdateTeamInvitation();
  const { leaveTeam, loading: leavingTeam } = useLeaveTeam();
  const savingRef = useRef(false);
  const deletingRef = useRef(false);
  const invitingRef = useRef(false);
  const removingRef = useRef(false);
  const invitationRef = useRef(false);
  const leavingRef = useRef(false);

  const reportFailure = (
    response: { code?: number; message?: string } | undefined,
  ) => {
    if (response?.code !== 0) {
      message.error(response?.message || t('setting.teamOperationFailed'));
      return true;
    }
    return false;
  };

  const reportRequestError = (error: unknown) => {
    message.error(
      error instanceof Error && error.message
        ? error.message
        : t('setting.teamOperationFailed'),
    );
  };

  async function runRequest<T>(
    pendingRef: { current: boolean },
    request: () => Promise<T>,
  ): Promise<T | undefined> {
    if (pendingRef.current) return undefined;

    pendingRef.current = true;
    try {
      return await request();
    } catch (error) {
      reportRequestError(error);
      return undefined;
    } finally {
      pendingRef.current = false;
    }
  }

  const saveTeam = async (dialog: TeamDialogState, name: string) => {
    if (!dialog || creatingTeam || renamingTeam) return false;
    if (dialog.mode === 'create' && !isSuperuser) return false;
    if (
      dialog.mode === 'rename' &&
      !manageableOwnedTeams.some((team) => team.id === dialog.teamId)
    ) {
      return false;
    }

    const response = await runRequest(savingRef, () =>
      dialog.mode === 'create'
        ? createTeam(name)
        : renameTeam({ teamId: dialog.teamId, name }),
    );
    if (!response || reportFailure(response)) return false;

    onTeamSaved();
    return true;
  };

  const removeTeam = async (team: ITeam) => {
    if (
      deletingTeam ||
      !manageableOwnedTeams.some((candidate) => candidate.id === team.id)
    ) {
      return false;
    }
    const response = await runRequest(deletingRef, () => deleteTeam(team.id));
    if (!response || reportFailure(response)) return false;

    if (selectedTeamId === team.id) {
      const nextTeam = manageableOwnedTeams.find(
        (candidate) => candidate.id !== team.id,
      );
      onTeamDeleted(nextTeam?.id ?? '');
    }
    return true;
  };

  const respondToInvitation = async (
    teamId: string,
    action: TeamInvitationAction,
  ) => {
    if (invitationLoading) return false;
    const response = await runRequest(invitationRef, () =>
      updateInvitation({ teamId, action }),
    );
    if (!response || reportFailure(response)) return false;
    return true;
  };

  const leaveJoinedTeam = async (teamId: string) => {
    if (!currentUserId || leavingTeam) return false;
    const response = await runRequest(leavingRef, () =>
      leaveTeam({ teamId, userId: currentUserId }),
    );
    if (!response || reportFailure(response)) return false;
    return true;
  };

  const inviteMember = async (email: string) => {
    if (!selectedTeam || invitingMember) return false;
    const response = await runRequest(invitingRef, () =>
      inviteTeamMember({
        teamId: selectedTeam.id,
        email,
      }),
    );
    if (!response || reportFailure(response)) return false;

    onMemberInvited();
    return true;
  };

  const removeMember = async (member: ITeamMember) => {
    if (!selectedTeam || removingMember) return false;
    const response = await runRequest(removingRef, () =>
      removeTeamMember({
        teamId: selectedTeam.id,
        userId: member.id,
      }),
    );
    if (!response || reportFailure(response)) return false;
    return true;
  };

  return {
    inviteMember,
    invitingMember,
    invitationLoading,
    leaveJoinedTeam,
    leavingTeam,
    removeMember,
    removingMember,
    removeTeam,
    deletingTeam,
    respondToInvitation,
    saveTeam,
    teamDialogLoading: creatingTeam || renamingTeam,
  };
};
