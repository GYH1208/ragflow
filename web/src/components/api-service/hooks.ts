import {
  useSetModalState,
  useShowDeleteConfirm,
  useTranslate,
} from '@/hooks/common-hooks';
import {
  useCreateSystemToken,
  useFetchManualSystemTokenList,
  useFetchSystemTokenList,
  useRemoveSystemToken,
  useUpdateSystemToken,
} from '@/hooks/use-user-setting-request';
import { IStats } from '@/interfaces/database/chat';
import type {
  ICreateAPIKeyRequest,
  IUpdateAPIKeyFields,
} from '@/interfaces/request/system';
import { selectFullAccessToken } from '@/utils/api-key';
import { useQueryClient } from '@tanstack/react-query';
import { useCallback, useState } from 'react';
import message from '../ui/message';

export const useOperateApiKey = () => {
  const { removeToken, loading: removingLoading } = useRemoveSystemToken();
  const { createToken, loading: creatingLoading } = useCreateSystemToken();
  const { updateToken, loading: updatingLoading } = useUpdateSystemToken();
  const {
    data: tokenList,
    error: listError,
    loading: listLoading,
    refetch,
  } = useFetchSystemTokenList();
  const [operationError, setOperationError] = useState<Error | null>(null);

  const showDeleteConfirm = useShowDeleteConfirm();

  const runAndRefresh = useCallback(
    async (
      operation: () => Promise<{ code?: number; message?: string }>,
    ): Promise<boolean> => {
      setOperationError(null);
      try {
        const response = await operation();
        if (response?.code !== 0) {
          throw new Error(response?.message || 'API key operation failed');
        }
        await refetch();
        return true;
      } catch (error) {
        setOperationError(
          error instanceof Error ? error : new Error('API key operation failed'),
        );
        return false;
      }
    },
    [refetch],
  );

  const onRemoveToken = (token: string) => {
    showDeleteConfirm({
      onOk: () => runAndRefresh(() => removeToken(token)),
    });
  };

  const onCreateToken = useCallback(
    (values: ICreateAPIKeyRequest) =>
      runAndRefresh(() => createToken(values)),
    [createToken, runAndRefresh],
  );

  const onUpdateToken = useCallback(
    (token: string, values: IUpdateAPIKeyFields) =>
      runAndRefresh(() => updateToken({ token, ...values })),
    [runAndRefresh, updateToken],
  );

  return {
    removeToken: onRemoveToken,
    createToken: onCreateToken,
    updateToken: onUpdateToken,
    tokenList,
    operationLoading:
      creatingLoading || updatingLoading || removingLoading,
    listLoading,
    error: listError ?? operationError,
    operationError,
  };
};

type ChartStatsType = {
  [k in keyof IStats]: Array<{ xAxis: string; yAxis: number }>;
};

export const useSelectChartStatsList = (): ChartStatsType => {
  const queryClient = useQueryClient();
  const data = queryClient.getQueriesData({ queryKey: ['fetchStats'] });
  const stats: IStats = (data.length > 0 ? data[0][1] : {}) as IStats;

  return Object.keys(stats).reduce((pre, cur) => {
    const item = stats[cur as keyof IStats];
    if (item.length > 0) {
      pre[cur as keyof IStats] = item.map((x) => ({
        xAxis: x[0] as string,
        yAxis: x[1] as number,
      }));
    }
    return pre;
  }, {} as ChartStatsType);
};

export const useShowTokenEmptyError = () => {
  const { t } = useTranslate('chat');

  const showTokenEmptyError = useCallback(() => {
    message.error(t('tokenError'));
  }, [t]);
  return { showTokenEmptyError };
};

export const useShowBetaEmptyError = () => {
  const { t } = useTranslate('chat');

  const showBetaEmptyError = useCallback(() => {
    message.error(t('betaError'));
  }, [t]);
  return { showBetaEmptyError };
};

const useFetchTokenListBeforeOtherStep = () => {
  const { showTokenEmptyError } = useShowTokenEmptyError();
  const { showBetaEmptyError } = useShowBetaEmptyError();

  const { data: tokenList, fetchSystemTokenList } =
    useFetchManualSystemTokenList();

  let token = '',
    beta = '';

  const fullAccessToken = Array.isArray(tokenList)
    ? selectFullAccessToken(tokenList)
    : undefined;
  token = fullAccessToken?.token ?? '';
  beta = fullAccessToken?.beta ?? '';

  const handleOperate = useCallback(async () => {
    const ret = await fetchSystemTokenList();
    const list = ret;
    const fullAccessToken = Array.isArray(list)
      ? selectFullAccessToken(list)
      : undefined;
    if (fullAccessToken) {
      if (!fullAccessToken.beta) {
        showBetaEmptyError();
        return false;
      }
      return fullAccessToken.token;
    } else {
      showTokenEmptyError();
      return false;
    }
  }, [fetchSystemTokenList, showBetaEmptyError, showTokenEmptyError]);

  return {
    token,
    beta,
    handleOperate,
  };
};

export const useShowEmbedModal = () => {
  const {
    visible: embedVisible,
    hideModal: hideEmbedModal,
    showModal: showEmbedModal,
  } = useSetModalState();

  const { handleOperate, token, beta } = useFetchTokenListBeforeOtherStep();

  const handleShowEmbedModal = useCallback(async () => {
    const succeed = await handleOperate();
    if (succeed) {
      showEmbedModal();
    }
  }, [handleOperate, showEmbedModal]);

  return {
    showEmbedModal: handleShowEmbedModal,
    hideEmbedModal,
    embedVisible,
    embedToken: token,
    beta,
  };
};
