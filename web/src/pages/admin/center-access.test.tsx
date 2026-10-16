import * as service from '@/services/admin-service';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import CenterAccess from './center-access';
jest.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));
jest.mock('@/services/admin-service', () => ({
  listWorkBuddyCenters: jest.fn(),
  listWorkBuddyDepartments: jest.fn(),
  listWorkBuddyDatasets: jest.fn(),
  getWorkBuddyCenterDatasets: jest.fn(),
  replaceWorkBuddyCenterDatasets: jest.fn(),
  setWorkBuddyCenter: jest.fn(),
}));
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
      <CenterAccess />
    </QueryClientProvider>,
  );
}
beforeEach(() => {
  jest.clearAllMocks();
  (service.listWorkBuddyCenters as jest.Mock).mockResolvedValue([
    { department_id: '2', name: '后勤服务中心', is_center: true },
  ]);
  (service.listWorkBuddyDepartments as jest.Mock).mockResolvedValue([]);
  (service.listWorkBuddyDatasets as jest.Mock).mockResolvedValue({
    items: [{ id: 'kb-a', name: '研发结构案例库', owner: 'owner' }],
    total: 1,
  });
  (service.getWorkBuddyCenterDatasets as jest.Mock).mockResolvedValue({
    dataset_ids: ['kb-a'],
  });
  (service.replaceWorkBuddyCenterDatasets as jest.Mock).mockResolvedValue({
    dataset_ids: [],
  });
});
test('saving_center_replaces_complete_list_including_empty', async () => {
  mount();
  await screen.findByRole('option', { name: /后勤服务中心/ });
  fireEvent.change(screen.getByLabelText('admin.workbuddy.center'), {
    target: { value: '2' },
  });
  const check = await screen.findByRole('checkbox', { name: /研发结构案例库/ });
  await waitFor(() => expect(check).toBeChecked());
  fireEvent.click(check);
  fireEvent.click(screen.getByRole('button', { name: 'admin.workbuddy.save' }));
  await waitFor(() =>
    expect(service.replaceWorkBuddyCenterDatasets).toHaveBeenCalledWith(
      '2',
      [],
    ),
  );
});
test('failed_save_does_not_show_success', async () => {
  (service.replaceWorkBuddyCenterDatasets as jest.Mock).mockRejectedValue(
    new Error('failed'),
  );
  mount();
  await screen.findByRole('option', { name: /后勤服务中心/ });
  fireEvent.change(screen.getByLabelText('admin.workbuddy.center'), {
    target: { value: '2' },
  });
  const check = await screen.findByRole('checkbox');
  await waitFor(() => expect(check).toBeChecked());
  fireEvent.click(check);
  fireEvent.click(screen.getByRole('button', { name: 'admin.workbuddy.save' }));
  expect(await screen.findByRole('alert')).toHaveTextContent(
    'admin.workbuddy.failed',
  );
  expect(screen.queryByText('admin.workbuddy.saved')).not.toBeInTheDocument();
});
test('center_page_is_available_without_commercial_flag', async () => {
  mount();
  expect(
    await screen.findByText('admin.workbuddy.centerAccess'),
  ).toBeInTheDocument();
});

test('same_named_centers_show_their_distinct_department_ids', async () => {
  (service.listWorkBuddyCenters as jest.Mock).mockResolvedValue([
    { department_id: '2', name: 'Shared center', is_center: true },
    { department_id: '4', name: 'Shared center', is_center: true },
  ]);
  mount();
  expect(
    await screen.findByRole('option', { name: 'Shared center (2)' }),
  ).toBeInTheDocument();
  expect(
    screen.getByRole('option', { name: 'Shared center (4)' }),
  ).toBeInTheDocument();
});
