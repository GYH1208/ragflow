"""Department IDs and server-managed center grants; empty always means deny."""

from datetime import UTC, datetime

from peewee import SqliteDatabase

from api.db.db_models import CenterDatasetGrant as G
from api.db.db_models import EnterpriseDepartment as D
from api.db.db_models import Knowledgebase
from common.misc_utils import get_uuid


class CenterDatasetService:
    @staticmethod
    def sync_departments(enterprise_id, paths):
        with D._meta.database.atomic():
            for path in paths:
                for node in path.hierarchy:
                    row, created = D.get_or_create(enterprise_id=enterprise_id, department_id=node.id, defaults={"id": get_uuid(), "name": node.name, "parent_id": node.parent_id})
                    values = {"name": node.name, "parent_id": node.parent_id}
                    if created or any(getattr(row, k) != v for k, v in values.items()):
                        D.update(**values, synced_at=datetime.now(UTC).replace(tzinfo=None)).where(D.id == row.id).execute()

    @staticmethod
    def _departments(enterprise_id, lock=False):
        query = D.select().where(D.enterprise_id == enterprise_id).order_by(D.department_id)
        if lock and not isinstance(D._meta.database, SqliteDatabase):
            query = query.for_update()
        return list(query)

    @classmethod
    def set_center(cls, enterprise_id, department_id, enabled, actor_id):
        if type(enabled) is not bool:
            raise ValueError("enabled must be boolean")
        with D._meta.database.atomic():
            nodes = {n.department_id: n for n in cls._departments(enterprise_id, lock=True)}
            row = nodes.get(department_id)
            if row is None:
                raise ValueError("Department not found in this enterprise")
            if enabled:

                def ancestors(start):
                    seen = set()
                    current = start
                    while current in nodes:
                        if current in seen:
                            raise ValueError("Department cycle")
                        seen.add(current)
                        current = nodes[current].parent_id
                    return seen

                branch = ancestors(department_id)
                for other in nodes.values():
                    if other.department_id != department_id and other.is_center and (other.department_id in branch or department_id in ancestors(other.department_id)):
                        raise ValueError("Ancestor and descendant cannot both be centers")
            D.update(is_center=enabled, updated_by=actor_id).where(D.id == row.id).execute()

    @staticmethod
    def resolve_center(enterprise_id, paths):
        primary = [p for p in paths if p.is_main]
        if len(primary) != 1 or not paths or any(not p.complete for p in paths):
            return None
        path = primary[0]
        ids = [n.id for n in path.hierarchy]
        if not ids or len(ids) != len(set(ids)) or ids[-1] != path.department_id:
            return None
        hits = list(D.select(D.department_id).where((D.enterprise_id == enterprise_id) & D.is_center & D.department_id.in_(ids)))
        return hits[0].department_id if len(hits) == 1 else None

    @classmethod
    def replace_grants(cls, enterprise_id, center_id, dataset_ids, actor_id):
        if not isinstance(dataset_ids, list) or len(dataset_ids) > 1000 or any(not isinstance(x, str) or not x or len(x) > 32 for x in dataset_ids):
            raise ValueError("dataset_ids must be a list of valid IDs")
        requested = set(dataset_ids)
        with D._meta.database.atomic():
            centers = cls._departments(enterprise_id, lock=True)
            if not any(c.department_id == center_id and c.is_center for c in centers):
                raise ValueError("Center not found in this enterprise")
            valid = {k.id for k in Knowledgebase.select(Knowledgebase.id).where(Knowledgebase.id.in_(requested) & (Knowledgebase.status == "1"))} if requested else set()
            if requested != valid:
                raise ValueError("One or more datasets are unavailable")
            G.update(enabled=False, updated_by=actor_id).where((G.enterprise_id == enterprise_id) & (G.center_department_id == center_id)).execute()
            for dataset_id in sorted(requested):
                row, _ = G.get_or_create(
                    enterprise_id=enterprise_id, center_department_id=center_id, dataset_id=dataset_id, defaults={"id": get_uuid(), "created_by": actor_id, "updated_by": actor_id}
                )
                G.update(enabled=True, scope="knowledge:retrieve", updated_by=actor_id).where(G.id == row.id).execute()
        return frozenset(requested)

    @staticmethod
    def allowed_datasets(enterprise_id, center_id):
        if center_id is None or not D.select().where((D.enterprise_id == enterprise_id) & (D.department_id == center_id) & D.is_center).exists():
            return frozenset()
        query = (
            G.select(G.dataset_id)
            .join(Knowledgebase, on=(G.dataset_id == Knowledgebase.id))
            .where((G.enterprise_id == enterprise_id) & (G.center_department_id == center_id) & G.enabled & (G.scope == "knowledge:retrieve") & (Knowledgebase.status == "1"))
        )
        return frozenset(g.dataset_id for g in query)
