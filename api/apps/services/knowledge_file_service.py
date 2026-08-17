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
from api.db import FileType
from api.db.services.document_service import DocumentService
from api.db.services.file2document_service import File2DocumentService
from api.db.services.file_service import FileService


class KnowledgeFileService:
    """Knowledge-base-specific folder organization and listing operations."""

    MAX_FOLDER_DEPTH = 64

    @staticmethod
    def _to_dict(record):
        if isinstance(record, dict):
            return dict(record)
        if hasattr(record, "to_dict"):
            return dict(record.to_dict())
        return dict(vars(record))

    @classmethod
    def get_kb_root(cls, kb, tenant_id):
        if str(kb.tenant_id) != str(tenant_id):
            raise PermissionError("Knowledge base does not belong to this tenant.")
        kb_parent = FileService.get_kb_folder(tenant_id)
        if not kb_parent:
            raise RuntimeError("Cannot find the knowledge base root folder.")
        kb_parent_id = kb_parent["id"] if isinstance(kb_parent, dict) else kb_parent.id
        root_data = FileService.new_a_file_from_kb(tenant_id, kb.name, kb_parent_id)
        root_id = root_data["id"] if isinstance(root_data, dict) else root_data.id
        found, root = FileService.get_by_id(root_id)
        if not found:
            raise RuntimeError("Cannot find the knowledge base folder.")
        return root

    @classmethod
    def assert_folder_in_kb(cls, kb, tenant_id, folder_id):
        root = cls.get_kb_root(kb, tenant_id)
        found, folder = FileService.get_by_id(folder_id)
        if not found or str(folder.tenant_id) != str(tenant_id) or folder.type != FileType.FOLDER.value:
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
            if not found or str(current.tenant_id) != str(tenant_id):
                break
        raise PermissionError("Folder does not belong to this knowledge base.")

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
        link_by_file_id = {link.file_id: link.document_id for link in links}
        documents = list(DocumentService.get_by_ids(list(link_by_file_id.values()))) if link_by_file_id else []
        document_by_id = {document.id: document for document in documents}
        path_records = cls._load_path_records(children, root.id)

        folder_entries = [cls._serialize_folder(folder, root.id, path_records) for folder in folders]
        document_entries = []
        for file_entry in files:
            document_id = link_by_file_id.get(file_entry.id)
            document = document_by_id.get(document_id)
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
    def list_entries(cls, kb, tenant_id, *, parent_id, page, page_size, orderby, desc, keywords, filters):
        root = cls.get_kb_root(kb, tenant_id)
        parent = cls.assert_folder_in_kb(kb, tenant_id, parent_id or root.id)
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
    def get_ancestors(cls, kb, tenant_id, folder_id):
        root = cls.get_kb_root(kb, tenant_id)
        folder = cls.assert_folder_in_kb(kb, tenant_id, folder_id)
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
