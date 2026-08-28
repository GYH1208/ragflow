import { ConfirmDeleteDialog } from '@/components/confirm-delete-dialog';
import { Button } from '@/components/ui/button';
import type {
  ITeam,
  TeamInvitationAction,
} from '@/interfaces/database/user-setting';
import { cn } from '@/lib/utils';
import { Pencil, Trash2 } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { canManageTeam, groupTeams } from './hooks';

interface TeamListProps {
  teams: ITeam[];
  isSuperuser: boolean;
  loading: boolean;
  searchTerm: string;
  selectedTeamId: string;
  deleteLoading: boolean;
  invitationLoading: boolean;
  leaveLoading: boolean;
  onDelete: (team: ITeam) => Promise<boolean>;
  onInvitation: (
    teamId: string,
    action: TeamInvitationAction,
  ) => Promise<boolean>;
  onLeave: (teamId: string) => Promise<boolean>;
  onRename: (team: ITeam) => void;
  onSelect: (teamId: string) => void;
}

const TeamList = ({
  teams,
  isSuperuser,
  loading,
  searchTerm,
  selectedTeamId,
  deleteLoading,
  invitationLoading,
  leaveLoading,
  onDelete,
  onInvitation,
  onLeave,
  onRename,
  onSelect,
}: TeamListProps) => {
  const { t } = useTranslation();
  const [deletingTeamId, setDeletingTeamId] = useState<string | null>(null);
  const filteredTeams = useMemo(() => {
    const normalizedSearch = searchTerm.trim().toLocaleLowerCase();
    if (!normalizedSearch) return teams;
    return teams.filter((team) =>
      team.name.toLocaleLowerCase().includes(normalizedSearch),
    );
  }, [searchTerm, teams]);
  const groupedTeams = groupTeams(filteredTeams);
  const userInfo = { is_superuser: isSuperuser };

  useEffect(() => {
    if (
      deletingTeamId &&
      !teams.some(
        (team) =>
          team.id === deletingTeamId &&
          canManageTeam({ is_superuser: isSuperuser }, team),
      )
    ) {
      setDeletingTeamId(null);
    }
  }, [deletingTeamId, isSuperuser, teams]);

  const renderTeam = (team: ITeam) => {
    const manageable = canManageTeam(userInfo, team);

    return (
      <li
        key={team.id}
        className={cn(
          'rounded-lg border border-border-default bg-bg-base p-3',
          selectedTeamId === team.id && 'border-accent-primary bg-bg-card',
        )}
      >
        <div className="flex items-start justify-between gap-2">
          {manageable ? (
            <button
              type="button"
              aria-label={team.name}
              className="min-w-0 flex-1 text-left"
              onClick={() => onSelect(team.id)}
            >
              <span className="block truncate font-medium text-text-primary">
                {team.name}
              </span>
              <span className="mt-1 block text-xs text-text-secondary">
                {t('setting.memberCount')}: {team.member_count} ·{' '}
                {t('setting.datasetCount')}: {team.dataset_count}
              </span>
            </button>
          ) : (
            <div className="min-w-0 flex-1">
              <span className="block truncate font-medium text-text-primary">
                {team.name}
              </span>
              <span className="mt-1 block text-xs text-text-secondary">
                {t('setting.memberCount')}: {team.member_count} ·{' '}
                {t('setting.datasetCount')}: {team.dataset_count}
              </span>
            </div>
          )}

          {manageable && (
            <div className="flex shrink-0 gap-1">
              <Button
                aria-label={`${t('setting.renameTeam')} ${team.name}`}
                size="icon-sm"
                variant="ghost"
                onClick={() => onRename(team)}
              >
                <Pencil />
              </Button>
              <ConfirmDeleteDialog
                confirmLoading={deleteLoading}
                title={t('setting.deleteTeam')}
                manualClose
                open={deletingTeamId === team.id}
                content={{
                  title: t('setting.deleteTeamWarning'),
                  node: <span>{team.name}</span>,
                }}
                onOpenChange={(open) => {
                  if (!deleteLoading) {
                    setDeletingTeamId(open ? team.id : null);
                  }
                }}
                onOk={async () => {
                  if (await onDelete(team)) setDeletingTeamId(null);
                }}
              >
                <Button
                  aria-label={`${t('setting.deleteTeam')} ${team.name}`}
                  disabled={deleteLoading}
                  size="icon-sm"
                  variant="delete"
                >
                  <Trash2 />
                </Button>
              </ConfirmDeleteDialog>
            </div>
          )}
        </div>

        {team.membership_state === 'invited' && (
          <div className="mt-3 flex gap-2">
            <Button
              aria-label={`${t('setting.acceptInvitation')} ${team.name}`}
              disabled={invitationLoading}
              size="sm"
              onClick={() => onInvitation(team.id, 'accept')}
            >
              {t('setting.acceptInvitation')}
            </Button>
            <Button
              aria-label={`${t('setting.rejectInvitation')} ${team.name}`}
              disabled={invitationLoading}
              size="sm"
              variant="outline"
              onClick={() => onInvitation(team.id, 'reject')}
            >
              {t('setting.rejectInvitation')}
            </Button>
          </div>
        )}

        {team.membership_state === 'active' && (
          <Button
            aria-label={`${t('setting.leaveTeam')} ${team.name}`}
            className="mt-3"
            disabled={leaveLoading}
            size="sm"
            variant="outline"
            onClick={() => onLeave(team.id)}
          >
            {t('setting.leaveTeam')}
          </Button>
        )}
      </li>
    );
  };

  const renderGroup = (title: string, group: ITeam[]) => (
    <section>
      <h3 className="mb-2 text-sm font-medium text-text-secondary">{title}</h3>
      {group.length > 0 ? (
        <ul className="space-y-2">{group.map(renderTeam)}</ul>
      ) : (
        <p className="rounded-lg border border-dashed border-border-default p-3 text-sm text-text-secondary">
          {t('common.noData')}
        </p>
      )}
    </section>
  );

  if (loading) {
    return (
      <div className="p-6 text-center text-sm text-text-secondary">
        {t('setting.loadingTeams')}
      </div>
    );
  }

  return (
    <div className="space-y-5">
      {renderGroup(t('setting.ownedTeams'), groupedTeams.owned)}
      <section className="space-y-4">
        <h2 className="text-base font-medium text-text-primary">
          {t('setting.joinedTeams')}
        </h2>
        {renderGroup(t('setting.activeTeams'), groupedTeams.active)}
        {renderGroup(t('setting.invitedTeams'), groupedTeams.invited)}
      </section>
    </div>
  );
};

export default TeamList;
