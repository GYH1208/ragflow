import * as service from '@/services/admin-service';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import WorkBuddyEmployees from './workbuddy-employees';
jest.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));
jest.mock('@/services/admin-service', () => ({
  listWorkBuddyEmployees: jest.fn(),
  listWorkBuddyCenters: jest.fn(),
  getWorkBuddyEmployee: jest.fn(),
  setWorkBuddyEmployeeEnabled: jest.fn(),
}));
const employee = {
  user_id: 'employee-1',
  name: '葛雨恒',
  alias_id: 'geyuheng',
  center_name: '后勤服务中心',
  primary_department_name: 'AI项目组',
  department_path: '后勤服务中心 / AI效率工程部 / AI项目组',
  enabled: true,
  directory_status: 'Enabled',
  sync_status: 'complete',
  profile_synced_at: '2026-10-15',
  last_seen_at: '2026-10-15',
  department_paths: [],
};
const mockList = service.listWorkBuddyEmployees as jest.Mock;
function mount() {
  return render(
    <QueryClientProvider
      client={
        new QueryClient({
          defaultOptions: {
            queries: { retry: false },
            mutations: { retry: false },
          },
        })
      }
    >
      <WorkBuddyEmployees />
    </QueryClientProvider>,
  );
}
beforeEach(() => {
  jest.clearAllMocks();
  mockList.mockResolvedValue({ items: [employee], total: 1 });
  (service.listWorkBuddyCenters as jest.Mock).mockResolvedValue([]);
  (service.getWorkBuddyEmployee as jest.Mock).mockResolvedValue({
    ...employee,
    allowed_dataset_ids: [],
  });
  (service.setWorkBuddyEmployeeEnabled as jest.Mock).mockResolvedValue(true);
});
test('employee_tab_shows_center_path_and_enable_only', async () => {
  mount();
  expect(await screen.findByText('葛雨恒')).toBeInTheDocument();
  expect(screen.getByText('后勤服务中心')).toBeInTheDocument();
  expect(screen.getByText(employee.department_path)).toBeInTheDocument();
  expect(screen.queryByText(/password/i)).not.toBeInTheDocument();
  fireEvent.click(
    screen.getByRole('button', { name: 'admin.workbuddy.disable' }),
  );
  await waitFor(() =>
    expect(service.setWorkBuddyEmployeeEnabled).toHaveBeenCalledWith(
      'employee-1',
      false,
    ),
  );
});
test('empty_grants_and_unsynced_profile_are_explained', async () => {
  mockList.mockResolvedValue({
    items: [{ ...employee, center_name: null, sync_status: 'pending' }],
    total: 1,
  });
  mount();
  expect(
    await screen.findByText('admin.workbuddy.pending'),
  ).toBeInTheDocument();
  fireEvent.click(
    screen.getByRole('button', { name: 'admin.workbuddy.details' }),
  );
  expect(
    await screen.findByText('admin.workbuddy.noAccess'),
  ).toBeInTheDocument();
});
