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
  manageableOwnedTeams: ITeam[];
  selectedTeam?: ITeam;
  selectedTeamId: string;
  onMemberInvited: () => void;
  onTeamDeleted: (nextTeamId: string) => void;
  onTeamSaved: () => void;
}

export const useTeamActions = ({
  currentUserId,
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
  const { deleteTeam } = useDeleteTeam();
  const { inviteTeamMember, loading: invitingMember } = useInviteTeamMember();
  const { removeTeamMember } = useRemoveTeamMember();
  const { updateInvitation } = useUpdateTeamInvitation();
  const { leaveTeam } = useLeaveTeam();

  const reportFailure = (
    response: { code?: number; message?: string } | undefined,
  ) => {
    if (response?.code !== 0) {
      message.error(response?.message || t('setting.teamOperationFailed'));
      return true;
    }
    return false;
  };

  const saveTeam = async (dialog: TeamDialogState, name: string) => {
    if (!dialog) return;
    const response =
      dialog.mode === 'create'
        ? await createTeam(name)
        : await renameTeam({ teamId: dialog.teamId, name });
    if (!reportFailure(response)) onTeamSaved();
    return response;
  };

  const removeTeam = async (team: ITeam) => {
    const response = await deleteTeam(team.id);
    if (reportFailure(response)) return response;

    if (selectedTeamId === team.id) {
      const nextTeam = manageableOwnedTeams.find(
        (candidate) => candidate.id !== team.id,
      );
      onTeamDeleted(nextTeam?.id ?? '');
    }
    return response;
  };

  const respondToInvitation = async (
    teamId: string,
    action: TeamInvitationAction,
  ) => {
    const response = await updateInvitation({ teamId, action });
    reportFailure(response);
    return response;
  };

  const leaveJoinedTeam = async (teamId: string) => {
    if (!currentUserId) return;
    const response = await leaveTeam({ teamId, userId: currentUserId });
    reportFailure(response);
    return response;
  };

  const inviteMember = async (email: string) => {
    if (!selectedTeam) return;
    const response = await inviteTeamMember({
      teamId: selectedTeam.id,
      email,
    });
    if (!reportFailure(response)) onMemberInvited();
    return response;
  };

  const removeMember = async (member: ITeamMember) => {
    if (!selectedTeam) return;
    const response = await removeTeamMember({
      teamId: selectedTeam.id,
      userId: member.id,
    });
    reportFailure(response);
    return response;
  };

  return {
    inviteMember,
    invitingMember,
    leaveJoinedTeam,
    removeMember,
    removeTeam,
    respondToInvitation,
    saveTeam,
    teamDialogLoading: creatingTeam || renamingTeam,
  };
};
