export interface IUserInfo {
  access_token: string;
  avatar?: any;
  color_schema: string;
  create_date: string;
  create_time: number;
  email: string;
  id: string;
  is_active: string;
  is_anonymous: string;
  is_authenticated: string;
  is_superuser: boolean;
  language: string;
  last_login_time: string;
  login_channel: string;
  nickname: string;
  password: string;
  status: string;
  timezone: string;
  update_date: string;
  update_time: number;
}

export type TaskExecutorElapsed = Record<string, number[]>;

export interface TaskExecutorHeartbeatItem {
  boot_at: string;
  current: null;
  done: number;
  failed: number;
  lag: number;
  name: string;
  now: string;
  pending: number;
}

export interface ISystemStatus {
  es: Es;
  storage: Storage;
  database: Database;
  redis: Redis;
  task_executor_heartbeat: Record<string, TaskExecutorHeartbeatItem[]>;
}

interface Redis {
  status: string;
  elapsed: number;
  error: string;
  pending: number;
}

export interface Storage {
  status: string;
  elapsed: number;
  error: string;
}

export interface Database {
  status: string;
  elapsed: number;
  error: string;
}

interface Es {
  status: string;
  elapsed: number;
  error: string;
  number_of_nodes: number;
  active_shards: number;
}

export interface ITenantUser {
  id: string;
  avatar: string;
  delta_seconds: number;
  email: string;
  is_active: string;
  is_anonymous: string;
  is_authenticated: string;
  is_superuser: boolean;
  nickname: string;
  role: string;
  status: string;
  update_date: string;
  user_id: string;
}

export interface ITenant {
  avatar: string;
  delta_seconds: number;
  email: string;
  nickname: string;
  role: string;
  tenant_id: string;
  update_date: string;
}

export type TeamMemberState = 'invited' | 'active';

export type TeamMembershipState = 'owner' | TeamMemberState;

export type TeamInvitationAction = 'accept' | 'reject';

export interface ITeamRecord {
  id: string;
  tenant_id: string;
  name: string;
  created_by: string;
  status: string;
  create_date: string;
  create_time: number;
  update_date: string;
  update_time: number;
}

export interface ITeam extends ITeamRecord {
  membership_state: TeamMembershipState;
  member_count: number;
  dataset_count: number;
  can_manage: boolean;
}

export type ITeamListResponse = ITeam[];

export interface ITeamMember {
  id: string;
  email: string;
  nickname: string;
  avatar: string | null;
  state: TeamMemberState;
}

export interface IDeleteTeamResponse {
  unassigned_dataset_count: number;
}
