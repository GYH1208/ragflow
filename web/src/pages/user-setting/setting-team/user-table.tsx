import {
  ConfirmDeleteDialog,
  ConfirmDeleteDialogNode,
} from '@/components/confirm-delete-dialog';
import { RAGFlowAvatar } from '@/components/ragflow-avatar';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import type { ITeamMember } from '@/interfaces/database/user-setting';
import { Trash2 } from 'lucide-react';
import { useMemo } from 'react';
import { useTranslation } from 'react-i18next';

interface TeamMemberTableProps {
  error?: Error | null;
  loading: boolean;
  members: ITeamMember[];
  searchTerm: string;
  onRemove: (member: ITeamMember) => Promise<unknown>;
}

const TeamMemberTable = ({
  error,
  loading,
  members,
  searchTerm,
  onRemove,
}: TeamMemberTableProps) => {
  const { t } = useTranslation();
  const filteredMembers = useMemo(() => {
    const normalizedSearch = searchTerm.trim().toLocaleLowerCase();
    if (!normalizedSearch) return members;

    return members.filter(
      (member) =>
        member.nickname.toLocaleLowerCase().includes(normalizedSearch) ||
        member.email.toLocaleLowerCase().includes(normalizedSearch),
    );
  }, [members, searchTerm]);

  if (error) {
    return (
      <div role="alert" className="p-6 text-center text-state-error">
        {error.message}
      </div>
    );
  }

  return (
    <div className="overflow-hidden rounded-lg border border-border-default bg-bg-input">
      <Table rootClassName="rounded-lg">
        <TableHeader className="bg-bg-title">
          <TableRow className="hover:bg-bg-title">
            <TableHead>{t('common.name')}</TableHead>
            <TableHead>{t('setting.email')}</TableHead>
            <TableHead>{t('setting.role')}</TableHead>
            <TableHead>{t('common.action')}</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody className="bg-bg-base">
          {loading ? (
            <TableRow>
              <TableCell colSpan={4} className="h-24 text-center">
                {t('setting.loadingMembers')}
              </TableCell>
            </TableRow>
          ) : filteredMembers.length > 0 ? (
            filteredMembers.map((member) => (
              <TableRow key={member.id}>
                <TableCell>
                  <div className="flex items-center gap-2">
                    <RAGFlowAvatar
                      isPerson
                      className="size-6"
                      avatar={member.avatar ?? undefined}
                      name={member.nickname}
                    />
                    <span>{member.nickname}</span>
                  </div>
                </TableCell>
                <TableCell>{member.email}</TableCell>
                <TableCell>
                  <Badge variant="outline">
                    {t(
                      member.state === 'invited'
                        ? 'setting.memberInvited'
                        : 'setting.memberActive',
                    )}
                  </Badge>
                </TableCell>
                <TableCell>
                  <ConfirmDeleteDialog
                    title={t('setting.removeMember')}
                    content={{
                      title: t('setting.confirmRemoveMember'),
                      node: (
                        <ConfirmDeleteDialogNode
                          avatar={{
                            avatar: member.avatar ?? undefined,
                            name: member.nickname,
                            isPerson: true,
                          }}
                          name={member.email}
                        />
                      ),
                    }}
                    okButtonText={t('setting.removeMember')}
                    onOk={() => onRemove(member)}
                  >
                    <Button
                      aria-label={`${t('setting.removeMember')} ${member.nickname}`}
                      size="icon-sm"
                      variant="delete"
                    >
                      <Trash2 />
                    </Button>
                  </ConfirmDeleteDialog>
                </TableCell>
              </TableRow>
            ))
          ) : (
            <TableRow>
              <TableCell colSpan={4} className="h-24 text-center">
                {t('common.noData')}
              </TableCell>
            </TableRow>
          )}
        </TableBody>
      </Table>
    </div>
  );
};

export default TeamMemberTable;
