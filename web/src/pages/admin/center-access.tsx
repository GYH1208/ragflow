import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import {
  getWorkBuddyCenterDatasets,
  getWorkBuddyEnterpriseDatasets,
  listWorkBuddyCenters,
  listWorkBuddyDatasets,
  listWorkBuddyDepartments,
  replaceWorkBuddyCenterDatasets,
  replaceWorkBuddyEnterpriseDatasets,
  setWorkBuddyCenter,
} from '@/services/admin-service';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { isAxiosError } from 'axios';
import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';

const enterpriseScope = 'enterprise';

export default function CenterAccess() {
  const { t } = useTranslation();
  const label = (key: string) => t(`admin.workbuddy.${key}`);
  const cache = useQueryClient();
  const [scope, setScope] = useState('');
  const [department, setDepartment] = useState('');
  const [q, setQ] = useState('');
  const [page, setPage] = useState(1);
  const [selected, setSelected] = useState<string[]>([]);
  const [dirty, setDirty] = useState(false);
  const [saved, setSaved] = useState(false);
  const centers = useQuery({
    queryKey: ['admin/workbuddy/centers'],
    queryFn: listWorkBuddyCenters,
  });
  const departments = useQuery({
    queryKey: ['admin/workbuddy/departments'],
    queryFn: listWorkBuddyDepartments,
  });
  const datasets = useQuery({
    queryKey: ['admin/workbuddy/datasets', q, page],
    queryFn: () => listWorkBuddyDatasets({ q, page, page_size: 30 }),
  });
  const grants = useQuery({
    queryKey: ['admin/workbuddy/grants', scope],
    queryFn: async () => ({
      scope,
      ...(await (scope === enterpriseScope
        ? getWorkBuddyEnterpriseDatasets()
        : getWorkBuddyCenterDatasets(scope))),
    }),
    enabled: !!scope,
  });
  useEffect(() => {
    if (grants.data?.scope === scope && !dirty)
      setSelected(grants.data.dataset_ids);
  }, [grants.data, scope, dirty]);
  const save = useMutation({
    mutationFn: ({ scope, ids }: { scope: string; ids: string[] }) =>
      scope === enterpriseScope
        ? replaceWorkBuddyEnterpriseDatasets(ids)
        : replaceWorkBuddyCenterDatasets(scope, ids),
    onSuccess: (data, variables) => {
      cache.setQueryData(['admin/workbuddy/grants', variables.scope], {
        scope: variables.scope,
        dataset_ids: data.dataset_ids,
      });
      setDirty(false);
      setSaved(true);
    },
  });
  const mark = useMutation({
    mutationFn: ({ id, value }: { id: string; value: boolean }) =>
      setWorkBuddyCenter(id, value),
    onSuccess: (_data, variables) => {
      cache.invalidateQueries({ queryKey: ['admin/workbuddy/centers'] });
      cache.invalidateQueries({ queryKey: ['admin/workbuddy/departments'] });
      if (!variables.value && scope === variables.id) {
        setScope('');
        setSelected([]);
        setDirty(false);
        setSaved(false);
        save.reset();
      }
    },
  });
  const changeScope = (value: string) => {
    if (dirty && !window.confirm(label('discard'))) return;
    setScope(value);
    setSelected([]);
    setDirty(false);
    setSaved(false);
    save.reset();
  };
  const node = departments.data?.find((d) => d.department_id === department);
  const visibleIds = new Set(datasets.data?.items.map((d) => d.id));
  const loaded = grants.data?.scope === scope && !grants.isError;
  const hierarchyConflict =
    isAxiosError<{ data?: { reason?: string } }>(mark.error) &&
    mark.error.response?.data?.data?.reason === 'center_hierarchy_conflict';
  return (
    <Card className="h-full overflow-auto bg-transparent shadow-none">
      <CardHeader>
        <CardTitle>{label('centerAccess')}</CardTitle>
        <p className="text-sm text-text-secondary">{label('centerHint')}</p>
      </CardHeader>
      <CardContent className="space-y-6">
        <section className="space-y-3 rounded border p-4">
          <h2>{label('centerDefinition')}</h2>
          <p className="text-sm text-text-secondary">{label('syncedOnly')}</p>
          <div className="flex flex-wrap items-center gap-3">
            <label>
              {label('organization')}{' '}
              <select
                className="rounded border bg-bg-base p-2"
                value={department}
                disabled={mark.isPending || save.isPending}
                onChange={(e) => {
                  setDepartment(e.target.value);
                  mark.reset();
                }}
              >
                <option value="">{label('choose')}</option>
                {departments.data?.map((d) => (
                  <option key={d.department_id} value={d.department_id}>
                    {d.name} ({d.department_id})
                  </option>
                ))}
              </select>
            </label>
            <Button
              variant="outline"
              disabled={!node || mark.isPending || save.isPending || dirty}
              onClick={() => {
                if (node)
                  mark.mutate({
                    id: node.department_id,
                    value: !node.is_center,
                  });
              }}
            >
              {label(node?.is_center ? 'unmark' : 'mark')}
            </Button>
          </div>
          <p className="text-sm text-text-secondary">
            {label('centerConflict')}
          </p>
          {mark.isError && (
            <p role="alert">
              {label(hierarchyConflict ? 'centerHierarchyError' : 'failed')}
            </p>
          )}
        </section>
        <label className="flex flex-wrap items-center gap-3">
          {label('scope')}
          <select
            className="rounded border bg-bg-base p-2"
            value={scope}
            disabled={save.isPending || mark.isPending}
            onChange={(e) => changeScope(e.target.value)}
          >
            <option value="">{label('choose')}</option>
            <option value={enterpriseScope}>{label('enterprise')}</option>
            {centers.data?.map((c) => (
              <option key={c.department_id} value={c.department_id}>
                {c.name} ({c.department_id})
              </option>
            ))}
          </select>
        </label>
        {(centers.isError ||
          departments.isError ||
          datasets.isError ||
          grants.isError ||
          save.isError) && <p role="alert">{label('failed')}</p>}
        {scope && (
          <section className="space-y-4">
            <h2>{label('allowedDatasets')}</h2>
            <p className="text-sm text-text-secondary">
              {label(
                scope === enterpriseScope ? 'enterpriseHint' : 'emptyDenies',
              )}
            </p>
            <Input
              className="max-w-sm"
              value={q}
              aria-label={label('searchDatasets')}
              placeholder={label('searchDatasets')}
              onChange={(e) => {
                setQ(e.target.value);
                setPage(1);
              }}
            />
            {grants.isPending || datasets.isPending ? (
              <p role="status">{label('loading')}</p>
            ) : (
              <div className="space-y-2">
                {datasets.data?.items.map((kb) => (
                  <label
                    key={kb.id}
                    className="flex items-start gap-3 rounded border p-3"
                  >
                    <input
                      type="checkbox"
                      className="mt-1"
                      checked={selected.includes(kb.id)}
                      disabled={!loaded || save.isPending}
                      onChange={(e) => {
                        setSelected((ids) =>
                          e.target.checked
                            ? [...new Set([...ids, kb.id])]
                            : ids.filter((id) => id !== kb.id),
                        );
                        setDirty(true);
                        setSaved(false);
                        save.reset();
                      }}
                    />
                    <span className="break-all">
                      {kb.name}
                      <span className="block text-xs text-text-secondary">
                        {kb.owner} · {kb.id}
                      </span>
                    </span>
                  </label>
                ))}
              </div>
            )}
            {selected
              .filter((id) => !visibleIds.has(id))
              .map((id) => (
                <div className="flex items-center gap-3 break-all" key={id}>
                  <span>
                    {label('selectedElsewhere')}: {id}
                  </span>
                  <Button
                    variant="outline"
                    disabled={save.isPending || !loaded}
                    onClick={() => {
                      setSelected((ids) => ids.filter((value) => value !== id));
                      setDirty(true);
                      setSaved(false);
                    }}
                  >
                    {label('remove')}
                  </Button>
                </div>
              ))}
            <div className="flex flex-wrap items-center justify-between gap-3">
              <span>
                {label('selected')}: {selected.length}
              </span>
              <div className="flex gap-3">
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
                  disabled={page * 30 >= (datasets.data?.total ?? 0)}
                  onClick={() => setPage((p) => p + 1)}
                >
                  {label('next')}
                </Button>
              </div>
            </div>
            <Button
              disabled={!loaded || !dirty || save.isPending}
              onClick={() => {
                setSaved(false);
                save.mutate({ scope, ids: [...selected] });
              }}
            >
              {label('save')}
            </Button>
            {saved && <p role="status">{label('saved')}</p>}
          </section>
        )}
      </CardContent>
    </Card>
  );
}
