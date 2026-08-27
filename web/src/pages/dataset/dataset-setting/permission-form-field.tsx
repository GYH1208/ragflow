import { SelectWithSearch } from '@/components/originui/select-with-search';
import { RAGFlowFormItem } from '@/components/ragflow-form';
import { PermissionRole } from '@/constants/permission';
import { useTeams } from '@/hooks/use-team-request';
import { useFetchUserInfo } from '@/hooks/use-user-setting-request';
import { useMemo } from 'react';
import { useFormContext, useWatch } from 'react-hook-form';
import { useTranslation } from 'react-i18next';

export function PermissionFormField() {
  const { t } = useTranslation();
  const form = useFormContext();
  const { data: userInfo } = useFetchUserInfo();
  const { data: teamData } = useTeams();
  const permission = useWatch({ control: form.control, name: 'permission' });
  const isSuperuser = Boolean(userInfo?.is_superuser);
  const permissionOptions = useMemo(() => {
    return Object.values(PermissionRole).map((x) => ({
      label: t('knowledgeConfiguration.' + x),
      value: x,
    }));
  }, [t]);
  const teamOptions = useMemo(
    () =>
      (teamData ?? [])
        .filter(
          (team) =>
            team.membership_state === 'owner' && team.can_manage === true,
        )
        .map((team) => ({ label: team.name, value: team.id })),
    [teamData],
  );

  if (!isSuperuser) return null;

  return (
    <>
      <RAGFlowFormItem
        name="permission"
        label={t('knowledgeConfiguration.permissions')}
        tooltip={t('knowledgeConfiguration.permissionsTip')}
        horizontal
      >
        {(field) => (
          <SelectWithSearch
            {...field}
            options={permissionOptions}
            triggerClassName="w-full"
            testId="ds-settings-basic-permissions-select"
            onChange={(value) => {
              field.onChange(value);
              if (value === PermissionRole.Me) {
                form.setValue('team_id', null, {
                  shouldDirty: true,
                  shouldValidate: true,
                });
              }
            }}
          ></SelectWithSearch>
        )}
      </RAGFlowFormItem>
      {permission === PermissionRole.Team && (
        <RAGFlowFormItem
          name="team_id"
          label={t('setting.specifiedTeam')}
          horizontal
        >
          <SelectWithSearch
            options={teamOptions}
            placeholder={t('knowledgeConfiguration.teamPlaceholder')}
            triggerClassName="w-full"
            testId="ds-settings-basic-team-select"
          ></SelectWithSearch>
        </RAGFlowFormItem>
      )}
    </>
  );
}
