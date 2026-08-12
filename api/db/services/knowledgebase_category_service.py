from peewee import fn

from api.db.db_models import DB, Knowledgebase, KnowledgebaseCategory
from api.db.services.common_service import CommonService
from api.db.services.knowledgebase_service import KnowledgebaseService
from api.db.services.user_service import TenantService
from common.constants import StatusEnum


class KnowledgebaseCategoryService(CommonService):
    model = KnowledgebaseCategory

    @classmethod
    def visible_tenant_ids(cls, user_id: str) -> list[str]:
        joined = TenantService.get_joined_tenants_by_user_id(user_id)
        return list(dict.fromkeys([user_id, *(item["tenant_id"] for item in joined)]))

    @classmethod
    def resolve_owner_ids(cls, user_id: str, owner_ids: list[str] | None = None) -> list[str]:
        visible_ids = cls.visible_tenant_ids(user_id)
        if not owner_ids:
            return visible_ids
        requested = set(owner_ids)
        return [tenant_id for tenant_id in visible_ids if tenant_id in requested]

    @classmethod
    @DB.connection_context()
    def name_exists(cls, tenant_id: str, name: str, exclude_id: str | None = None) -> bool:
        query = cls.model.select().where(
            cls.model.tenant_id == tenant_id,
            fn.LOWER(cls.model.name) == name.strip().lower(),
            cls.model.status == StatusEnum.VALID.value,
        )
        if exclude_id:
            query = query.where(cls.model.id != exclude_id)
        return query.exists()

    @classmethod
    @DB.connection_context()
    def list_with_counts(cls, user_id: str, owner_ids: list[str] | None = None) -> dict:
        joined_tenant_ids = cls.visible_tenant_ids(user_id)
        tenant_ids = cls.resolve_owner_ids(user_id, owner_ids)
        if not tenant_ids:
            return {"categories": [], "total_count": 0, "uncategorized_count": 0}

        categories = list(
            cls.model.select()
            .where(
                cls.model.tenant_id.in_(tenant_ids),
                cls.model.status == StatusEnum.VALID.value,
            )
            .order_by(cls.model.create_time.asc(), cls.model.name.asc())
        )

        visibility = KnowledgebaseService._visibility_and_status_filter(joined_tenant_ids, user_id)
        base_query = Knowledgebase.select().where(visibility, Knowledgebase.tenant_id.in_(tenant_ids))
        grouped_counts = {
            row["category_id"]: row["count"]
            for row in (
                base_query.select(
                    Knowledgebase.category_id,
                    fn.COUNT(Knowledgebase.id).alias("count"),
                )
                .group_by(Knowledgebase.category_id)
                .dicts()
            )
        }

        return {
            "categories": [
                {
                    **category.to_dict(),
                    "count": grouped_counts.get(category.id, 0),
                    "can_manage": category.tenant_id == user_id,
                }
                for category in categories
            ],
            "total_count": sum(grouped_counts.values()),
            "uncategorized_count": grouped_counts.get(None, 0),
        }

    @classmethod
    def delete_and_unassign(cls, category_id: str, tenant_id: str) -> int | None:
        category = cls.get_or_none(id=category_id, status=StatusEnum.VALID.value)
        if category is None or category.tenant_id != tenant_id:
            return None

        with DB.atomic():
            unassigned = KnowledgebaseService.filter_update(
                [Knowledgebase.category_id == category_id, Knowledgebase.tenant_id == tenant_id],
                {"category_id": None},
            )
            cls.delete_by_id(category_id)
        return unassigned
