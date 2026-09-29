import CopyToClipboard from '@/components/copy-to-clipboard';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { useTranslate } from '@/hooks/common-hooks';
import { useFetchKnowledgeList } from '@/hooks/use-knowledge-request';
import type { IToken } from '@/interfaces/database/chat';
import type {
  ICreateAPIKeyRequest,
  IUpdateAPIKeyFields,
} from '@/interfaces/request/system';
import { IModalProps } from '@/interfaces/common';
import { formatDate } from '@/utils/date';
import { Pencil, Power, Trash2 } from 'lucide-react';
import { useMemo, useState } from 'react';
import { useOperateApiKey } from '../hooks';
import ApiKeyForm, { APIKeyFormValues } from './api-key-form';

const isCurrentToken = (
  token: IToken,
): token is Extract<IToken, { name: string }> => 'name' in token;

const tokenName = (token: IToken, legacyName: string) =>
  isCurrentToken(token) ? token.name : legacyName;

const tokenType = (token: IToken) => token.key_type ?? 'full_access';

const tokenStatus = (token: IToken) => {
  if (token.enabled === false) return 'disabled';
  if (token.expires_at && new Date(token.expires_at).getTime() <= Date.now()) {
    return 'expired';
  }
  return 'active';
};

const ChatApiKeyModal = ({ hideModal }: IModalProps<any> & {
  dialogId?: string;
  idKey: string;
}) => {
  const {
    createToken,
    updateToken,
    removeToken,
    tokenList,
    listLoading,
    operationLoading,
    error,
    operationError,
  } = useOperateApiKey();
  const { list: knowledgeList } = useFetchKnowledgeList();
  const { t } = useTranslate('chat');
  const [formOpen, setFormOpen] = useState(false);
  const [editingToken, setEditingToken] = useState<IToken>();

  const datasetNames = useMemo(
    () => new Map(knowledgeList.map((dataset) => [dataset.id, dataset.name])),
    [knowledgeList],
  );

  const openCreateForm = () => {
    setEditingToken(undefined);
    setFormOpen(true);
  };

  const openEditForm = (token: IToken) => {
    setEditingToken(token);
    setFormOpen(true);
  };

  const submitForm = async (values: APIKeyFormValues) => {
    if (editingToken) {
      const updateValues: IUpdateAPIKeyFields = {
        name: values.name,
        enabled: values.enabled,
      };
      if (tokenType(editingToken) === 'retrieval') {
        updateValues.allowed_dataset_ids = values.allowed_dataset_ids;
        updateValues.expires_in_days = values.expires_in_days;
      }
      const succeeded = await updateToken(editingToken.token, updateValues);
      if (!succeeded) return;
    } else if (values.key_type) {
      const createValues: ICreateAPIKeyRequest = {
        name: values.name,
        key_type: values.key_type,
      };
      if (values.key_type === 'retrieval') {
        createValues.allowed_dataset_ids = values.allowed_dataset_ids;
        createValues.expires_in_days = values.expires_in_days;
      }
      const succeeded = await createToken(createValues);
      if (!succeeded) return;
    }
    setFormOpen(false);
    setEditingToken(undefined);
  };

  const renderScope = (token: IToken) => {
    if (!isCurrentToken(token) || tokenType(token) === 'full_access') {
      return '—';
    }
    return (
      <div className="space-y-1">
        <div className="text-xs text-text-secondary">
          {t('apiKeyDatasetCount', {
            count: token.allowed_dataset_ids.length,
          })}
        </div>
        <div>
          {token.allowed_dataset_ids
            .map((id) => datasetNames.get(id) ?? id)
            .join(', ')}
        </div>
      </div>
    );
  };

  const renderResult = (token: IToken) => {
    if (!isCurrentToken(token) || !token.last_result) return '—';
    const labels: Record<string, string> = {
      denied: t('apiKeyResultDenied'),
      error: t('apiKeyResultError'),
      rate_limited: t('apiKeyResultRateLimited'),
      success: t('apiKeyResultSuccess'),
    };
    return labels[token.last_result] ?? token.last_result;
  };

  return (
    <Dialog open onOpenChange={hideModal}>
      <DialogContent className="max-w-[90vw]">
        <DialogHeader>
          <DialogTitle>{t('apiKey')}</DialogTitle>
          <DialogDescription>{t('apiKeyDescription')}</DialogDescription>
        </DialogHeader>
        <div className="space-y-4 overflow-hidden">
          {error && (
            <p className="rounded-md bg-state-error/5 p-3 text-sm text-state-error">
              {operationError
                ? t('apiKeyOperationError')
                : t('apiKeyLoadError')}
            </p>
          )}
          {formOpen ? (
            <ApiKeyForm
              key={editingToken?.token ?? 'create'}
              token={editingToken}
              loading={operationLoading}
              onCancel={() => setFormOpen(false)}
              onSubmit={submitForm}
            />
          ) : (
            <>
              {operationLoading && (
                <p className="text-sm text-text-secondary">
                  {t('apiKeySaving')}
                </p>
              )}
              {listLoading ? (
                <div className="flex justify-center py-8">
                  {t('apiKeyLoading')}
                </div>
              ) : tokenList.length === 0 ? (
                <div className="py-8 text-center text-text-secondary">
                  {t('apiKeyEmpty')}
                </div>
              ) : (
                <div className="max-h-[60vh] overflow-auto">
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead>{t('apiKeyNameAndKey')}</TableHead>
                        <TableHead>{t('apiKeyType')}</TableHead>
                        <TableHead>{t('apiKeyScope')}</TableHead>
                        <TableHead>{t('created')}</TableHead>
                        <TableHead>{t('apiKeyExpiry')}</TableHead>
                        <TableHead>{t('apiKeyStatus')}</TableHead>
                        <TableHead>{t('apiKeyUsage')}</TableHead>
                        <TableHead>{t('apiKeyLastUsed')}</TableHead>
                        <TableHead>{t('apiKeyLastResult')}</TableHead>
                        <TableHead>{t('action')}</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {tokenList.map((token) => {
                        const name = tokenName(token, t('apiKeyLegacyName'));
                        const type = tokenType(token);
                        const status = tokenStatus(token);
                        const legacy =
                          token.key_type === undefined ||
                          (isCurrentToken(token) && token.legacy);
                        return (
                          <TableRow key={token.token}>
                            <TableCell className="min-w-48 align-top">
                              <div className="font-medium">{name}</div>
                              <div className="mt-1 flex items-center gap-1 break-all text-xs text-text-secondary">
                                <span>{token.token}</span>
                                <CopyToClipboard
                                  text={token.token}
                                  aria-label={t('apiKeyCopy', {
                                    token: token.token,
                                  })}
                                />
                              </div>
                            </TableCell>
                            <TableCell className="min-w-36 align-top">
                              <div className="flex flex-wrap gap-1">
                                <Badge>
                                  {type === 'retrieval'
                                    ? t('apiKeyRetrieval')
                                    : t('apiKeyFullAccess')}
                                </Badge>
                                {legacy && (
                                  <Badge variant="secondary">
                                    {t('apiKeyLegacy')}
                                  </Badge>
                                )}
                              </div>
                              {type === 'full_access' && (
                                <p className="mt-2 text-xs text-state-warning">
                                  {t('apiKeyFullAccessWarning')}
                                </p>
                              )}
                            </TableCell>
                            <TableCell className="max-w-56 align-top">
                              {renderScope(token)}
                            </TableCell>
                            <TableCell className="whitespace-nowrap align-top">
                              {formatDate(token.create_date) || '—'}
                            </TableCell>
                            <TableCell className="whitespace-nowrap align-top">
                              {type === 'full_access'
                                ? '—'
                                : token.expires_at
                                  ? formatDate(token.expires_at)
                                  : t('apiKeyNeverExpires')}
                            </TableCell>
                            <TableCell className="align-top">
                              <Badge
                                variant={
                                  status === 'active'
                                    ? 'success'
                                    : status === 'expired'
                                      ? 'destructive'
                                      : 'secondary'
                                }
                              >
                                {status === 'active'
                                  ? t('apiKeyActive')
                                  : status === 'expired'
                                    ? t('apiKeyExpired')
                                    : t('apiKeyDisabled')}
                              </Badge>
                            </TableCell>
                            <TableCell className="whitespace-nowrap align-top">
                              {isCurrentToken(token)
                                ? t('apiKeyUsageValue', {
                                    total: token.total_calls,
                                    retrieval: token.retrieval_calls,
                                  })
                                : '—'}
                            </TableCell>
                            <TableCell className="whitespace-nowrap align-top">
                              {isCurrentToken(token) && token.last_used_at
                                ? formatDate(token.last_used_at)
                                : '—'}
                            </TableCell>
                            <TableCell className="align-top">
                              {renderResult(token)}
                            </TableCell>
                            <TableCell className="align-top">
                              <div className="flex items-center gap-1">
                                <Button
                                  variant="ghost"
                                  size="icon"
                                  disabled={operationLoading}
                                  aria-label={t('apiKeyEdit', { name })}
                                  onClick={() => openEditForm(token)}
                                >
                                  <Pencil />
                                </Button>
                                <Button
                                  variant="ghost"
                                  size="icon"
                                  disabled={operationLoading}
                                  aria-label={
                                    token.enabled === false
                                      ? t('apiKeyEnable', { name })
                                      : t('apiKeyDisable', { name })
                                  }
                                  onClick={() =>
                                    updateToken(token.token, {
                                      enabled: token.enabled === false,
                                    })
                                  }
                                >
                                  <Power />
                                </Button>
                                <Button
                                  variant="ghost"
                                  size="icon"
                                  disabled={operationLoading}
                                  aria-label={t('apiKeyDelete', { name })}
                                  onClick={() => removeToken(token.token)}
                                >
                                  <Trash2 />
                                </Button>
                              </div>
                            </TableCell>
                          </TableRow>
                        );
                      })}
                    </TableBody>
                  </Table>
                </div>
              )}
              <Button
                onClick={openCreateForm}
                loading={operationLoading}
                disabled={operationLoading}
              >
                {t('createNewKey')}
              </Button>
            </>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
};

export default ChatApiKeyModal;
