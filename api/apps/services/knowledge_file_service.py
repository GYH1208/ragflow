#
#  Copyright 2026 The InfiniFlow Authors. All Rights Reserved.
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
#
from api.constants import FILE_NAME_LEN_LIMIT
from api.db import FileType
from api.db.db_models import DB
from api.db.services.document_service import DocumentService
from api.db.services.file2document_service import File2DocumentService
from api.db.services.file_service import FileService
from common.constants import FileSource
from common.misc_utils import get_uuid


def update_document_name_only(document_id, name):
    """Import lazily so read-only folder listing does not load indexing dependencies."""
    from api.apps.services.document_api_service import update_document_name_only as update_name

    return update_name(document_id, name)


class KnowledgeFileService:
    """Knowledge-base-specific folder organization and listing operations."""

    MAX_FOLDER_DEPTH = 64

    @staticmethod
    def _require_owner_context(kb, owner_tenant_id):
        if str(owner_tenant_id) != str(kb.tenant_id):
            raise PermissionError("Knowledge base owner context does not match the requested tenant.")
        return owner_tenant_id

    @staticmethod
    def _to_dict(record):
        if isinstance(record, dict):
            return dict(record)
        if hasattr(record, "to_dict"):
            return dict(record.to_dict())
        return dict(vars(record))

    @classmethod
    def get_kb_root(cls, kb, owner_tenant_id):
        owner_tenant_id = cls._require_owner_context(kb, owner_tenant_id)
        kb_parent = FileService.get_kb_folder(owner_tenant_id)
        if not kb_parent:
            raise RuntimeError("Cannot find the knowledge base root folder.")
        kb_parent_id = kb_parent["id"] if isinstance(kb_parent, dict) else kb_parent.id
        root_data = FileService.new_a_file_from_kb(owner_tenant_id, kb.name, kb_parent_id)
        root_id = root_data["id"] if isinstance(root_data, dict) else root_data.id
        found, root = FileService.get_by_id(root_id)
        if not found:
            raise RuntimeError("Cannot find the knowledge base folder.")
        return root

    @classmethod
    def assert_folder_in_kb(cls, kb, owner_tenant_id, folder_id):
        root = cls.get_kb_root(kb, owner_tenant_id)
        found, folder = FileService.get_by_id(folder_id)
        if not found or str(folder.tenant_id) != str(kb.tenant_id) or folder.type != FileType.FOLDER.value:
            raise PermissionError("Folder does not belong to this knowledge base.")

        current = folder
        visited = set()
        for _ in range(cls.MAX_FOLDER_DEPTH + 1):
            if current.id == root.id:
                return folder
            if current.id in visited or current.parent_id == current.id:
                break
            visited.add(current.id)
            found, current = FileService.get_by_id(current.parent_id)
            if not found or str(current.tenant_id) != str(kb.tenant_id):
                break
        raise PermissionError("Folder does not belong to this knowledge base.")

    @classmethod
    def assert_entry_in_kb(cls, kb, owner_tenant_id, entry_id):
        root = cls.get_kb_root(kb, owner_tenant_id)
        found, entry = FileService.get_by_id(entry_id)
        if not found or str(entry.tenant_id) != str(kb.tenant_id):
            raise PermissionError("Entry does not belong to this knowledge base.")

        current = entry
        visited = set()
        for _ in range(cls.MAX_FOLDER_DEPTH + 1):
            if current.id == root.id:
                return entry
            if current.id in visited or current.parent_id == current.id:
                break
            visited.add(current.id)
            found, current = FileService.get_by_id(current.parent_id)
            if not found or str(current.tenant_id) != str(kb.tenant_id):
                break
        raise PermissionError("Entry does not belong to this knowledge base.")

    @classmethod
    def _load_path_records(cls, entries, root_id):
        records = {entry.id: entry for entry in entries}
        frontier = {entry.parent_id for entry in entries if entry.id != root_id and entry.parent_id != root_id}
        visited = set(records)
        for _ in range(cls.MAX_FOLDER_DEPTH):
            frontier -= visited
            if not frontier:
                break
            parents = list(FileService.get_by_ids(list(frontier)))
            if not parents:
                break
            records.update({parent.id: parent for parent in parents})
            visited.update(parent.id for parent in parents)
            frontier = {parent.parent_id for parent in parents if parent.id != root_id and parent.parent_id != root_id}
        return records

    @classmethod
    def _build_relative_path(cls, entry, root_id, records):
        names = [entry.name]
        current = entry
        visited = {entry.id}
        for _ in range(cls.MAX_FOLDER_DEPTH):
            if current.parent_id == root_id:
                return "/".join(reversed(names))
            parent = records.get(current.parent_id)
            if parent is None or parent.id in visited:
                raise PermissionError("Entry does not belong to this knowledge base.")
            visited.add(parent.id)
            names.append(parent.name)
            current = parent
        raise PermissionError("Knowledge folder depth exceeds the supported limit.")

    @classmethod
    def _serialize_folder(cls, folder, root_id, path_records):
        data = cls._to_dict(folder)
        return {
            **data,
            "entry_type": "folder",
            "file_id": folder.id,
            "relative_path": cls._build_relative_path(folder, root_id, path_records),
            "has_child_folder": bool(FileService.query(parent_id=folder.id, type=FileType.FOLDER.value)),
        }

    @classmethod
    def _serialize_document(cls, document, file_entry, root_id, path_records):
        data = cls._to_dict(document)
        data["dataset_id"] = data.pop("kb_id", data.get("dataset_id"))
        data["chunk_count"] = data.pop("chunk_num", data.get("chunk_count", 0))
        data["token_count"] = data.pop("token_num", data.get("token_count", 0))
        data["chunk_method"] = data.pop("parser_id", data.get("chunk_method"))
        return {
            **data,
            "entry_type": "document",
            "file_id": file_entry.id,
            "parent_id": file_entry.parent_id,
            "relative_path": cls._build_relative_path(file_entry, root_id, path_records),
        }

    @classmethod
    def _document_matches_filters(cls, document, filters):
        data = cls._to_dict(document)
        filter_fields = {
            "run_status": "run",
            "types": "type",
            "suffix": "suffix",
        }
        for request_key, field_name in filter_fields.items():
            accepted = filters.get(request_key) or []
            if accepted and data.get(field_name) not in accepted:
                return False
        return True

    @classmethod
    def _resolve_kb_documents_by_file(cls, kb, links):
        document_ids = list(dict.fromkeys(link.document_id for link in links))
        documents = list(DocumentService.get_by_ids(document_ids)) if document_ids else []
        document_by_id = {
            document["id"] if isinstance(document, dict) else document.id: document
            for document in documents
        }
        documents_by_file = {}
        invalid_file_ids = set()
        for link in links:
            document = document_by_id.get(link.document_id)
            document_kb_id = None
            if document is not None:
                document_kb_id = document.get("kb_id") if isinstance(document, dict) else document.kb_id
            if document is None or str(document_kb_id) != str(kb.id):
                invalid_file_ids.add(link.file_id)
                continue
            documents_by_file.setdefault(link.file_id, []).append(document)
        return documents_by_file, invalid_file_ids

    @staticmethod
    def _require_single_kb_document(file_id, documents_by_file, invalid_file_ids):
        documents = documents_by_file.get(file_id, [])
        if file_id in invalid_file_ids or len(documents) != 1:
            raise RuntimeError("File/document association crosses the knowledge base boundary.")
        return documents[0]

    @staticmethod
    def _sort_entries(entries, orderby, desc):
        allowed_order_fields = {"name", "create_time", "update_time", "size"}
        order_field = orderby if orderby in allowed_order_fields else "create_time"

        def sort_key(entry):
            value = entry.get(order_field)
            if isinstance(value, str):
                value = value.casefold()
            return value is None, value

        return sorted(entries, key=sort_key, reverse=desc)

    @classmethod
    def _list_current_folder(cls, kb, root, parent, *, page, page_size, orderby, desc, filters):
        children = list(FileService.query(tenant_id=kb.tenant_id, parent_id=parent.id))
        folders = [entry for entry in children if entry.type == FileType.FOLDER.value]
        files = [entry for entry in children if entry.type != FileType.FOLDER.value]
        links = File2DocumentService.get_by_file_ids([entry.id for entry in files]) if files else []
        documents_by_file, _invalid_file_ids = cls._resolve_kb_documents_by_file(kb, links)
        path_records = cls._load_path_records(children, root.id)

        folder_entries = [cls._serialize_folder(folder, root.id, path_records) for folder in folders]
        document_entries = []
        for file_entry in files:
            linked_documents = documents_by_file.get(file_entry.id, [])
            document = linked_documents[0] if len(linked_documents) == 1 else None
            if document is not None and cls._document_matches_filters(document, filters):
                document_entries.append(cls._serialize_document(document, file_entry, root.id, path_records))

        ordered = [
            *cls._sort_entries(folder_entries, orderby, desc),
            *cls._sort_entries(document_entries, orderby, desc),
        ]
        start = (page - 1) * page_size
        return ordered[start : start + page_size], len(ordered)

    @classmethod
    def _search_documents(cls, kb, root, *, page, page_size, orderby, desc, keywords, filters):
        documents, total = DocumentService.get_by_kb_id(
            kb.id,
            page,
            page_size,
            orderby,
            desc,
            keywords,
            filters.get("run_status", []),
            filters.get("types", []),
            filters.get("suffix", []),
        )
        document_ids = [document["id"] if isinstance(document, dict) else document.id for document in documents]
        links = File2DocumentService.get_by_document_ids(document_ids) if document_ids else []
        file_id_by_document_id = {
            link["document_id"] if isinstance(link, dict) else link.document_id: link["file_id"] if isinstance(link, dict) else link.file_id
            for link in links
        }
        file_entries = list(FileService.get_by_ids(list(file_id_by_document_id.values()))) if file_id_by_document_id else []
        file_by_id = {entry.id: entry for entry in file_entries}
        path_records = cls._load_path_records(file_entries, root.id)

        serialized = []
        for document in documents:
            document_id = document["id"] if isinstance(document, dict) else document.id
            file_entry = file_by_id.get(file_id_by_document_id.get(document_id))
            if file_entry is not None:
                serialized.append(cls._serialize_document(document, file_entry, root.id, path_records))
        return serialized, total

    @classmethod
    def list_entries(cls, kb, owner_tenant_id, *, parent_id, page, page_size, orderby, desc, keywords, filters):
        root = cls.get_kb_root(kb, owner_tenant_id)
        parent = cls.assert_folder_in_kb(kb, owner_tenant_id, parent_id or root.id)
        if keywords:
            entries, total = cls._search_documents(
                kb,
                root,
                page=page,
                page_size=page_size,
                orderby=orderby,
                desc=desc,
                keywords=keywords,
                filters=filters,
            )
        else:
            entries, total = cls._list_current_folder(
                kb,
                root,
                parent,
                page=page,
                page_size=page_size,
                orderby=orderby,
                desc=desc,
                filters=filters,
            )
        return {
            "entries": entries,
            "parent_folder": {"id": parent.id, "name": parent.name},
            "total": total,
        }

    @classmethod
    def get_ancestors(cls, kb, owner_tenant_id, folder_id):
        root = cls.get_kb_root(kb, owner_tenant_id)
        folder = cls.assert_folder_in_kb(kb, owner_tenant_id, folder_id)
        ancestors = []
        current = folder
        visited = set()
        for _ in range(cls.MAX_FOLDER_DEPTH + 1):
            ancestors.append({"id": current.id, "name": current.name, "parent_id": current.parent_id})
            if current.id == root.id:
                return list(reversed(ancestors))
            if current.id in visited:
                break
            visited.add(current.id)
            found, current = FileService.get_by_id(current.parent_id)
            if not found:
                break
        raise PermissionError("Folder does not belong to this knowledge base.")

    @staticmethod
    def _validate_entry_name(name):
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Entry name cannot be empty.")
        if name != name.strip():
            raise ValueError("Entry name cannot start or end with whitespace.")
        if name in {".", ".."} or "/" in name or "\\" in name or "\x00" in name:
            raise ValueError("Entry name contains invalid path characters.")
        if len(name.encode("utf-8")) > FILE_NAME_LEN_LIMIT:
            raise ValueError(f"Entry name must be {FILE_NAME_LEN_LIMIT} bytes or less.")

    @classmethod
    def _validate_sibling_name(cls, parent_id, name, *, exclude_ids=None):
        cls._validate_entry_name(name)
        excluded = set(exclude_ids or [])
        if any(entry.id not in excluded for entry in FileService.query(parent_id=parent_id, name=name)):
            raise ValueError("An entry with the same name already exists in the destination folder.")

    @classmethod
    def create_folder(cls, kb, owner_tenant_id, parent_id, name, *, created_by=None):
        owner_tenant_id = cls._require_owner_context(kb, owner_tenant_id)
        parent = cls.assert_folder_in_kb(kb, owner_tenant_id, parent_id)
        cls._validate_sibling_name(parent.id, name)
        folder = FileService.insert(
            {
                "id": get_uuid(),
                "parent_id": parent.id,
                "tenant_id": owner_tenant_id,
                "created_by": owner_tenant_id if created_by is None else created_by,
                "name": name,
                "location": "",
                "size": 0,
                "type": FileType.FOLDER.value,
                "source_type": FileSource.KNOWLEDGEBASE,
            }
        )
        return cls._to_dict(folder)

    @classmethod
    def _destination_is_descendant_of(cls, destination, folder_id):
        current = destination
        visited = set()
        for _ in range(cls.MAX_FOLDER_DEPTH + 1):
            if current.id == folder_id:
                return True
            if current.id in visited or current.parent_id == current.id:
                return False
            visited.add(current.id)
            found, current = FileService.get_by_id(current.parent_id)
            if not found:
                return False
        return False

    @classmethod
    def move_entries(cls, kb, owner_tenant_id, entry_ids, destination_id):
        if not entry_ids:
            raise ValueError("At least one entry is required.")
        destination = cls.assert_folder_in_kb(kb, owner_tenant_id, destination_id)
        root = cls.get_kb_root(kb, owner_tenant_id)
        unique_ids = list(dict.fromkeys(entry_ids))
        entries = [cls.assert_entry_in_kb(kb, owner_tenant_id, entry_id) for entry_id in unique_ids]
        if any(entry.id == root.id for entry in entries):
            raise ValueError("The knowledge base root folder cannot be moved.")

        moving_ids = {entry.id for entry in entries}
        destination_names = {
            child.name
            for child in FileService.query(parent_id=destination.id)
            if child.id not in moving_ids
        }
        selected_names = set()
        for entry in entries:
            if entry.name in destination_names or entry.name in selected_names:
                raise ValueError("An entry with the same name already exists in the destination folder.")
            selected_names.add(entry.name)
            if entry.id == destination.id:
                raise ValueError("An entry cannot be moved into itself.")
            if entry.type == FileType.FOLDER.value and cls._destination_is_descendant_of(destination, entry.id):
                raise ValueError("A folder cannot be moved into its own descendant.")

        with DB.atomic():
            for entry in entries:
                if entry.parent_id != destination.id and not FileService.update_by_id_in_transaction(
                    entry.id, {"parent_id": destination.id}
                ):
                    raise RuntimeError("Database error (File move)!")
        return {"moved": len(entries)}

    @classmethod
    def rename_entry(cls, kb, owner_tenant_id, entry_id, name):
        entry = cls.assert_entry_in_kb(kb, owner_tenant_id, entry_id)
        root = cls.get_kb_root(kb, owner_tenant_id)
        if entry.id == root.id:
            raise ValueError("The knowledge base root folder cannot be renamed.")
        cls._validate_sibling_name(entry.parent_id, name, exclude_ids={entry.id})
        if entry.type == FileType.FOLDER.value:
            if not FileService.update_by_id(entry.id, {"name": name}):
                raise RuntimeError("Database error (Folder rename)!")
        else:
            links = File2DocumentService.get_by_file_id(entry.id)
            if not links:
                raise RuntimeError("Cannot find the document associated with this file.")
            documents_by_file, invalid_file_ids = cls._resolve_kb_documents_by_file(kb, links)
            document = cls._require_single_kb_document(entry.id, documents_by_file, invalid_file_ids)
            error = update_document_name_only(document.id, name)
            if error:
                message = error.get("message", str(error)) if isinstance(error, dict) else str(error)
                raise RuntimeError(message)
        return {"id": entry.id, "name": name}

    @classmethod
    def _collect_descendants_postorder(cls, roots, owner_tenant_id):
        files = []
        folders = []
        visited = set()

        def visit(entry):
            if entry.id in visited:
                return
            visited.add(entry.id)
            if str(entry.tenant_id) != str(owner_tenant_id):
                raise PermissionError("Entry does not belong to this knowledge base.")
            if entry.type != FileType.FOLDER.value:
                files.append(entry)
                return
            for child in FileService.list_all_files_by_parent_id(entry.id):
                visit(child)
            folders.append(entry)

        for root in roots:
            visit(root)
        return files, folders

    @classmethod
    def count_descendant_documents(cls, kb, owner_tenant_id, entry_ids):
        root = cls.get_kb_root(kb, owner_tenant_id)
        roots = [cls.assert_entry_in_kb(kb, owner_tenant_id, entry_id) for entry_id in dict.fromkeys(entry_ids)]
        if any(entry.id == root.id for entry in roots):
            raise ValueError("The knowledge base root folder cannot be deleted.")
        files, _folders = cls._collect_descendants_postorder(roots, owner_tenant_id)
        links = File2DocumentService.get_by_file_ids([entry.id for entry in files]) if files else []
        documents_by_file, _invalid_file_ids = cls._resolve_kb_documents_by_file(kb, links)
        return len(
            {
                document["id"] if isinstance(document, dict) else document.id
                for documents in documents_by_file.values()
                for document in documents
            }
        )

    @classmethod
    def delete_entries(cls, kb, owner_tenant_id, entry_ids):
        if not entry_ids:
            raise ValueError("At least one entry is required.")
        root = cls.get_kb_root(kb, owner_tenant_id)
        roots = [cls.assert_entry_in_kb(kb, owner_tenant_id, entry_id) for entry_id in dict.fromkeys(entry_ids)]
        if any(entry.id == root.id for entry in roots):
            raise ValueError("The knowledge base root folder cannot be deleted.")

        files, folders = cls._collect_descendants_postorder(roots, owner_tenant_id)
        path_records = cls._load_path_records([*files, *folders], root.id)
        links = File2DocumentService.get_by_file_ids([entry.id for entry in files]) if files else []
        documents_by_file, invalid_file_ids = cls._resolve_kb_documents_by_file(kb, links)
        document_by_file_id = {}
        for file_entry in files:
            document_by_file_id[file_entry.id] = cls._require_single_kb_document(
                file_entry.id,
                documents_by_file,
                invalid_file_ids,
            )
        deleted = 0
        failed = []

        for file_entry in files:
            document = document_by_file_id.get(file_entry.id)
            document_id = document.id if document is not None else None
            error = FileService.delete_docs([document_id], owner_tenant_id) if document_id else ""
            if error:
                failed.append(
                    {
                        "id": file_entry.id,
                        "path": cls._build_relative_path(file_entry, root.id, path_records),
                        "message": error,
                    }
                )
                continue
            if not document_id:
                FileService.delete_by_id(file_entry.id)
            deleted += 1

        for folder in folders:
            if not FileService.list_all_files_by_parent_id(folder.id):
                FileService.delete_by_id(folder.id)
                deleted += 1
        return {"deleted": deleted, "failed": failed}
