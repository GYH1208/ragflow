# 知识库文件夹管理实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不改变知识库解析存储地址和解析状态的前提下，让文件夹上传保留目录树，并在知识库页面支持文件管理器式浏览、搜索、新建、重命名、移动和递归删除。

**Architecture:** 继续使用 `Document` 作为解析与检索数据源，使用现有 `File`/`File2Document` 作为目录组织数据源。上传请求逐文件携带相对路径；后端创建真实 `File` 文件夹树。知识库目录操作走专用服务，只更新组织层级，不复用会移动底层对象的通用文件管理移动逻辑。

**Tech Stack:** Python 3.10+、Quart、Peewee、pytest、TypeScript 5、React 18、React Query、TanStack Table、Jest、Testing Library。

## Global Constraints

- 新建、移动或重命名文件夹不得修改任何 `Document.location`。
- 移动知识库文件只修改 `File.parent_id`，不得移动底层存储对象。
- 目录操作不得改变解析状态、分块数、词元数、解析配置、元数据或索引分块，也不得创建解析任务。
- 文件夹不是文档，不显示或执行解析、启用/禁用、元数据等文档操作。
- 同一父目录名称唯一；不同目录允许同名文件。
- 合法 Unicode 路径（包括中文）必须原样保留；绝对路径、盘符、NUL 和 `.`/`..` 路径穿越必须拒绝。
- 历史扁平文档与单文件上传继续显示在知识库根目录，不做迁移。
- 全局搜索搜索整个知识库，返回相对路径；清空搜索后恢复此前所在目录。
- 规格来源：`docs/superpowers/specs/2026-08-17-knowledge-base-folder-management-design.md`。

---

## 文件职责

- `api/utils/file_utils.py`：上传相对路径的结构校验与规范化。
- `api/db/services/file_service.py`：上传时创建/复用 `File` 目录链及叶子关联。
- `api/apps/services/knowledge_file_service.py`：新增；目录查询、祖先路径和所有知识库目录 mutation。
- `api/apps/restful_apis/knowledge_file_api.py`：新增；鉴权、请求校验和响应序列化。
- `web/src/utils/knowledge-file-upload.ts`：新增；构建文件与逐项相对路径匹配的 `FormData`。
- `web/src/interfaces/database/knowledge-file.ts`：新增；定义文件夹/文档联合类型。
- `web/src/hooks/use-knowledge-file-request.ts`：新增；封装目录 query 和 mutation。
- `web/src/pages/dataset/dataset/knowledge-file-browser.tsx`：新增；组合目录、搜索、表格、面包屑和弹窗。
- `web/src/pages/dataset/dataset/knowledge-file-breadcrumb.tsx`：新增；渲染知识库目录面包屑。
- `web/src/pages/dataset/dataset/knowledge-entry-action-cell.tsx`：新增；渲染文件夹/文档各自允许的操作。
- `web/src/pages/dataset/dataset/knowledge-move-dialog.tsx`：新增；选择知识库内的移动目标。

---

### Task 1: 实现 Unicode 安全的上传相对路径校验

**Files:**
- Create: `test/unit_test/api/utils/test_knowledge_upload_path.py`
- Modify: `api/utils/file_utils.py`

**Interfaces:**
- Consumes: multipart 中的 `relative_path` 和实际上传文件名。
- Produces: `normalize_knowledge_upload_path(raw_path: str, expected_filename: str, *, max_depth: int = 32) -> list[str]`。

- [ ] **Step 1: 写失败测试，覆盖中文、分隔符和不安全路径**

```python
import pytest

from api.utils.file_utils import normalize_knowledge_upload_path


def test_preserves_unicode_folder_names():
    assert normalize_knowledge_upload_path(
        "2、二级文件/制度文件/审批流程.docx",
        "审批流程.docx",
    ) == ["2、二级文件", "制度文件", "审批流程.docx"]


def test_normalizes_backslashes_and_duplicate_separators():
    assert normalize_knowledge_upload_path(
        r"一级\\二级//file.txt",
        "file.txt",
    ) == ["一级", "二级", "file.txt"]


@pytest.mark.parametrize(
    "raw_path",
    [
        "/etc/passwd",
        r"C:\\secret\\file.txt",
        "../file.txt",
        "safe/../file.txt",
        "safe/./file.txt",
        "safe/fi\x00le.txt",
    ],
)
def test_rejects_unsafe_paths(raw_path):
    with pytest.raises(ValueError):
        normalize_knowledge_upload_path(raw_path, "file.txt")


def test_rejects_basename_mismatch():
    with pytest.raises(ValueError, match="does not match"):
        normalize_knowledge_upload_path("folder/other.txt", "file.txt")


def test_rejects_excessive_depth():
    raw_path = "/".join(["folder"] * 33 + ["file.txt"])
    with pytest.raises(ValueError, match="depth"):
        normalize_knowledge_upload_path(raw_path, "file.txt", max_depth=32)
```

- [ ] **Step 2: 运行测试并确认因函数不存在而失败**

Run: `uv run pytest test/unit_test/api/utils/test_knowledge_upload_path.py -v`

Expected: FAIL，错误包含 `cannot import name 'normalize_knowledge_upload_path'`。

- [ ] **Step 3: 实现最小路径规范化函数**

```python
_WINDOWS_DRIVE_PATH = re.compile(r"^[A-Za-z]:[\\/]")


def normalize_knowledge_upload_path(raw_path: str, expected_filename: str, *, max_depth: int = 32) -> list[str]:
    if not isinstance(raw_path, str) or not raw_path.strip():
        return [expected_filename]
    if "\x00" in raw_path or raw_path.startswith(("/", "\\")) or _WINDOWS_DRIVE_PATH.match(raw_path):
        raise ValueError("Upload path must be relative.")
    normalized = raw_path.replace("\\", "/")
    raw_segments = normalized.split("/")
    if any(segment in {".", ".."} for segment in raw_segments):
        raise ValueError("Upload path contains traversal segments.")
    segments = [segment for segment in raw_segments if segment]
    if not segments or segments[-1] != expected_filename:
        raise ValueError("Upload path basename does not match the uploaded filename.")
    if len(segments) - 1 > max_depth:
        raise ValueError("Upload path exceeds the maximum directory depth.")
    if any(len(segment.encode("utf-8")) > FILE_NAME_LEN_LIMIT for segment in segments):
        raise ValueError(f"Upload path segment exceeds {FILE_NAME_LEN_LIMIT} bytes.")
    return segments
```

- [ ] **Step 4: 运行路径测试和现有文件工具测试**

Run: `uv run pytest test/unit_test/api/utils/test_knowledge_upload_path.py test/unit_test/api/utils/test_api_file_utils.py -v`

Expected: PASS；现有 `sanitize_path()` 行为保持不变。

- [ ] **Step 5: 提交路径校验**

```bash
git add api/utils/file_utils.py test/unit_test/api/utils/test_knowledge_upload_path.py
git commit -m "feat: validate knowledge folder upload paths"
```

---

### Task 2: 文件夹上传时创建真实知识库目录树

**Files:**
- Modify: `api/apps/restful_apis/document_api.py`
- Modify: `api/db/services/file_service.py`
- Modify: `test/unit_test/api/db/services/test_file_service_upload_document.py`
- Modify: `test/testcases/test_web_api/test_document_app/test_upload_documents.py`

**Interfaces:**
- Consumes: Task 1 的 `normalize_knowledge_upload_path()`；multipart 字段 `relative_path`，每个 `file` 对应一个值。
- Produces: `FileService.upload_document(self, kb, file_objs, user_id, src="local", parent_path=None, parser_config_override=None, relative_paths: list[str] | None = None)`；`FileService.ensure_kb_folder_path(kb_folder_id: str, segments: list[str], tenant_id: str) -> File`。

- [ ] **Step 1: 写失败测试，证明目录链、中文和跨目录同名行为**

```python
from werkzeug.datastructures import MultiDict


def test_upload_document_places_files_in_relative_folders(upload_fixture):
    kb, files, created_folders, created_files = upload_fixture
    relative_paths = [
        "2、二级文件/制度文件/A.docx",
        "2、二级文件/表单/A.docx",
    ]

    err, uploaded = _unwrapped_upload_document()(
        FileService,
        kb,
        files,
        "tenant-1",
        relative_paths=relative_paths,
    )

    assert err == []
    assert [item[0]["name"] for item in uploaded] == ["A.docx", "A.docx"]
    assert {item[0]["location"] for item in uploaded} == {
        "2、二级文件/制度文件/A.docx",
        "2、二级文件/表单/A.docx",
    }
    assert created_folders == [
        ("kb-folder", "2、二级文件"),
        ("2、二级文件", "制度文件"),
        ("2、二级文件", "表单"),
    ]
    assert {item["parent_id"] for item in created_files} == {"制度文件", "表单"}


def test_local_upload_requires_one_relative_path_per_file(document_rest_api_module, monkeypatch):
    module = document_rest_api_module
    request = _DummyRequest(
        form=MultiDict([("relative_path", "folder/one.txt")]),
        files=_DummyFiles({
            "file": [_DummyFile("one.txt"), _DummyFile("two.txt")],
        }),
    )
    monkeypatch.setattr(module, "request", request)

    response = _run(module._upload_local_documents(kb, "tenant-1"))

    assert response["code"] == 101
    assert "relative_path" in response["message"]


def test_failed_file_removes_request_created_empty_folders(upload_fixture):
    kb, files, created_folders, created_files = upload_fixture
    files[0].read_error = RuntimeError("broken upload")
    err, uploaded = _unwrapped_upload_document()(
        FileService,
        kb,
        [files[0]],
        "tenant-1",
        relative_paths=["顶层/空目录/A.docx"],
    )
    assert uploaded == []
    assert err == ["A.docx: broken upload"]
    assert FileService.query(name="顶层", type=FileType.FOLDER.value) == []
```

- [ ] **Step 2: 运行目标测试并确认接口尚未实现**

Run: `uv run pytest test/unit_test/api/db/services/test_file_service_upload_document.py test/testcases/test_web_api/test_document_app/test_upload_documents.py -k "relative_folders or relative_path_per_file" -v`

Expected: FAIL；`relative_paths` 参数和逐文件路径计数校验尚不存在。

- [ ] **Step 3: 实现路径读取、目录链和叶子落位**

```python
relative_paths = form.getlist("relative_path")
if relative_paths and len(relative_paths) != len(file_objs):
    return get_error_argument_result("Each uploaded file must have one relative_path value.")
if not relative_paths:
    relative_paths = [""] * len(file_objs)
```

```python
@classmethod
def ensure_kb_folder_path(cls, kb_folder_id, segments, tenant_id):
    current = cls.get_by_id(kb_folder_id)[1]
    for segment in segments:
        matches = cls.query(parent_id=current.id, name=segment, type=FileType.FOLDER.value)
        if matches:
            current = matches[0]
            continue
        current = cls.insert({
            "id": get_uuid(),
            "parent_id": current.id,
            "tenant_id": tenant_id,
            "created_by": tenant_id,
            "name": segment,
            "location": "",
            "size": 0,
            "type": FileType.FOLDER.value,
            "source_type": FileSource.KNOWLEDGEBASE,
        })
    return current
```

上传循环按索引读取 `relative_paths[index]`，以 `FileService.query(parent_id=target_folder.id)` 为同名范围；`Document.location` 使用首次写入的完整相对存储键，后续移动不得改写。`parent_path` 保留给现有连接器，逐文件路径存在时优先使用 `relative_paths`。

`ensure_kb_folder_path()` 在 `DB.atomic()` 内按父目录和名称再次查询后创建，捕获并发唯一性冲突后重新读取同一个目录。为 `File(parent_id, name)` 增加迁移前必须先做数据重复审计；若现有数据不能直接建立唯一索引，则本任务使用事务内行锁保护目录创建，不修改全局表约束。记录本次请求实际新建的文件夹 ID；单文件失败后按从深到浅顺序删除没有任何子项的请求新建目录，其他成功文件正在使用的目录不得删除。

- [ ] **Step 4: 运行上传服务与路由回归**

Run: `uv run pytest test/unit_test/api/db/services/test_file_service_upload_document.py test/testcases/test_web_api/test_document_app/test_upload_documents.py -v`

Expected: PASS；单文件仍位于知识库根目录，跨目录同名文件不被全局改名。

- [ ] **Step 5: 提交目录化上传后端**

```bash
git add api/apps/restful_apis/document_api.py api/db/services/file_service.py test/unit_test/api/db/services/test_file_service_upload_document.py test/testcases/test_web_api/test_document_app/test_upload_documents.py
git commit -m "feat: preserve knowledge folder upload hierarchy"
```

---

### Task 3: 实现知识库分层列表、祖先路径和全局搜索路径

**Files:**
- Create: `api/apps/services/knowledge_file_service.py`
- Create: `test/unit_test/api/apps/services/test_knowledge_file_service.py`
- Modify: `api/db/services/document_service.py`

**Interfaces:**
- Consumes: `Knowledgebase`、当前文件夹 ID、分页/排序/筛选参数和关键词。
- Produces:
  - `KnowledgeFileService.get_kb_root(kb, tenant_id) -> File`
  - `KnowledgeFileService.list_entries(kb, tenant_id, *, parent_id, page, page_size, orderby, desc, keywords, filters) -> dict`
  - `KnowledgeFileService.get_ancestors(kb, tenant_id, folder_id) -> list[dict]`
  - 所有列表项包含 `entry_type`、`file_id`、`parent_id` 和 `relative_path`。

- [ ] **Step 1: 写失败测试，定义联合列表和搜索响应**

```python
def test_list_entries_returns_direct_folders_before_documents(service_fixture):
    kb, tenant_id, root, folder, nested_file, root_file = service_fixture
    result = KnowledgeFileService.list_entries(
        kb,
        tenant_id,
        parent_id=root.id,
        page=1,
        page_size=20,
        orderby="create_time",
        desc=True,
        keywords="",
        filters={},
    )
    assert [entry["entry_type"] for entry in result["entries"]] == ["folder", "document"]
    assert result["entries"][0]["id"] == folder.id
    assert result["entries"][1]["file_id"] == root_file.id
    assert result["total"] == 2


def test_global_search_includes_relative_path(service_fixture):
    kb, tenant_id, root, folder, nested_file, root_file = service_fixture
    result = KnowledgeFileService.list_entries(
        kb,
        tenant_id,
        parent_id=folder.id,
        page=1,
        page_size=20,
        orderby="create_time",
        desc=True,
        keywords="A.docx",
        filters={},
    )
    assert [entry["entry_type"] for entry in result["entries"]] == ["document"]
    assert result["entries"][0]["relative_path"] == "2、二级文件/制度文件/A.docx"


def test_rejects_folder_from_another_knowledge_base(service_fixture, other_kb_folder):
    kb, tenant_id, *_ = service_fixture
    with pytest.raises(PermissionError):
        KnowledgeFileService.list_entries(
            kb,
            tenant_id,
            parent_id=other_kb_folder.id,
            page=1,
            page_size=20,
            orderby="create_time",
            desc=True,
            keywords="",
            filters={},
        )
```

- [ ] **Step 2: 运行服务测试并确认模块不存在**

Run: `uv run pytest test/unit_test/api/apps/services/test_knowledge_file_service.py -v`

Expected: FAIL，错误包含 `No module named 'api.apps.services.knowledge_file_service'`。

- [ ] **Step 3: 实现归属校验、联合分页和路径构建**

```python
class KnowledgeFileService:
    @classmethod
    def assert_folder_in_kb(cls, kb, tenant_id, folder_id):
        root = cls.get_kb_root(kb, tenant_id)
        ok, folder = FileService.get_by_id(folder_id)
        if not ok or folder.tenant_id != tenant_id:
            raise PermissionError("Folder does not belong to this knowledge base.")
        ancestors = FileService.get_all_parent_folders(folder_id)
        if folder_id != root.id and root.id not in {item.id for item in ancestors}:
            raise PermissionError("Folder does not belong to this knowledge base.")
        return folder

    @classmethod
    def build_relative_path(cls, file_entry, root_id):
        ancestors = FileService.get_all_parent_folders(file_entry.id)
        names = [item.name for item in reversed(ancestors) if item.id != root_id]
        return "/".join([*names, file_entry.name])
```

无关键词时只查询当前目录直接子文件夹和直接子文档，以“文件夹在前、文档在后”做联合分页。有关键词时忽略 `parent_id` 范围，沿用 `DocumentService.get_by_kb_id()` 的全知识库筛选和分页，再批量补齐路径，禁止逐文档执行祖先 N+1 查询。响应结构固定为：

```python
{
    "entries": [
        {"entry_type": "folder", "id": "folder-id", "file_id": "folder-id", "parent_id": "root-id", "name": "制度文件", "type": "folder", "relative_path": "2、二级文件/制度文件", "has_child_folder": True},
        {"entry_type": "document", "id": "document-id", "file_id": "file-id", "parent_id": "folder-id", "name": "A.docx", "type": "docx", "relative_path": "2、二级文件/制度文件/A.docx", "status": "1", "run": "3", "chunk_count": 10},
    ],
    "parent_folder": {"id": "root-id", "name": "知识库名称"},
    "total": 2,
}
```

- [ ] **Step 4: 运行服务测试和现有文档列表测试**

Run: `uv run pytest test/unit_test/api/apps/services/test_knowledge_file_service.py test/testcases/restful_api/test_documents.py -k "list or search or knowledge_file" -v`

Expected: PASS；原有扁平文档接口未被破坏。

- [ ] **Step 5: 提交分层查询服务**

```bash
git add api/apps/services/knowledge_file_service.py api/db/services/document_service.py test/unit_test/api/apps/services/test_knowledge_file_service.py
git commit -m "feat: list knowledge folders and document paths"
```

---

### Task 4: 实现知识库文件夹新建、移动和重命名

**Files:**
- Modify: `api/apps/services/knowledge_file_service.py`
- Modify: `test/unit_test/api/apps/services/test_knowledge_file_service.py`
- Modify: `api/apps/services/document_api_service.py`

**Interfaces:**
- Consumes: 知识库、用户 ID、条目 ID、目标父目录 ID、新名称。
- Produces: `create_folder()`、`move_entries()`、`rename_entry()`。

- [ ] **Step 1: 写失败测试，锁定解析隔离和循环检查**

```python
def test_move_document_changes_only_file_parent(service_fixture, monkeypatch):
    kb, tenant_id, root, folder, nested_file, root_file = service_fixture
    before = snapshot_document(nested_file.document_id)
    storage_moves = []
    monkeypatch.setattr(settings.STORAGE_IMPL, "move", lambda *args: storage_moves.append(args))
    result = KnowledgeFileService.move_entries(kb, tenant_id, [nested_file.id], root.id)
    assert result == {"moved": 1}
    assert FileService.get_by_id(nested_file.id)[1].parent_id == root.id
    assert snapshot_document(nested_file.document_id) == before
    assert storage_moves == []


def test_move_folder_rejects_descendant_destination(service_fixture):
    kb, tenant_id, root, folder, nested_file, root_file = service_fixture
    child = make_folder(parent_id=folder.id, name="child")
    with pytest.raises(ValueError, match="own descendant"):
        KnowledgeFileService.move_entries(kb, tenant_id, [folder.id], child.id)


def test_rename_document_updates_index_without_reparse(service_fixture, monkeypatch):
    kb, tenant_id, root, folder, nested_file, root_file = service_fixture
    before = snapshot_document(nested_file.document_id)
    index_updates = []
    monkeypatch.setattr(settings.docStoreConn, "update", lambda *args: index_updates.append(args))
    KnowledgeFileService.rename_entry(kb, tenant_id, nested_file.id, "新名称.docx")
    after = snapshot_document(nested_file.document_id)
    assert after["name"] == "新名称.docx"
    assert after["location"] == before["location"]
    assert after["run"] == before["run"]
    assert after["chunk_num"] == before["chunk_num"]
    assert index_updates
```

- [ ] **Step 2: 运行 mutation 测试并确认失败**

Run: `uv run pytest test/unit_test/api/apps/services/test_knowledge_file_service.py -k "move or rename or create_folder" -v`

Expected: FAIL；知识库专用 mutation 尚不存在，不能改为调用通用 `move_files()` 让测试通过。

- [ ] **Step 3: 实现只改变组织层级的 mutation**

```python
@classmethod
def move_entries(cls, kb, tenant_id, entry_ids, destination_id):
    destination = cls.assert_folder_in_kb(kb, tenant_id, destination_id)
    entries = [cls.assert_entry_in_kb(kb, tenant_id, entry_id) for entry_id in entry_ids]
    cls.validate_move(entries, destination)
    with DB.atomic():
        for entry in entries:
            FileService.update_by_id(entry.id, {"parent_id": destination.id})
    return {"moved": len(entries)}


@classmethod
def rename_entry(cls, kb, tenant_id, entry_id, name):
    entry = cls.assert_entry_in_kb(kb, tenant_id, entry_id)
    cls.validate_sibling_name(entry.parent_id, name, exclude_id=entry.id)
    if entry.type == FileType.FOLDER.value:
        FileService.update_by_id(entry.id, {"name": name})
    else:
        document_id = File2DocumentService.get_by_file_id(entry.id)[0].document_id
        error = update_document_name_only(document_id, name)
        if error:
            raise RuntimeError(error["message"])
    return {"id": entry.id, "name": name}
```

`validate_move()` 在写入前完成目标归属、同级冲突、自身目标和后代目标检查；`create_folder()` 创建 `source_type=FileSource.KNOWLEDGEBASE` 的目录；文件重命名复用索引标题更新，但不得调用解析重置函数。

- [ ] **Step 4: 运行 mutation 与文档重命名回归**

Run: `uv run pytest test/unit_test/api/apps/services/test_knowledge_file_service.py test/testcases/test_web_api/test_document_app -k "rename or move or create_folder" -v`

Expected: PASS；存储 `move()` 调用次数为零，解析快照保持不变。

- [ ] **Step 5: 提交目录创建、移动和重命名**

```bash
git add api/apps/services/knowledge_file_service.py api/apps/services/document_api_service.py test/unit_test/api/apps/services/test_knowledge_file_service.py
git commit -m "feat: manage knowledge folder organization"
```

---

### Task 5: 实现知识库文件夹递归删除和部分失败报告

**Files:**
- Modify: `api/apps/services/knowledge_file_service.py`
- Modify: `test/unit_test/api/apps/services/test_knowledge_file_service.py`
- Modify: `api/db/services/file_service.py`

**Interfaces:**
- Consumes: 已通过知识库归属预检的文件或文件夹 ID。
- Produces: `count_descendant_documents(kb, tenant_id, entry_ids: list[str]) -> int`；`delete_entries(kb, tenant_id, entry_ids: list[str]) -> {"deleted": int, "failed": list[dict]}`。

- [ ] **Step 1: 写失败测试，覆盖预检、递归顺序和重试**

```python
def test_delete_folder_removes_documents_before_folders(service_fixture, monkeypatch):
    kb, tenant_id, root, folder, nested_file, root_file = service_fixture
    removed_docs, removed_folders = [], []
    monkeypatch.setattr(FileService, "delete_docs", lambda ids, uid: removed_docs.extend(ids) or "")
    monkeypatch.setattr(FileService, "delete_by_id", lambda entry_id: removed_folders.append(entry_id))
    result = KnowledgeFileService.delete_entries(kb, tenant_id, [folder.id])
    assert result == {"deleted": 2, "failed": []}
    assert removed_docs == [nested_file.document_id]
    assert removed_folders[-1] == folder.id


def test_delete_preflight_failure_makes_no_changes(service_fixture, other_kb_folder, monkeypatch):
    kb, tenant_id, *_ = service_fixture
    deletes = []
    monkeypatch.setattr(FileService, "delete_by_id", lambda entry_id: deletes.append(entry_id))
    with pytest.raises(PermissionError):
        KnowledgeFileService.delete_entries(kb, tenant_id, [other_kb_folder.id])
    assert deletes == []


def test_delete_reports_external_failure(service_fixture, monkeypatch):
    kb, tenant_id, root, folder, nested_file, root_file = service_fixture
    monkeypatch.setattr(FileService, "delete_docs", lambda ids, uid: "storage unavailable")
    result = KnowledgeFileService.delete_entries(kb, tenant_id, [folder.id])
    assert result["failed"][0]["path"].endswith("A.docx")
    assert result["failed"][0]["message"] == "storage unavailable"
```

- [ ] **Step 2: 运行删除测试并确认失败**

Run: `uv run pytest test/unit_test/api/apps/services/test_knowledge_file_service.py -k "delete or descendant" -v`

Expected: FAIL；递归删除和结构化失败尚不存在。

- [ ] **Step 3: 实现先预检、后文档、最后文件夹的删除流程**

```python
@classmethod
def delete_entries(cls, kb, tenant_id, entry_ids):
    roots = [cls.assert_entry_in_kb(kb, tenant_id, entry_id) for entry_id in entry_ids]
    files, folders = cls.collect_descendants_postorder(roots)
    failed, deleted = [], 0
    root_id = cls.get_kb_root(kb, tenant_id).id
    for file_entry in files:
        links = File2DocumentService.get_by_file_id(file_entry.id)
        error = FileService.delete_docs([links[0].document_id], tenant_id) if links else ""
        if error:
            failed.append({"id": file_entry.id, "path": cls.build_relative_path(file_entry, root_id), "message": error})
        else:
            if not links:
                FileService.delete_by_id(file_entry.id)
            deleted += 1
    for folder in folders:
        if not FileService.list_all_files_by_parent_id(folder.id):
            FileService.delete_by_id(folder.id)
            deleted += 1
    return {"deleted": deleted, "failed": failed}
```

`collect_descendants_postorder()` 去重重叠选择并保证子项在父目录之前。已不存在的叶子/关联按成功处理，保证重试安全。`FileService.delete_docs()` 继续负责任务、分块、元数据、缩略图、存储对象和关联清理。

- [ ] **Step 4: 运行删除服务和现有文档删除回归**

Run: `uv run pytest test/unit_test/api/apps/services/test_knowledge_file_service.py test/testcases/restful_api/test_documents.py -k "delete" -v`

Expected: PASS；权限预检失败没有写操作，部分失败包含路径。

- [ ] **Step 5: 提交递归删除**

```bash
git add api/apps/services/knowledge_file_service.py api/db/services/file_service.py test/unit_test/api/apps/services/test_knowledge_file_service.py
git commit -m "feat: recursively delete knowledge folders"
```

---

### Task 6: 暴露 REST API 并接入前端数据层

**Files:**
- Create: `api/apps/restful_apis/knowledge_file_api.py`
- Create: `test/testcases/test_web_api/test_document_app/test_knowledge_file_routes_unit.py`
- Create: `web/src/interfaces/database/knowledge-file.ts`
- Create: `web/src/hooks/use-knowledge-file-request.ts`
- Create: `web/src/hooks/__tests__/use-knowledge-file-request.test.tsx`
- Modify: `web/src/utils/api.ts`
- Modify: `web/src/services/knowledge-service.ts`

**Interfaces:**
- Consumes: Task 3–5 的 `KnowledgeFileService`。
- Produces:
  - `GET /api/v1/datasets/:datasetId/entries`
  - `GET /api/v1/datasets/:datasetId/folders/:folderId/ancestors`
  - `POST /api/v1/datasets/:datasetId/folders`
  - `PATCH /api/v1/datasets/:datasetId/entries/:entryId`
  - `POST /api/v1/datasets/:datasetId/entries/move`
  - `POST /api/v1/datasets/:datasetId/entries/delete-preview`
  - `DELETE /api/v1/datasets/:datasetId/entries`

- [ ] **Step 1: 写失败的路由契约测试和前端 hook 测试**

```python
def test_list_entries_passes_validated_query(knowledge_file_api_module, monkeypatch):
    module = knowledge_file_api_module
    captured = {}
    monkeypatch.setattr(module.KnowledgebaseService, "get_by_id", lambda kb_id: (True, kb))
    monkeypatch.setattr(module, "check_kb_team_permission", lambda kb, tenant_id: True)
    monkeypatch.setattr(
        module.KnowledgeFileService,
        "list_entries",
        lambda kb, tenant_id, **kwargs: captured.update(kwargs) or {"entries": [], "total": 0},
    )
    response = _run(module.list_entries("kb-1", "tenant-1"))
    assert response["code"] == 0
    assert captured["parent_id"] == "folder-1"
    assert captured["keywords"] == "审批"


def test_delete_entries_returns_partial_failures(knowledge_file_api_module, monkeypatch):
    module = knowledge_file_api_module
    monkeypatch.setattr(
        module.KnowledgeFileService,
        "delete_entries",
        lambda *args: {"deleted": 2, "failed": [{"id": "f3", "path": "目录/A.docx", "message": "storage unavailable"}]},
    )
    response = _run(module.delete_entries("kb-1", "tenant-1"))
    assert response["code"] == 500
    assert response["data"]["deleted"] == 2
    assert response["data"]["failed"][0]["path"] == "目录/A.docx"
```

前端 hook 测试 mock `knowledge-service`，断言 query key 包含 `datasetId`、`folderId`、搜索词、筛选和分页；mutation 成功后失效目录列表及现有文档列表缓存。

- [ ] **Step 2: 运行两组测试并确认模块不存在**

Run: `uv run pytest test/testcases/test_web_api/test_document_app/test_knowledge_file_routes_unit.py -v`

Run: `cd web && npm test -- --runInBand src/hooks/__tests__/use-knowledge-file-request.test.tsx`

Expected: FAIL；后端路由和前端 hook 尚不存在。

- [ ] **Step 3: 实现薄路由、联合类型、客户端和 hooks**

```typescript
export interface KnowledgeFolderEntry {
  entry_type: 'folder';
  id: string;
  file_id: string;
  parent_id: string;
  name: string;
  type: 'folder';
  relative_path: string;
  has_child_folder: boolean;
  create_time: number;
  update_time: number;
}

export type KnowledgeDocumentEntry = IDocumentInfo & {
  entry_type: 'document';
  file_id: string;
  parent_id: string;
  relative_path: string;
};

export type KnowledgeEntry = KnowledgeFolderEntry | KnowledgeDocumentEntry;
```

```typescript
export const listKnowledgeEntries = (datasetId: string, params: Record<string, unknown>) =>
  request.get(api.knowledgeEntries(datasetId), { params });

export const createKnowledgeFolder = (datasetId: string, data: { parent_id: string; name: string }) =>
  request.post(api.knowledgeFolders(datasetId), { data });

export const moveKnowledgeEntries = (datasetId: string, data: { ids: string[]; destination_id: string }) =>
  request.post(api.knowledgeEntryMove(datasetId), { data });

export const renameKnowledgeEntry = (datasetId: string, entryId: string, name: string) =>
  request.patch(api.knowledgeEntry(datasetId, entryId), { data: { name } });

export const deleteKnowledgeEntries = (datasetId: string, ids: string[]) =>
  request.delete(api.knowledgeEntries(datasetId), { data: { ids } });
```

每个后端路由统一读取知识库、执行 `check_kb_team_permission()`、验证参数再调用服务。`PermissionError` 返回归属/权限错误，`ValueError` 返回参数错误；删除部分失败返回 `code=500`，同时保留 `data.deleted` 和 `data.failed`。

- [ ] **Step 4: 运行路由、hook、类型检查和 lint**

Run: `uv run pytest test/testcases/test_web_api/test_document_app/test_knowledge_file_routes_unit.py -v`

Run: `cd web && npm test -- --runInBand src/hooks/__tests__/use-knowledge-file-request.test.tsx && npm run type-check && npm run lint`

Expected: PASS；无 TypeScript、ESLint 或路由导入错误。

- [ ] **Step 5: 提交 API 与前端数据层**

```bash
git add api/apps/restful_apis/knowledge_file_api.py test/testcases/test_web_api/test_document_app/test_knowledge_file_routes_unit.py web/src/interfaces/database/knowledge-file.ts web/src/hooks/use-knowledge-file-request.ts web/src/hooks/__tests__/use-knowledge-file-request.test.tsx web/src/utils/api.ts web/src/services/knowledge-service.ts
git commit -m "feat: expose knowledge folder management API"
```

---

### Task 7: 前端逐文件提交相对路径并显示上传路径

**Files:**
- Create: `web/src/utils/knowledge-file-upload.ts`
- Create: `web/src/utils/__tests__/knowledge-file-upload.test.ts`
- Modify: `web/src/hooks/use-document-request.ts`
- Modify: `web/src/components/file-uploader.tsx`
- Modify: `web/src/components/file-upload-dialog/index.tsx`

**Interfaces:**
- Consumes: 浏览器 `File[]` 和 `webkitRelativePath`。
- Produces: `buildKnowledgeUploadFormData(files, parserConfig?) -> FormData`；每个 `file` 对应一个 `relative_path`。

- [ ] **Step 1: 写失败测试，验证顺序和单文件兼容**

```typescript
import { buildKnowledgeUploadFormData, getUploadDisplayPath } from '../knowledge-file-upload';

function folderFile(name: string, relativePath: string) {
  const file = new File(['content'], name, { type: 'text/plain' });
  Object.defineProperty(file, 'webkitRelativePath', { value: relativePath });
  return file;
}

test('appends one ordered relative_path for every file', () => {
  const files = [
    folderFile('A.txt', '中文目录/制度/A.txt'),
    folderFile('B.txt', '中文目录/表单/B.txt'),
  ];
  const formData = buildKnowledgeUploadFormData(files);
  expect(formData.getAll('file')).toEqual(files);
  expect(formData.getAll('relative_path')).toEqual([
    '中文目录/制度/A.txt',
    '中文目录/表单/B.txt',
  ]);
});

test('uses an empty relative path for a normal file', () => {
  const file = new File(['content'], 'single.txt');
  expect(buildKnowledgeUploadFormData([file]).getAll('relative_path')).toEqual(['']);
});

test('shows relative path in preview', () => {
  expect(getUploadDisplayPath(folderFile('A.txt', '中文目录/制度/A.txt'))).toBe('中文目录/制度/A.txt');
});
```

- [ ] **Step 2: 运行工具测试并确认模块不存在**

Run: `cd web && npm test -- --runInBand src/utils/__tests__/knowledge-file-upload.test.ts`

Expected: FAIL，错误包含 `Cannot find module '../knowledge-file-upload'`。

- [ ] **Step 3: 实现构建器并接入上传 hook/预览**

```typescript
export type FolderUploadFile = File & { webkitRelativePath?: string };

export function getUploadDisplayPath(file: File): string {
  return (file as FolderUploadFile).webkitRelativePath || file.name;
}

export function buildKnowledgeUploadFormData(
  files: File[],
  parserConfig?: Record<string, unknown>,
): FormData {
  const formData = new FormData();
  files.forEach((file) => {
    formData.append('file', file, file.name);
    formData.append('relative_path', (file as FolderUploadFile).webkitRelativePath || '');
  });
  if (parserConfig) formData.append('parser_config', JSON.stringify(parserConfig));
  return formData;
}
```

`useUploadDocument()` 改用该函数。`FileCard` 的名称、tooltip 和进度键使用 `getUploadDisplayPath(file)`，避免同名文件混淆。`UploadFormSchemaType.fileList` 收敛为 `File[]`，删除未实际使用的 `{file, path}` 联合分支。

- [ ] **Step 4: 运行测试、类型检查和目标 lint**

Run: `cd web && npm test -- --runInBand src/utils/__tests__/knowledge-file-upload.test.ts && npm run type-check && npx eslint src/utils/knowledge-file-upload.ts src/hooks/use-document-request.ts src/components/file-uploader.tsx src/components/file-upload-dialog/index.tsx`

Expected: PASS；预览显示完整路径，普通文件提交空路径占位。

- [ ] **Step 5: 提交前端相对路径上传**

```bash
git add web/src/utils/knowledge-file-upload.ts web/src/utils/__tests__/knowledge-file-upload.test.ts web/src/hooks/use-document-request.ts web/src/components/file-uploader.tsx web/src/components/file-upload-dialog/index.tsx
git commit -m "feat: submit knowledge upload relative paths"
```

---

### Task 8: 实现知识库文件管理器界面并完成端到端回归

**Files:**
- Create: `web/src/pages/dataset/dataset/knowledge-file-browser.tsx`
- Create: `web/src/pages/dataset/dataset/knowledge-file-breadcrumb.tsx`
- Create: `web/src/pages/dataset/dataset/knowledge-entry-action-cell.tsx`
- Create: `web/src/pages/dataset/dataset/knowledge-move-dialog.tsx`
- Create: `web/src/pages/dataset/dataset/__tests__/knowledge-file-browser.test.tsx`
- Create: `test/testcases/restful_api/test_knowledge_folder_workflow.py`
- Modify: `web/src/pages/dataset/dataset/index.tsx`
- Modify: `web/src/pages/dataset/dataset/dataset-table.tsx`
- Modify: `web/src/pages/dataset/dataset/use-dataset-table-columns.tsx`
- Modify: `web/src/pages/dataset/dataset/use-bulk-operate-dataset.tsx`
- Modify: `web/src/locales/zh.ts`
- Modify: `web/src/locales/en.ts`

**Interfaces:**
- Consumes: Task 6 的 hooks/联合类型和 Task 7 的目录化上传结果。
- Produces: 文件夹优先列表、面包屑、全局搜索路径、目录操作和只含移动/删除的文件夹批量栏。

- [ ] **Step 1: 写失败的组件测试和后端工作流测试**

```typescript
test('renders document controls only for document rows', async () => {
  mockEntryQuery([
    folderEntry({ id: 'folder-1', name: '制度文件' }),
    documentEntry({ id: 'doc-1', name: 'A.docx' }),
  ]);
  render(<KnowledgeFileBrowser />);
  expect(screen.getByText('制度文件')).toBeInTheDocument();
  expect(screen.getByText('A.docx')).toBeInTheDocument();
  expect(screen.getAllByRole('switch')).toHaveLength(1);
  await userEvent.click(screen.getByText('制度文件'));
  expect(mockNavigateFolder).toHaveBeenCalledWith('folder-1');
});

test('global search shows path and clearing restores folder', async () => {
  render(<KnowledgeFileBrowser />);
  await userEvent.click(screen.getByText('制度文件'));
  await userEvent.type(screen.getByRole('searchbox'), '审批');
  expect(await screen.findByText('2、二级文件/制度文件/A.docx')).toBeInTheDocument();
  await userEvent.clear(screen.getByRole('searchbox'));
  expect(mockEntryQuery).toHaveBeenLastCalledWith(expect.objectContaining({ folderId: 'folder-1', keywords: '' }));
});

test('folder selection exposes only move and delete', async () => {
  mockEntryQuery([folderEntry({ id: 'folder-1', name: '制度文件' })]);
  render(<KnowledgeFileBrowser />);
  await userEvent.click(screen.getByLabelText('Select row'));
  expect(screen.getByText('移动')).toBeInTheDocument();
  expect(screen.getByText('删除')).toBeInTheDocument();
  expect(screen.queryByText('解析')).not.toBeInTheDocument();
  expect(screen.queryByText('元数据')).not.toBeInTheDocument();
});
```

REST 工作流测试依次执行：上传两条不同分支的同名文档、列目录、启动解析、移动/重命名父目录、下载/预览、递归删除。固定断言：

```python
assert root_entries[0]["entry_type"] == "folder"
assert root_entries[0]["name"] == "2、二级文件"
assert {entry["name"] for entry in child_entries} == {"制度文件", "表单"}
assert document_before["location"] == document_after["location"]
assert document_before["chunk_count"] == document_after["chunk_count"]
assert download_response.status_code == 200
assert delete_response["data"]["failed"] == []
```

- [ ] **Step 2: 运行组件和工作流测试并确认失败**

Run: `cd web && npm test -- --runInBand src/pages/dataset/dataset/__tests__/knowledge-file-browser.test.tsx`

Run: `uv run pytest test/testcases/restful_api/test_knowledge_folder_workflow.py -v`

Expected: FAIL；浏览器组件和完整目录流程尚未接入。

- [ ] **Step 3: 实现文件管理器式知识库页面**

```typescript
export function KnowledgeFileBrowser() {
  const [folderId, setFolderId] = useState('');
  const [folderBeforeSearch, setFolderBeforeSearch] = useState('');
  const [keywords, setKeywords] = useState('');
  const [rowSelection, setRowSelection] = useState<RowSelectionState>({});
  const onSearchChange = (value: string) => {
    if (value && !keywords) setFolderBeforeSearch(folderId);
    setKeywords(value);
    if (!value) setFolderId(folderBeforeSearch);
  };
  const query = useKnowledgeEntries({ folderId, keywords });
  return (
    <>
      <KnowledgeFileBreadcrumb folderId={folderId} onNavigate={setFolderId} />
      <DatasetTable
        entries={query.entries}
        rowSelection={rowSelection}
        setRowSelection={setRowSelection}
        onOpenFolder={setFolderId}
        onLocateSearchResult={(entry) => setFolderId(entry.parent_id)}
      />
    </>
  );
}
```

联合表格名称列根据 `entry_type` 打开文件夹或文档；所有文档专属列遇到文件夹返回 `null`。`KnowledgeMoveDialog` 使用知识库目录 API 加载 `AsyncTreeSelect`，排除自身和后代目标。删除前请求后代文档数，确认框明确数量；部分失败逐条显示路径并刷新列表。

新增中英文同结构文案键：

```typescript
knowledgeFolder: {
  newFolder: '新建文件夹',
  deleteConfirmWithCount: '将删除该文件夹及其中的 {{count}} 个文档，此操作不可撤销。',
  partialDeleteFailed: '已删除 {{deleted}} 项，{{failed}} 项删除失败。',
  path: '路径',
}
```

- [ ] **Step 4: 运行完整自动验证**

Run: `uv run pytest test/unit_test/api/utils/test_knowledge_upload_path.py test/unit_test/api/db/services/test_file_service_upload_document.py test/unit_test/api/apps/services/test_knowledge_file_service.py test/testcases/test_web_api/test_document_app/test_knowledge_file_routes_unit.py test/testcases/restful_api/test_knowledge_folder_workflow.py -v`

Run: `cd web && npm test -- --runInBand src/utils/__tests__/knowledge-file-upload.test.ts src/hooks/__tests__/use-knowledge-file-request.test.tsx src/pages/dataset/dataset/__tests__/knowledge-file-browser.test.tsx`

Run: `cd web && npm run type-check && npm run lint`

Run: `ruff check api/utils/file_utils.py api/db/services/file_service.py api/db/services/document_service.py api/apps/services/knowledge_file_service.py api/apps/restful_apis/document_api.py api/apps/restful_apis/knowledge_file_api.py`

Expected: 全部 PASS；无类型、lint 或 ruff 错误。

- [ ] **Step 5: 手工冒烟验证关键路径**

按 `AGENTS.md` 启动依赖、后端和 `cd web && npm run dev`，依次验证：中文多级目录上传预览；顶层文件夹展示；创建时解析；目录移动/重命名后解析状态、分块、预览和下载不变；全局搜索显示路径并定位；新建目录和移动文件；文件夹批量栏只有移动/删除；递归删除显示文档数；普通单文件仍在根目录。

- [ ] **Step 6: 提交 UI 与端到端测试**

```bash
git add web/src/pages/dataset/dataset/knowledge-file-browser.tsx web/src/pages/dataset/dataset/knowledge-file-breadcrumb.tsx web/src/pages/dataset/dataset/knowledge-entry-action-cell.tsx web/src/pages/dataset/dataset/knowledge-move-dialog.tsx web/src/pages/dataset/dataset/__tests__/knowledge-file-browser.test.tsx web/src/pages/dataset/dataset/index.tsx web/src/pages/dataset/dataset/dataset-table.tsx web/src/pages/dataset/dataset/use-dataset-table-columns.tsx web/src/pages/dataset/dataset/use-bulk-operate-dataset.tsx web/src/locales/zh.ts web/src/locales/en.ts test/testcases/restful_api/test_knowledge_folder_workflow.py
git commit -m "feat: add knowledge base file browser"
```

---

## 最终验收

完成八个任务后再次执行相关后端测试、三个新增前端测试、`npm run type-check`、`npm run lint` 和目标 Python `ruff check`。最终必须同时满足：目录结构正确、中文路径不丢失、单文件兼容、目录操作不触发解析、搜索显示路径、删除如实报告部分失败、全部目标测试通过。

## 规格覆盖映射

- 文件夹上传、Unicode 路径与路径安全：Task 1、2、7。
- 文件管理器式层级、面包屑与历史扁平兼容：Task 3、6、8。
- 新建、重命名、移动、同级冲突和目录循环校验：Task 4、6、8。
- 解析/存储隔离与文件标题索引同步：Task 4、8。
- 递归删除、后代数量、预检、部分失败和幂等重试：Task 5、6、8。
- 全知识库搜索、路径展示和结果定位：Task 3、6、8。
- 文件夹批量栏只显示移动/删除：Task 8。
- 自动化与手工端到端验收：所有任务的 RED/GREEN 步骤及 Task 8。
