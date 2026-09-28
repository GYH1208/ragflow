import { useSetModalState, useTranslate } from '@/hooks/common-hooks';
import { useFetchManualSystemTokenList } from '@/hooks/use-user-setting-request';
import { selectFullAccessToken } from '@/utils/api-key';
import { useCallback } from 'react';
import message from '../ui/message';

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

export const useFetchTokenListBeforeOtherStep = () => {
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
