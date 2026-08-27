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

export const listTeams = () =>
  request.get<ResponseType<ITeamListResponse>>(api.listTeams);

export const createTeam = (name: string) =>
  request.post<ResponseType<ITeamRecord>>(api.createTeam, {
    data: { name },
  });

export const renameTeam = (teamId: string, name: string) =>
  request.patch<ResponseType<ITeamRecord>>(api.teamDetail(teamId), {
    data: { name },
  });

export const deleteTeam = (teamId: string) =>
  request.delete<ResponseType<IDeleteTeamResponse>>(api.teamDetail(teamId));

export const listTeamMembers = (teamId: string) =>
  request.get<ResponseType<ITeamMember[]>>(api.teamMembers(teamId));

export const inviteTeamMember = (teamId: string, email: string) =>
  request.post<ResponseType<ITeamMember>>(api.teamMembers(teamId), {
    data: { email },
  });

export const removeTeamMember = (teamId: string, userId: string) =>
  request.delete<ResponseType<boolean>>(api.teamMember(teamId, userId));

export const updateTeamInvitation = (
  teamId: string,
  action: TeamInvitationAction,
) =>
  request.patch<ResponseType<boolean>>(api.teamInvitation(teamId), {
    data: { action },
  });
