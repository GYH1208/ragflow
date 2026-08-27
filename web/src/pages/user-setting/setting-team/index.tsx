import Spotlight from '@/components/spotlight';
import { Button } from '@/components/ui/button';
import { SearchInput } from '@/components/ui/input';
import { useTeamMembers, useTeams } from '@/hooks/use-team-request';
import { useFetchUserInfo } from '@/hooks/use-user-setting-request';
import { Plus, UserPlus } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { ProfileSettingWrapperCard } from '../components/user-setting-header';
import InviteMemberModal from './add-user-modal';
import { canManageTeam, TeamDialogState, useTeamActions } from './hooks';
import TeamDialog from './team-dialog';
import TeamList from './team-list';
import TeamMemberTable from './user-table';

const UserSettingTeam = () => {
  const { t } = useTranslation();
  const { data: userInfo } = useFetchUserInfo();
  const {
    data: teamData,
    error: teamError,
    loading: teamsLoading,
  } = useTeams();
  const [selectedTeamId, setSelectedTeamId] = useState('');
  const [teamDialog, setTeamDialog] = useState<TeamDialogState>(null);
  const [inviteDialogOpen, setInviteDialogOpen] = useState(false);
  const [teamSearch, setTeamSearch] = useState('');
  const [memberSearch, setMemberSearch] = useState('');

  const teams = useMemo(
    () => (Array.isArray(teamData) ? teamData : []),
    [teamData],
  );
  const manageableOwnedTeams = useMemo(
    () => teams.filter((team) => canManageTeam(userInfo, team)),
    [teams, userInfo],
  );
  const selectedTeam = teams.find(
    (team) => team.id === selectedTeamId && canManageTeam(userInfo, team),
  );
  const memberTeamId = selectedTeam?.id ?? '';
  const {
    data: memberData,
    error: memberError,
    loading: membersLoading,
  } = useTeamMembers(memberTeamId);
  const members = Array.isArray(memberData) ? memberData : [];

  useEffect(() => {
    if (selectedTeamId && !selectedTeam) {
      setSelectedTeamId(manageableOwnedTeams[0]?.id ?? '');
      setInviteDialogOpen(false);
    }
  }, [manageableOwnedTeams, selectedTeam, selectedTeamId]);

  useEffect(() => {
    if (!teamDialog) return;

    const authorized =
      teamDialog.mode === 'create'
        ? Boolean(userInfo?.is_superuser)
        : manageableOwnedTeams.some((team) => team.id === teamDialog.teamId);
    if (!authorized) setTeamDialog(null);
  }, [manageableOwnedTeams, teamDialog, userInfo?.is_superuser]);

  const teamActions = useTeamActions({
    currentUserId: userInfo?.id,
    isSuperuser: Boolean(userInfo?.is_superuser),
    manageableOwnedTeams,
    selectedTeam,
    selectedTeamId,
    onMemberInvited: () => setInviteDialogOpen(false),
    onTeamDeleted: (nextTeamId) => {
      setSelectedTeamId(nextTeamId);
      setInviteDialogOpen(false);
    },
    onTeamSaved: () => setTeamDialog(null),
  });

  const renamedTeam =
    teamDialog?.mode === 'rename'
      ? teams.find((team) => team.id === teamDialog.teamId)
      : undefined;

  return (
    <ProfileSettingWrapperCard
      header={
        <header className="flex items-center justify-between gap-4">
          <h2 className="text-2xl font-medium text-text-primary">
            {t('setting.teamManagement')}
          </h2>
          {userInfo?.is_superuser && (
            <Button onClick={() => setTeamDialog({ mode: 'create' })}>
              <Plus />
              {t('setting.createTeam')}
            </Button>
          )}
        </header>
      }
    >
      <Spotlight />

      <div className="grid h-full min-h-0 grid-cols-1 gap-4 overflow-y-auto p-4 lg:grid-cols-[minmax(280px,360px)_minmax(0,1fr)]">
        <aside className="space-y-4 rounded-lg border border-border-default bg-bg-card p-4">
          <SearchInput
            className="w-full bg-bg-input"
            placeholder={t('common.search')}
            value={teamSearch}
            onChange={(event) => setTeamSearch(event.target.value)}
          />
          {teamError ? (
            <div role="alert" className="p-6 text-center text-state-error">
              {teamError.message}
            </div>
          ) : (
            <TeamList
              teams={teams}
              isSuperuser={Boolean(userInfo?.is_superuser)}
              loading={teamsLoading}
              searchTerm={teamSearch}
              selectedTeamId={selectedTeamId}
              deleteLoading={teamActions.deletingTeam}
              invitationLoading={teamActions.invitationLoading}
              leaveLoading={teamActions.leavingTeam}
              onDelete={teamActions.removeTeam}
              onInvitation={teamActions.respondToInvitation}
              onLeave={teamActions.leaveJoinedTeam}
              onRename={(team) =>
                setTeamDialog({ mode: 'rename', teamId: team.id })
              }
              onSelect={setSelectedTeamId}
            />
          )}
        </aside>

        <section className="min-w-0 rounded-lg border border-border-default bg-bg-card p-4">
          {selectedTeam ? (
            <>
              <header className="mb-4 flex flex-wrap items-center justify-between gap-3">
                <div>
                  <h2 className="text-lg font-medium text-text-primary">
                    {selectedTeam.name}
                  </h2>
                  <p className="text-sm text-text-secondary">
                    {t('setting.teamMembers')}
                  </p>
                </div>
                <div className="flex items-center gap-3">
                  <SearchInput
                    className="w-40 bg-bg-input"
                    placeholder={t('common.search')}
                    value={memberSearch}
                    onChange={(event) => setMemberSearch(event.target.value)}
                  />
                  <Button onClick={() => setInviteDialogOpen(true)}>
                    <UserPlus />
                    {t('setting.invite')}
                  </Button>
                </div>
              </header>
              <TeamMemberTable
                error={memberError}
                loading={membersLoading}
                members={members}
                removeLoading={teamActions.removingMember}
                searchTerm={memberSearch}
                onRemove={teamActions.removeMember}
              />
            </>
          ) : (
            <div className="flex h-48 items-center justify-center text-text-secondary">
              {t('setting.selectTeam')}
            </div>
          )}
        </section>
      </div>

      <TeamDialog
        initialName={renamedTeam?.name}
        loading={teamActions.teamDialogLoading}
        mode={teamDialog?.mode ?? 'create'}
        open={Boolean(teamDialog)}
        onOpenChange={(open) => !open && setTeamDialog(null)}
        onSubmit={(name) => teamActions.saveTeam(teamDialog, name)}
      />

      {selectedTeam && (
        <InviteMemberModal
          visible={inviteDialogOpen}
          hideModal={() => setInviteDialogOpen(false)}
          loading={teamActions.invitingMember}
          onOk={teamActions.inviteMember}
        />
      )}
    </ProfileSettingWrapperCard>
  );
};

export default UserSettingTeam;
