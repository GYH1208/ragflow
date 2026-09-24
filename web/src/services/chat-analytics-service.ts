import api from '@/utils/api';
import { registerNextServer } from '@/utils/register-server';
import { AxiosRequestConfig } from 'axios';

const methods = {
  getDashboard: {
    url: api.chatAnalytics,
    method: 'get',
  },
} as const;

const registeredService = registerNextServer<keyof typeof methods>(methods);

const chatAnalyticsService = {
  getDashboard: (config: AxiosRequestConfig) =>
    registeredService.getDashboard(config, true),
};

export default chatAnalyticsService;
