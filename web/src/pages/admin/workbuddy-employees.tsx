import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import {
  getWorkBuddyEmployee,
  listWorkBuddyCenters,
  listWorkBuddyEmployees,
  setWorkBuddyEmployeeEnabled,
} from '@/services/admin-service';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

export default function WorkBuddyEmployees() {
  const { t } = useTranslation();
  const cache = useQueryClient();
  const [q, setQ] = useState('');
  const [center, setCenter] = useState('');
  const [enabled, setEnabled] = useState('');
  const [page, setPage] = useState(1);
  const [selected, setSelected] = useState<string | null>(null);
  const employees = useQuery({
    queryKey: ['admin/workbuddy/employees', q, center, enabled, page],
    queryFn: () =>
      listWorkBuddyEmployees({
        q,
        center_id: center || undefined,
        enabled: enabled === '' ? undefined : enabled === 'true',
        page,
        page_size: 30,
      }),
  });
  const centers = useQuery({
    queryKey: ['admin/workbuddy/centers'],
    queryFn: listWorkBuddyCenters,
  });
  const detail = useQuery({
    queryKey: ['admin/workbuddy/employee', selected],
    queryFn: () => getWorkBuddyEmployee(selected!),
    enabled: !!selected,
  });
  const toggle = useMutation({
    mutationFn: ({ id, value }: { id: string; value: boolean }) =>
      setWorkBuddyEmployeeEnabled(id, value),
    onSuccess: () => {
      cache.invalidateQueries({ queryKey: ['admin/workbuddy/employees'] });
      cache.invalidateQueries({ queryKey: ['admin/workbuddy/employee'] });
    },
  });
  const label = (key: string) => t(`admin.workbuddy.${key}`);
  const cols = [
    'name',
    'alias',
    'center',
    'department',
    'path',
    'directoryStatus',
    'localStatus',
    'synced',
    'lastSeen',
    'actions',
  ];
  return (
    <Card className="h-full overflow-auto bg-transparent shadow-none">
      <CardHeader>
        <CardTitle>{label('employees')}</CardTitle>
        <p className="text-sm text-text-secondary">{label('employeeHint')}</p>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="flex flex-wrap gap-3">
          <Input
            className="max-w-xs"
            aria-label={label('searchEmployees')}
            placeholder={label('searchEmployees')}
            value={q}
            onChange={(e) => {
              setQ(e.target.value);
              setPage(1);
            }}
          />
          <label className="flex items-center gap-2">
            {label('center')}
            <select
              className="rounded border bg-bg-base p-2"
              value={center}
              onChange={(e) => {
                setCenter(e.target.value);
                setPage(1);
              }}
            >
              <option value="">{label('all')}</option>
              {centers.data?.map((c) => (
                <option key={c.department_id} value={c.department_id}>
                  {c.name}
                </option>
              ))}
            </select>
          </label>
          <label className="flex items-center gap-2">
            {label('localStatus')}
            <select
              className="rounded border bg-bg-base p-2"
              value={enabled}
              onChange={(e) => {
                setEnabled(e.target.value);
                setPage(1);
              }}
            >
              <option value="">{label('all')}</option>
              <option value="true">{label('enabled')}</option>
              <option value="false">{label('disabled')}</option>
            </select>
          </label>
        </div>
        {(employees.isError || centers.isError || toggle.isError) && (
          <p role="alert">{label('failed')}</p>
        )}
        {employees.isPending ? (
          <p role="status">{label('loading')}</p>
        ) : (
          <div className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  {cols.map((key) => (
                    <TableHead key={key}>{label(key)}</TableHead>
                  ))}
                </TableRow>
              </TableHeader>
              <TableBody>
                {employees.data?.items.map((employee) => (
                  <TableRow key={employee.user_id}>
                    <TableCell>{employee.name}</TableCell>
                    <TableCell>{employee.alias_id || '—'}</TableCell>
                    <TableCell>
                      {employee.center_name || label('unassigned')}
                    </TableCell>
                    <TableCell>
                      {employee.primary_department_name || '—'}
                    </TableCell>
                    <TableCell className="min-w-64 whitespace-normal break-words">
                      {employee.department_path || '—'}
                      {employee.sync_status !== 'complete' && (
                        <p className="text-text-secondary">
                          {label('pending')}
                        </p>
                      )}
                    </TableCell>
                    <TableCell>{employee.directory_status || '—'}</TableCell>
                    <TableCell>
                      {label(employee.enabled ? 'enabled' : 'disabled')}
                    </TableCell>
                    <TableCell>{employee.profile_synced_at || '—'}</TableCell>
                    <TableCell>{employee.last_seen_at || '—'}</TableCell>
                    <TableCell>
                      <div className="flex gap-2">
                        <Button
                          variant="outline"
                          onClick={() => setSelected(employee.user_id)}
                        >
                          {label('details')}
                        </Button>
                        <Button
                          variant="outline"
                          disabled={toggle.isPending}
                          onClick={() =>
                            toggle.mutate({
                              id: employee.user_id,
                              value: !employee.enabled,
                            })
                          }
                        >
                          {label(employee.enabled ? 'disable' : 'enable')}
                        </Button>
                      </div>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        )}
        {employees.data?.total === 0 && <p>{label('noEmployees')}</p>}
        <div className="flex items-center justify-end gap-3">
          <span>
            {label('total')}: {employees.data?.total ?? 0}
          </span>
          <Button
            variant="outline"
            disabled={page === 1}
            onClick={() => setPage((p) => p - 1)}
          >
            {label('previous')}
          </Button>
          <span>{page}</span>
          <Button
            variant="outline"
            disabled={page * 30 >= (employees.data?.total ?? 0)}
            onClick={() => setPage((p) => p + 1)}
          >
            {label('next')}
          </Button>
        </div>
        <Dialog
          open={!!selected}
          onOpenChange={(open) => {
            if (!open) setSelected(null);
          }}
        >
          <DialogContent className="max-h-[85vh] overflow-auto">
            <DialogHeader>
              <DialogTitle>{label('details')}</DialogTitle>
              <DialogDescription>{label('employeeHint')}</DialogDescription>
            </DialogHeader>
            {detail.isPending ? (
              <p>{label('loading')}</p>
            ) : detail.isError ? (
              <p role="alert">{label('failed')}</p>
            ) : (
              detail.data && (
                <div className="space-y-4">
                  <p>
                    {detail.data.name} · {detail.data.alias_id}
                  </p>
                  <p>{label('source')}: WorkBuddy</p>
                  {detail.data.department_paths.map((path, i) => (
                    <div key={`${path.department_id}-${i}`}>
                      <p className="break-words">
                        {path.hierarchy.map((n) => n.name).join(' / ')}
                      </p>
                      {!path.complete && <p>{label('pending')}</p>}
                    </div>
                  ))}
                  <h3>{label('allowedDatasets')}</h3>
                  {detail.data.allowed_dataset_ids?.length ? (
                    <ul>
                      {detail.data.allowed_dataset_ids.map((id) => (
                        <li className="break-all" key={id}>
                          {id}
                        </li>
                      ))}
                    </ul>
                  ) : (
                    <p>{label('noAccess')}</p>
                  )}
                </div>
              )
            )}
          </DialogContent>
        </Dialog>
      </CardContent>
    </Card>
  );
}
