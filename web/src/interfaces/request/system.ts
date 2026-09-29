import type { APIKeyType } from '../database/chat';

export type APIKeyExpiryDays = 30 | 90 | 180 | 365;

export interface ICreateAPIKeyRequest {
  name: string;
  key_type: APIKeyType;
  allowed_dataset_ids?: string[];
  expires_in_days?: APIKeyExpiryDays | null;
}

export interface IUpdateAPIKeyRequest {
  token: string;
  name?: string;
  allowed_dataset_ids?: string[];
  expires_in_days?: APIKeyExpiryDays | null;
  enabled?: boolean;
}

export type IUpdateAPIKeyFields = Omit<IUpdateAPIKeyRequest, 'token'>;

export interface IDeleteAPIKeyRequest {
  token: string;
}

export interface ISetLangfuseConfigRequestBody {
  secret_key: string;
  public_key: string;
  host: string;
}
