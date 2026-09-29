import type { IToken } from '@/interfaces/database/chat';

const isActive = (token: IToken) => {
  if (token.enabled === false) {
    return false;
  }

  if (token.expires_at == null) {
    return true;
  }

  return new Date(token.expires_at).getTime() > Date.now();
};

export const selectFullAccessToken = (tokens: IToken[]): IToken | undefined => {
  const explicitFullAccessToken = tokens.find(
    (token) => token.key_type === 'full_access' && isActive(token),
  );
  if (explicitFullAccessToken) {
    return explicitFullAccessToken;
  }

  return tokens.find((token) => token.key_type === undefined && isActive(token));
};
