import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { MultiSelect } from '@/components/ui/multi-select';
import { Switch } from '@/components/ui/switch';
import { useTranslate } from '@/hooks/common-hooks';
import { useFetchKnowledgeList } from '@/hooks/use-knowledge-request';
import type { APIKeyType, IToken } from '@/interfaces/database/chat';
import type { APIKeyExpiryDays } from '@/interfaces/request/system';
import { formatDate } from '@/utils/date';
import { type FormEvent, useMemo, useState } from 'react';

export type APIKeyFormValues = {
  name: string;
  key_type?: APIKeyType;
  allowed_dataset_ids?: string[];
  expires_in_days?: APIKeyExpiryDays | null;
  enabled: boolean;
};

type Props = {
  token?: IToken;
  loading?: boolean;
  onCancel?: () => void;
  onSubmit(values: APIKeyFormValues): void | Promise<void>;
};

type FormKeyType = APIKeyType | '';
type ExpiryValue = `${APIKeyExpiryDays}` | 'forever';
type ExpirySelection = ExpiryValue | '';

const expiryOptions: ExpiryValue[] = ['30', '90', '180', '365', 'forever'];

const ApiKeyForm = ({ token, loading, onCancel, onSubmit }: Props) => {
  const { t } = useTranslate('chat');
  const { list: knowledgeList, loading: knowledgeLoading } =
    useFetchKnowledgeList();
  const editing = Boolean(token);
  const initialType = token ? (token.key_type ?? 'full_access') : '';
  const [name, setName] = useState(token && 'name' in token ? token.name : '');
  const [keyType, setKeyType] = useState<FormKeyType>(initialType);
  const [datasetIds, setDatasetIds] = useState<string[]>(
    token && 'allowed_dataset_ids' in token
      ? token.allowed_dataset_ids
      : [],
  );
  const [expiry, setExpiry] = useState<ExpirySelection>(token ? '' : '90');
  const [enabled, setEnabled] = useState(token?.enabled !== false);
  const [errors, setErrors] = useState<{
    name?: string;
    keyType?: string;
    datasets?: string;
  }>({});

  const knowledgeOptions = useMemo(
    () =>
      knowledgeList.map((dataset) => ({
        label: dataset.name,
        value: dataset.id,
      })),
    [knowledgeList],
  );

  const handleTypeChange = (value: string) => {
    const nextType = value as FormKeyType;
    setKeyType(nextType);
    if (nextType === 'full_access') {
      setDatasetIds([]);
      setExpiry('90');
    }
  };

  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const trimmedName = name.trim();
    const nextErrors: typeof errors = {};
    if (!trimmedName) {
      nextErrors.name = t('apiKeyNameRequired');
    } else if (trimmedName.length > 64) {
      nextErrors.name = t('apiKeyNameTooLong');
    }
    if (!keyType) {
      nextErrors.keyType = t('apiKeyTypeRequired');
    }
    if (keyType === 'retrieval' && datasetIds.length === 0) {
      nextErrors.datasets = t('apiKeyDatasetRequired');
    }
    setErrors(nextErrors);
    if (Object.keys(nextErrors).length > 0 || !keyType) {
      return;
    }

    const values: APIKeyFormValues = { name: trimmedName, enabled };
    if (!editing) {
      values.key_type = keyType;
    }
    if (keyType === 'retrieval') {
      values.allowed_dataset_ids = datasetIds;
      if (!editing || expiry) {
        values.expires_in_days =
          expiry === 'forever'
            ? null
            : (Number(expiry) as APIKeyExpiryDays);
      }
    }
    void onSubmit(values);
  };

  return (
    <form className="space-y-4" onSubmit={handleSubmit}>
      <div className="space-y-2">
        <Label htmlFor="api-key-name">{t('apiKeyName')}</Label>
        <Input
          id="api-key-name"
          value={name}
          onChange={(event) => setName(event.target.value)}
        />
        {errors.name && <p className="text-sm text-state-error">{errors.name}</p>}
      </div>

      <div className="space-y-2">
        <Label htmlFor="api-key-type">{t('apiKeyType')}</Label>
        <select
          id="api-key-type"
          className="h-8 w-full rounded-md border border-border-button bg-bg-input px-3 text-sm"
          value={keyType}
          disabled={editing}
          onChange={(event) => handleTypeChange(event.target.value)}
        >
          <option value="">{t('apiKeySelectType')}</option>
          <option value="retrieval">{t('apiKeyRetrieval')}</option>
          <option value="full_access">{t('apiKeyFullAccess')}</option>
        </select>
        {errors.keyType && (
          <p className="text-sm text-state-error">{errors.keyType}</p>
        )}
      </div>

      {keyType === 'retrieval' && (
        <>
          <div className="space-y-2">
            <Label htmlFor="api-key-datasets">{t('apiKeyDatasets')}</Label>
            <MultiSelect
              id="api-key-datasets"
              aria-label={t('apiKeyDatasets')}
              options={knowledgeOptions}
              defaultValue={datasetIds}
              onValueChange={setDatasetIds}
              placeholder={t('apiKeySelectDatasets')}
              disabled={knowledgeLoading}
              maxCount={3}
              modalPopover
            />
            {errors.datasets && (
              <p className="text-sm text-state-error">{errors.datasets}</p>
            )}
          </div>
          <div className="space-y-2">
            <Label htmlFor="api-key-expiry">{t('apiKeyExpiry')}</Label>
            <select
              id="api-key-expiry"
              className="h-8 w-full rounded-md border border-border-button bg-bg-input px-3 text-sm"
              value={expiry}
              onChange={(event) =>
                setExpiry(event.target.value as ExpirySelection)
              }
            >
              {editing && (
                <option value="">
                  {t('apiKeyKeepCurrentExpiry', {
                    expiry:
                      token &&
                      'expires_at' in token &&
                      token.expires_at !== null
                        ? formatDate(token.expires_at)
                        : t('apiKeyNeverExpires'),
                  })}
                </option>
              )}
              {expiryOptions.map((value) => (
                <option key={value} value={value}>
                  {value === 'forever'
                    ? t('apiKeyNeverExpires')
                    : t('apiKeyExpiryDays', { days: value })}
                </option>
              ))}
            </select>
          </div>
        </>
      )}

      {keyType === 'full_access' && (
        <p className="rounded-md bg-state-warning/10 p-3 text-sm text-state-warning">
          {t('apiKeyFullAccessWarning')}
        </p>
      )}

      {editing && (
        <div className="flex items-center gap-2">
          <Switch
            id="api-key-enabled"
            checked={enabled}
            onCheckedChange={setEnabled}
          />
          <Label htmlFor="api-key-enabled">{t('apiKeyEnabled')}</Label>
        </div>
      )}

      <div className="flex justify-end gap-2">
        {onCancel && (
          <Button type="button" variant="outline" onClick={onCancel}>
            {t('apiKeyCancel')}
          </Button>
        )}
        <Button type="submit" loading={loading}>
          {editing ? t('apiKeySaveChanges') : t('apiKeyCreate')}
        </Button>
      </div>
    </form>
  );
};

export default ApiKeyForm;
