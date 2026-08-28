import { Input } from '@/components/ui/input';
import { Modal } from '@/components/ui/modal/modal';
import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';

interface TeamDialogProps {
  initialName?: string;
  loading: boolean;
  mode: 'create' | 'rename';
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSubmit: (name: string) => Promise<unknown>;
}

const TeamDialog = ({
  initialName = '',
  loading,
  mode,
  open,
  onOpenChange,
  onSubmit,
}: TeamDialogProps) => {
  const { t } = useTranslation();
  const [name, setName] = useState(initialName);

  useEffect(() => {
    if (open) setName(initialName);
  }, [initialName, open]);

  const title = t(
    mode === 'create' ? 'setting.createTeam' : 'setting.renameTeam',
  );

  return (
    <Modal
      title={title}
      open={open}
      onOpenChange={onOpenChange}
      onOk={() => onSubmit(name.trim())}
      confirmLoading={loading}
      disabled={!name.trim()}
      okText={t('common.ok')}
      cancelText={t('common.cancel')}
    >
      <label className="flex flex-col gap-2">
        <span className="text-sm text-text-primary">
          {t('setting.teamName')}
        </span>
        <Input
          autoFocus
          value={name}
          placeholder={t('setting.teamName')}
          onChange={(event) => setName(event.target.value)}
        />
      </label>
    </Modal>
  );
};

export default TeamDialog;
