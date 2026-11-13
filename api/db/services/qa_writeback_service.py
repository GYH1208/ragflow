"""SQL-backed writeback leases and atomic document/KB counter completion."""

import time
import uuid

from peewee import SqliteDatabase

from api.apps.services.qa_writeback_service import QAWritebackError
from api.db.db_models import DB, Document, Knowledgebase, QAWriteback

LEASE_SECONDS = 300


def _processing():
    raise QAWritebackError(409, 106, "REQUEST_PROCESSING", "This writeback request is already being processed. Retry later.", True)


def _record(row):
    return {"id": row.id, "chunk_id": row.chunk_id, "prepared": row.prepared or None, "completed": row.completed}


class QAWritebackStore:
    @DB.connection_context()
    def claim(self, identity, fingerprint, chunk_id):
        defaults = {key: value for key, value in identity.items() if key != "id"}
        defaults.update(request_hash=fingerprint, chunk_id=chunk_id)
        row, _ = QAWriteback.get_or_create(id=identity["id"], defaults=defaults)
        if row.request_hash != fingerprint:
            raise QAWritebackError(409, 409, "IDEMPOTENCY_CONFLICT", "This external ID was already submitted with different content.")
        if row.completed:
            return _record(row), ""
        owner = uuid.uuid4().hex
        now = int(time.time())
        changed = (
            QAWriteback.update(lease_owner=owner, lease_until=now + LEASE_SECONDS)
            .where((QAWriteback.id == row.id) & (QAWriteback.completed == False) & ((QAWriteback.lease_owner == "") | (QAWriteback.lease_until <= now)))
            .execute()
        )
        if not changed:
            row = QAWriteback.get_by_id(row.id)
            if row.completed:
                return _record(row), ""
            _processing()
        return _record(QAWriteback.get_by_id(row.id)), owner

    @DB.connection_context()
    def prepare(self, key, owner, prepared):
        changed = (
            QAWriteback.update(prepared=prepared, lease_until=int(time.time()) + LEASE_SECONDS)
            .where((QAWriteback.id == key) & (QAWriteback.lease_owner == owner) & (QAWriteback.completed == False))
            .execute()
        )
        if not changed:
            _processing()

    @DB.connection_context()
    def finish(self, key, owner, publish):
        with DB.atomic():
            query = QAWriteback.select().where(QAWriteback.id == key)
            row = (query if isinstance(DB, SqliteDatabase) else query.for_update()).get()
            if row.completed:
                return False
            if row.lease_owner != owner:
                _processing()
            document_query = Document.select().where((Document.id == row.document_id) & (Document.kb_id == row.dataset_id))
            doc = (document_query if isinstance(DB, SqliteDatabase) else document_query.for_update()).get_or_none()
            if doc is None or str(doc.status) != "1":
                raise QAWritebackError(404, 404, "TARGET_NOT_FOUND", "Target document is no longer available.")
            if doc.parser_id != "qa":
                raise QAWritebackError(400, 101, "INVALID_DOCUMENT_TYPE", "Target document must use Q&A parsing.")
            if str(doc.run) == "1":
                raise QAWritebackError(409, 106, "DOCUMENT_BUSY", "Target document is being parsed. Retry later.", True)
            dataset_query = Knowledgebase.select().where(Knowledgebase.id == row.dataset_id)
            kb = (dataset_query if isinstance(DB, SqliteDatabase) else dataset_query.for_update()).get_or_none()
            if kb is None or str(kb.status) != "1" or kb.tenant_id != row.tenant_id:
                raise QAWritebackError(404, 404, "TARGET_NOT_FOUND", "Target dataset is no longer available.")
            # Lock ledger -> document -> KB (the existing counter lock order)
            # through publication. Takeover/reparse/deletion must wait.
            created = publish()
            token_num = int(row.prepared["token_num"])
            Document.update(chunk_num=Document.chunk_num + 1, token_num=Document.token_num + token_num).where(Document.id == row.document_id).execute()
            changed = (
                Knowledgebase.update(chunk_num=Knowledgebase.chunk_num + 1, token_num=Knowledgebase.token_num + token_num)
                .where((Knowledgebase.id == row.dataset_id) & (Knowledgebase.status == "1"))
                .execute()
            )
            if not changed:
                raise QAWritebackError(404, 404, "TARGET_NOT_FOUND", "Target dataset is no longer available.")
            QAWriteback.update(completed=True, lease_owner="", lease_until=0).where(QAWriteback.id == key).execute()
            return created

    @DB.connection_context()
    def release(self, key, owner):
        QAWriteback.update(lease_owner="", lease_until=0).where((QAWriteback.id == key) & (QAWriteback.lease_owner == owner)).execute()
