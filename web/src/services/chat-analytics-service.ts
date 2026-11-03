import api from '@/utils/api';
import { registerNextServer } from '@/utils/register-server';
import { AxiosRequestConfig } from 'axios';

const methods = {
  getDashboard: {
    url: api.chatAnalytics,
    method: 'get',
  },
  getMcpDashboard: {
    url: api.mcpAnalytics,
    method: 'get',
  },
} as const;

const registeredService = registerNextServer<keyof typeof methods>(methods);

const chatAnalyticsService = {
  getMcpDashboard: () => registeredService.getMcpDashboard({}, true),
  getDashboard: (config: AxiosRequestConfig) =>
    registeredService.getDashboard(config, true),
};

export default chatAnalyticsService;
