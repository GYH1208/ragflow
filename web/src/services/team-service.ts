import { ResponseType } from '@/interfaces/database/base';
import {
  IDeleteTeamResponse,
  ITeamListResponse,
  ITeamMember,
  ITeamRecord,
  TeamInvitationAction,
} from '@/interfaces/database/user-setting';
import api from '@/utils/api';
import request from '@/utils/request';
import type { RequestMethod } from 'umi-request';

// `utils/request` enables `getResponse`, but its exported annotation erases the
// literal `true`. Restore that runtime contract for typed team responses.
const requestWithResponse = request as RequestMethod<true>;

export const listTeams = () =>
  requestWithResponse.get<ResponseType<ITeamListResponse>>(api.listTeams);

export const createTeam = (name: string) =>
  requestWithResponse.post<ResponseType<ITeamRecord>>(api.createTeam, {
    data: { name },
  });

export const renameTeam = (teamId: string, name: string) =>
  requestWithResponse.patch<ResponseType<ITeamRecord>>(api.teamDetail(teamId), {
    data: { name },
  });

export const deleteTeam = (teamId: string) =>
  requestWithResponse.delete<ResponseType<IDeleteTeamResponse>>(
    api.teamDetail(teamId),
  );

export const listTeamMembers = (teamId: string) =>
  requestWithResponse.get<ResponseType<ITeamMember[]>>(api.teamMembers(teamId));

export const inviteTeamMember = (teamId: string, email: string) =>
  requestWithResponse.post<ResponseType<ITeamMember>>(api.teamMembers(teamId), {
    data: { email },
  });

export const removeTeamMember = (teamId: string, userId: string) =>
  requestWithResponse.delete<ResponseType<boolean>>(
    api.teamMember(teamId, userId),
  );

export const updateTeamInvitation = (
  teamId: string,
  action: TeamInvitationAction,
) =>
  requestWithResponse.patch<ResponseType<boolean>>(api.teamInvitation(teamId), {
    data: { action },
  });
