# 知识库分类实施计划

> **面向智能体执行者：** 必须使用 `superpowers:subagent-driven-development`（推荐）或 `superpowers:executing-plans`，严格按任务逐项执行。所有步骤使用复选框（`- [ ]`）跟踪。

**目标：** 为知识库列表增加团队共享、手动维护、单归属的分类能力，并以左侧固定分类栏支持服务端筛选、分类管理和知识库移动。

**架构：** 后端新增独立的 `knowledgebase_category` 表，并给 `knowledgebase` 增加可空的 `category_id`；分类业务集中在独立服务中，现有知识库服务只增加创建、更新、序列化和列表过滤所需的最小改动。前端用独立 React Query hook 管理分类查询和变更，用可复用的分类选择器连接左侧栏、知识库卡片菜单和创建弹窗。

**技术栈：** Python 3.10+、Quart、Peewee、Pydantic、pytest；React 18、TypeScript、React Query、React Router、React Hook Form、Zod、Radix UI、Jest、Testing Library。

## 全局约束

- 一个知识库最多属于一个真实分类；`category_id = NULL` 表示“未分类”。
- 分类属于知识库所有者的租户/团队，同团队分类名称去除首尾空格后不允许重复，最长 128 个字符。
- 分类对能访问该团队知识库的成员可见；写操作沿用现有知识库管理权限，当前实现即仅所有者租户可修改。
- 删除分类必须在同一数据库事务中清空成员知识库的 `category_id`，不得删除知识库。
- 只做增量兼容改造：新增一张表、一个可空字段和可选 API 属性，不改变已有字段语义。
- 原有客户端不传 `category_id` 时行为必须保持不变。
- “全部知识库”和“未分类”是虚拟项，不能重命名或删除。
- 分类筛选、负责人筛选、搜索和分页必须在服务端组合生效；切换分类时页码重置为 1。
- 不实现多级分类、多分类归属、自动分类、批量移动、分类颜色或分类图标。

---

## 文件结构与职责

### 后端新增

- `api/db/services/knowledgebase_category_service.py`：分类查询、计数、名称唯一性、权限校验、删除事务。
- `test/unit_test/api/db/services/test_knowledgebase_category_service.py`：分类服务的隔离单元测试。
- `test/unit_test/api/apps/services/test_dataset_categories.py`：分类应用服务与知识库分类赋值的业务测试。
- `test/testcases/test_http_api/test_dataset_management/test_categories.py`：真实 HTTP 合同和兼容性测试。

### 后端修改

- `api/db/db_models.py`：新增 `KnowledgebaseCategory` 模型、唯一索引、`Knowledgebase.category_id` 和启动迁移。
- `api/utils/validation_utils.py`：分类请求模型及知识库 `category_id` 校验。
- `api/apps/services/dataset_api_service.py`：分类 CRUD 应用服务；知识库创建、更新、列表的分类集成。
- `api/apps/restful_apis/dataset_api.py`：分类路由与错误映射。
- `api/db/services/knowledgebase_service.py`：列表查询选择并过滤 `category_id`。
- `test/unit_test/api/utils/test_doc_validation.py`：分类名称和 ID 请求校验。
- `test/testcases/test_http_api/common.py`：分类 HTTP 测试助手。
- `test/testcases/test_http_api/test_dataset_management/test_create_dataset.py`：创建时分类赋值和旧请求兼容。
- `test/testcases/test_http_api/test_dataset_management/test_update_dataset.py`：移动与移入未分类。
- `test/testcases/test_http_api/test_dataset_management/test_list_datasets.py`：组合过滤和分页。

### 前端新增

- `web/src/pages/datasets/category-constants.ts`：URL 虚拟值和筛选映射函数。
- `web/src/pages/datasets/use-dataset-categories.ts`：分类 React Query 查询、创建、重命名、删除、移动 mutation。
- `web/src/pages/datasets/dataset-category-sidebar.tsx`：左侧分类导航与管理入口。
- `web/src/pages/datasets/dataset-category-dialog.tsx`：新建/重命名分类表单。
- `web/src/pages/datasets/dataset-category-picker.tsx`：创建弹窗与卡片菜单共用的分类选择器数据模型。
- `web/src/pages/datasets/__tests__/category-constants.test.ts`：URL/请求映射单元测试。
- `web/src/pages/datasets/__tests__/dataset-category-sidebar.test.tsx`：侧栏交互测试。
- `web/src/pages/datasets/__tests__/dataset-category-picker.test.tsx`：选择与移动测试。

### 前端修改

- `web/src/interfaces/database/dataset.ts`：分类 DTO 与 `IDataset.category_id`。
- `web/src/interfaces/request/knowledge.ts`：分类筛选和创建/更新请求类型。
- `web/src/utils/api.ts`：分类端点。
- `web/src/services/knowledge-service.ts`：分类请求函数。
- `web/src/hooks/use-knowledge-request.ts`：知识库列表接收分类筛选。
- `web/src/pages/datasets/index.tsx`：方案 1 的双栏布局和选中分类标题。
- `web/src/pages/datasets/dataset-dropdown.tsx`：增加“移动到分类”子菜单。
- `web/src/pages/datasets/dataset-card.tsx`：向下传递分类数据和移动回调。
- `web/src/pages/datasets/dataset-creating-dialog.tsx`：可选分类字段。
- `web/src/pages/datasets/hooks.ts`：创建知识库时传递 `category_id`。
- `web/src/locales/zh.ts`、`web/src/locales/en.ts`：分类相关文案。

---

### 任务 1：添加分类数据模型和请求校验

**文件：**

- 修改：`api/db/db_models.py`
- 修改：`api/utils/validation_utils.py`
- 创建：`test/unit_test/api/db/test_knowledgebase_category_model.py`
- 修改：`test/unit_test/api/utils/test_doc_validation.py`

**接口：**

- 产出：`KnowledgebaseCategory` 模型。
- 产出：`Knowledgebase.category_id: str | None`。
- 产出：`CreateDatasetCategoryReq`、`UpdateDatasetCategoryReq`。
- 产出：`CreateDatasetReq.category_id: str | None`，并由 `UpdateDatasetReq` 继承。

- [ ] **步骤 1：先写模型和校验失败测试**

在模型测试中锁定表名、唯一索引和可空字段：

```python
from api.db.db_models import Knowledgebase, KnowledgebaseCategory


def test_knowledgebase_category_schema_contract():
    assert KnowledgebaseCategory._meta.table_name == "knowledgebase_category"
    assert (("tenant_id", "name"), True) in KnowledgebaseCategory._meta.indexes
    assert Knowledgebase.category_id.null is True
    assert Knowledgebase.category_id.index is True
```

在校验测试中锁定名称和 UUID1 hex：

```python
import uuid

import pytest
from pydantic import ValidationError

from api.utils.validation_utils import CreateDatasetCategoryReq, CreateDatasetReq


def test_category_name_is_trimmed_and_limited():
    assert CreateDatasetCategoryReq(name="  财务  ").name == "财务"
    with pytest.raises(ValidationError):
        CreateDatasetCategoryReq(name=" ")
    with pytest.raises(ValidationError):
        CreateDatasetCategoryReq(name="a" * 129)


def test_dataset_category_id_accepts_none_or_uuid1_hex():
    category_id = uuid.uuid1().hex
    assert CreateDatasetReq(name="kb", category_id=category_id).category_id == category_id
    assert CreateDatasetReq(name="kb", category_id=None).category_id is None
    with pytest.raises(ValidationError):
        CreateDatasetReq(name="kb", category_id="not-a-uuid")
```

- [ ] **步骤 2：运行测试并确认失败原因正确**

运行：

```bash
uv run pytest test/unit_test/api/db/test_knowledgebase_category_model.py test/unit_test/api/utils/test_doc_validation.py -q
```

预期：因 `KnowledgebaseCategory`、`category_id` 和分类请求模型尚不存在而失败。

- [ ] **步骤 3：实现最小数据模型和校验**

在 `Knowledgebase` 前增加模型，并在 `Knowledgebase` 中增加字段：

```python
class KnowledgebaseCategory(DataBaseModel):
    id = CharField(max_length=32, primary_key=True)
    tenant_id = CharField(max_length=32, null=False, index=True)
    name = CharField(max_length=128, null=False)
    created_by = CharField(max_length=32, null=False, index=True)
    status = CharField(max_length=1, null=False, default="1", index=True)

    class Meta:
        db_table = "knowledgebase_category"
        indexes = ((('tenant_id', 'name'), True),)


class Knowledgebase(DataBaseModel):
    # 保留现有字段
    category_id = CharField(max_length=32, null=True, index=True)
```

在 `migrate_db()` 中加入幂等增列：

```python
alter_db_add_column(
    migrator,
    "knowledgebase",
    "category_id",
    CharField(max_length=32, null=True, index=True),
)
```

在校验模块中增加：

```python
class CreateDatasetCategoryReq(Base):
    name: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=128),
    ]


class UpdateDatasetCategoryReq(CreateDatasetCategoryReq):
    category_id: Annotated[str, Field(...)]

    @field_validator("category_id", mode="before")
    @classmethod
    def validate_category_id(cls, value: Any) -> str:
        return validate_uuid1_hex(value)
```

给 `CreateDatasetReq` 添加可选 `category_id`，使用同样的 UUID1 校验；`None` 必须原样保留。

- [ ] **步骤 4：运行定向测试和格式检查**

运行：

```bash
uv run pytest test/unit_test/api/db/test_knowledgebase_category_model.py test/unit_test/api/utils/test_doc_validation.py -q
uv run ruff check api/db/db_models.py api/utils/validation_utils.py test/unit_test/api/db/test_knowledgebase_category_model.py test/unit_test/api/utils/test_doc_validation.py
```

预期：全部通过。

- [ ] **步骤 5：提交数据模型**

```bash
git add api/db/db_models.py api/utils/validation_utils.py test/unit_test/api/db/test_knowledgebase_category_model.py test/unit_test/api/utils/test_doc_validation.py
git commit -m "feat: add knowledge base category schema"
```

---

### 任务 2：实现分类数据库服务

**文件：**

- 创建：`api/db/services/knowledgebase_category_service.py`
- 创建：`test/unit_test/api/db/services/test_knowledgebase_category_service.py`

**接口：**

- 消费：任务 1 的 `KnowledgebaseCategory`、`Knowledgebase.category_id`。
- 产出：`KnowledgebaseCategoryService.visible_tenant_ids(user_id)`。
- 产出：`KnowledgebaseCategoryService.list_with_counts(user_id, owner_ids)`。
- 产出：`KnowledgebaseCategoryService.name_exists(tenant_id, name, exclude_id=None)`。
- 产出：`KnowledgebaseCategoryService.delete_and_unassign(category_id, tenant_id)`。

- [ ] **步骤 1：编写可见范围、唯一性和删除事务测试**

使用 monkeypatch 隔离数据库表达式，重点验证业务调用顺序：

```python
from contextlib import nullcontext

from api.db.services.knowledgebase_category_service import KnowledgebaseCategoryService


def test_visible_tenant_ids_include_owner_and_joined_tenants(monkeypatch):
    monkeypatch.setattr(
        "api.db.services.knowledgebase_category_service.TenantService.get_joined_tenants_by_user_id",
        lambda _user_id: [{"tenant_id": "team-a"}, {"tenant_id": "team-b"}],
    )
    assert KnowledgebaseCategoryService.visible_tenant_ids("me") == ["me", "team-a", "team-b"]


def test_delete_and_unassign_updates_before_delete(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "api.db.services.knowledgebase_category_service.DB.atomic",
        lambda: nullcontext(),
    )
    monkeypatch.setattr(
        "api.db.services.knowledgebase_category_service.KnowledgebaseService.filter_update",
        lambda _filters, data: calls.append(("unassign", data)) or 2,
    )
    monkeypatch.setattr(
        KnowledgebaseCategoryService,
        "delete_by_id",
        classmethod(lambda cls, category_id: calls.append(("delete", category_id)) or 1),
    )

    assert KnowledgebaseCategoryService.delete_and_unassign("cat-1", "team-a") == 2
    assert calls == [("unassign", {"category_id": None}), ("delete", "cat-1")]
```

另加测试锁定：负责人过滤只能缩小可见租户集合；名称比较使用 `LOWER(name)`；计数只统计 `status=VALID` 且符合知识库可见性规则的记录。

- [ ] **步骤 2：运行服务测试确认失败**

```bash
uv run pytest test/unit_test/api/db/services/test_knowledgebase_category_service.py -q
```

预期：导入新服务失败。

- [ ] **步骤 3：实现分类数据库服务**

服务继承 `CommonService`，对外保持清晰边界：

```python
class KnowledgebaseCategoryService(CommonService):
    model = KnowledgebaseCategory

    @classmethod
    def visible_tenant_ids(cls, user_id: str) -> list[str]:
        joined = TenantService.get_joined_tenants_by_user_id(user_id)
        return list(dict.fromkeys([user_id, *(item["tenant_id"] for item in joined)]))

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
```

`list_with_counts()` 返回：

```python
{
    "categories": [
        {
            "id": "...",
            "tenant_id": "...",
            "name": "财务",
            "count": 2,
            "can_manage": True,
        }
    ],
    "total_count": 9,
    "uncategorized_count": 1,
}
```

查询必须复用 `KnowledgebaseService._visibility_and_status_filter(joined_tenant_ids, user_id)`，并在此基础上叠加负责人范围；按 `Knowledgebase.category_id` 分组计算真实分类数量，同时单独计算 `category_id IS NULL`。`owner_ids` 先与 `visible_tenant_ids(user_id)` 求交集，客户端传入不可见负责人时返回空计数，不能扩大权限。

`delete_and_unassign()` 必须使用 `with DB.atomic():`，先把同租户、同分类知识库更新为 `category_id=None`，再删除分类，并返回被解除分类的知识库数量。

- [ ] **步骤 4：运行服务测试**

```bash
uv run pytest test/unit_test/api/db/services/test_knowledgebase_category_service.py -q
uv run ruff check api/db/services/knowledgebase_category_service.py test/unit_test/api/db/services/test_knowledgebase_category_service.py
```

预期：全部通过。

- [ ] **步骤 5：提交服务层**

```bash
git add api/db/services/knowledgebase_category_service.py test/unit_test/api/db/services/test_knowledgebase_category_service.py
git commit -m "feat: add knowledge base category service"
```

---

### 任务 3：提供分类 CRUD API

**文件：**

- 修改：`api/apps/services/dataset_api_service.py`
- 修改：`api/apps/restful_apis/dataset_api.py`
- 修改：`test/testcases/test_http_api/common.py`
- 创建：`test/unit_test/api/apps/services/test_dataset_categories.py`
- 创建：`test/testcases/test_http_api/test_dataset_management/test_categories.py`

**接口：**

- 消费：任务 2 的分类数据库服务。
- 产出：`list_dataset_categories(user_id, args)`。
- 产出：`create_dataset_category(user_id, req)`。
- 产出：`update_dataset_category(user_id, category_id, req)`。
- 产出：`delete_dataset_category(user_id, category_id)`。
- 产出：`GET/POST /api/v1/datasets/categories`。
- 产出：`PUT/DELETE /api/v1/datasets/categories/{category_id}`。

- [ ] **步骤 1：先写应用服务和 HTTP 合同测试**

应用服务测试至少包含：

```python
def test_create_category_rejects_case_insensitive_duplicate(monkeypatch):
    monkeypatch.setattr(
        KnowledgebaseCategoryService,
        "name_exists",
        classmethod(lambda cls, tenant_id, name, exclude_id=None: True),
    )
    ok, result = create_dataset_category("owner-1", {"name": "财务"})
    assert ok is False
    assert result == "Dataset category name already exists"


def test_member_cannot_rename_owner_category(monkeypatch):
    category = SimpleNamespace(id="cat-1", tenant_id="owner-1", name="财务")
    monkeypatch.setattr(
        KnowledgebaseCategoryService,
        "get_or_none",
        classmethod(lambda cls, **kwargs: category),
    )
    ok, result = update_dataset_category("member-2", "cat-1", {"name": "预算"})
    assert ok is False
    assert result == "No authorization to manage this dataset category"
```

在 HTTP 测试助手中加入 `list_categories`、`create_category`、`update_category`、`delete_category`，并验证标准响应：

```python
def test_category_crud(HttpApiAuth):
    created = create_category(HttpApiAuth, {"name": "财务"})
    assert created["code"] == 0
    category_id = created["data"]["id"]

    listed = list_categories(HttpApiAuth)
    assert any(item["id"] == category_id for item in listed["data"]["categories"])

    renamed = update_category(HttpApiAuth, category_id, {"name": "预算"})
    assert renamed["code"] == 0

    deleted = delete_category(HttpApiAuth, category_id)
    assert deleted["code"] == 0
```

另加 HTTP 用例：空名称、129 字符、同团队大小写重名、无效 UUID、无权限、删除不存在分类。

- [ ] **步骤 2：运行测试确认路由和函数不存在**

```bash
uv run pytest test/unit_test/api/apps/services/test_dataset_categories.py -q
uv run pytest test/testcases/test_http_api/test_dataset_management/test_categories.py -q
```

预期：应用服务函数或分类路由不存在而失败。

- [ ] **步骤 3：实现应用服务和路由**

应用服务采用现有 `(success, result)` 风格；创建时固定 `tenant_id=user_id`，避免客户端越权指定团队：

```python
def create_dataset_category(user_id: str, req: dict):
    name = req["name"].strip()
    if KnowledgebaseCategoryService.name_exists(user_id, name):
        return False, "Dataset category name already exists"
    category = KnowledgebaseCategoryService.insert(
        tenant_id=user_id,
        name=name,
        created_by=user_id,
        status=StatusEnum.VALID.value,
    )
    return True, category.to_dict()
```

重命名和删除先按 ID 取分类，再要求 `category.tenant_id == user_id`。捕获并发下的 Peewee `IntegrityError`，返回同一个重名错误。

路由统一调用校验模型和 `get_result()` / `get_error_data_result()`：

```python
@manager.route("/datasets/categories", methods=["POST"])
@login_required
@add_tenant_id_to_kwargs
async def create_category(tenant_id):
    req, err = await validate_and_parse_json_request(request, CreateDatasetCategoryReq)
    if err is not None:
        return get_error_argument_result(err)
    success, result = dataset_api_service.create_dataset_category(tenant_id, req)
    return get_result(data=result) if success else get_error_data_result(message=result)
```

`GET /datasets/categories` 复用 `ListDatasetReq` 解析查询参数，只消费 `args["ext"].get("owner_ids", [])`；返回的 `data` 必须是任务 2 定义的 `{categories, total_count, uncategorized_count}`，从而与知识库列表的负责人过滤保持一致。

- [ ] **步骤 4：运行分类 API 测试**

```bash
uv run pytest test/unit_test/api/apps/services/test_dataset_categories.py -q
uv run pytest test/testcases/test_http_api/test_dataset_management/test_categories.py -q
uv run ruff check api/apps/services/dataset_api_service.py api/apps/restful_apis/dataset_api.py test/unit_test/api/apps/services/test_dataset_categories.py test/testcases/test_http_api/test_dataset_management/test_categories.py
```

预期：全部通过。

- [ ] **步骤 5：提交分类 API**

```bash
git add api/apps/services/dataset_api_service.py api/apps/restful_apis/dataset_api.py test/testcases/test_http_api/common.py test/unit_test/api/apps/services/test_dataset_categories.py test/testcases/test_http_api/test_dataset_management/test_categories.py
git commit -m "feat: add dataset category APIs"
```

---

### 任务 4：把分类接入知识库创建、更新和列表

**文件：**

- 修改：`api/apps/services/dataset_api_service.py`
- 修改：`api/db/services/knowledgebase_service.py`
- 修改：`test/unit_test/api/apps/services/test_dataset_categories.py`
- 修改：`test/testcases/test_http_api/test_dataset_management/test_create_dataset.py`
- 修改：`test/testcases/test_http_api/test_dataset_management/test_update_dataset.py`
- 修改：`test/testcases/test_http_api/test_dataset_management/test_list_datasets.py`

**接口：**

- 消费：任务 1 的 `category_id` 和任务 2 的分类服务。
- 产出：`validate_category_assignment(user_id, kb_tenant_id, category_id)`。
- 产出：`KnowledgebaseService.get_list(..., category_id=None, uncategorized=False)`。
- 产出：列表项中的 `category_id: str | None`。

- [ ] **步骤 1：写知识库分类集成失败测试**

覆盖创建、移动、未分类和组合分页：

```python
def test_create_dataset_with_category(HttpApiAuth):
    category = create_category(HttpApiAuth, {"name": "财务"})["data"]
    result = create_dataset(HttpApiAuth, {"name": "预算库", "category_id": category["id"]})
    assert result["code"] == 0
    assert result["data"]["category_id"] == category["id"]


def test_update_dataset_to_uncategorized(HttpApiAuth):
    category = create_category(HttpApiAuth, {"name": "财务"})["data"]
    dataset = create_dataset(HttpApiAuth, {"name": "预算库", "category_id": category["id"]})["data"]
    result = update_dataset(HttpApiAuth, dataset["id"], {"category_id": None})
    assert result["code"] == 0
    assert result["data"]["category_id"] is None


def test_category_filter_combines_with_keyword_and_pagination(HttpApiAuth):
    category = create_category(HttpApiAuth, {"name": "财务"})["data"]
    create_dataset(HttpApiAuth, {"name": "财务月报", "category_id": category["id"]})
    create_dataset(HttpApiAuth, {"name": "财务年报", "category_id": category["id"]})
    result = list_datasets(
        HttpApiAuth,
        {"page": 2, "page_size": 1, "ext": {"category_id": category["id"], "keywords": "财务"}},
    )
    assert result["code"] == 0
    assert result["total"] == 2
    assert len(result["data"]) == 1
```

另加用例：不存在分类、其他租户分类、`ext.category_id="uncategorized"`、完全不传分类字段的旧请求。

- [ ] **步骤 2：运行现有和新增知识库 API 测试**

```bash
uv run pytest \
  test/testcases/test_http_api/test_dataset_management/test_create_dataset.py \
  test/testcases/test_http_api/test_dataset_management/test_update_dataset.py \
  test/testcases/test_http_api/test_dataset_management/test_list_datasets.py -q
```

预期：新增分类用例失败，原有用例继续通过。

- [ ] **步骤 3：实现分类赋值和服务端过滤**

增加统一校验，创建和更新都调用它：

```python
def validate_category_assignment(user_id: str, kb_tenant_id: str, category_id: str | None):
    if category_id is None:
        return True, None
    category = KnowledgebaseCategoryService.get_or_none(id=category_id, status=StatusEnum.VALID.value)
    if category is None:
        return False, "Dataset category not found"
    if category.tenant_id != kb_tenant_id or kb_tenant_id != user_id:
        return False, "No authorization to assign this dataset category"
    return True, category
```

在知识库查询选择字段中加入 `cls.model.category_id`；在分页前应用：

```python
if uncategorized:
    kbs = kbs.where(cls.model.category_id.is_null(True))
elif category_id:
    kbs = kbs.where(cls.model.category_id == category_id)
```

`list_datasets()` 把 `ext.category_id == "uncategorized"` 转为 `uncategorized=True`；省略分类值时不加条件。

- [ ] **步骤 4：运行知识库 API 回归测试**

```bash
uv run pytest \
  test/unit_test/api/apps/services/test_dataset_categories.py \
  test/testcases/test_http_api/test_dataset_management/test_categories.py \
  test/testcases/test_http_api/test_dataset_management/test_create_dataset.py \
  test/testcases/test_http_api/test_dataset_management/test_update_dataset.py \
  test/testcases/test_http_api/test_dataset_management/test_list_datasets.py -q
uv run ruff check api/apps/services/dataset_api_service.py api/db/services/knowledgebase_service.py
```

预期：全部通过。

- [ ] **步骤 5：提交知识库集成**

```bash
git add api/apps/services/dataset_api_service.py api/db/services/knowledgebase_service.py test/unit_test/api/apps/services/test_dataset_categories.py test/testcases/test_http_api/test_dataset_management/test_create_dataset.py test/testcases/test_http_api/test_dataset_management/test_update_dataset.py test/testcases/test_http_api/test_dataset_management/test_list_datasets.py
git commit -m "feat: integrate categories with datasets"
```

---

### 任务 5：添加前端分类契约、服务和状态 hook

**文件：**

- 修改：`web/src/interfaces/database/dataset.ts`
- 修改：`web/src/interfaces/request/knowledge.ts`
- 修改：`web/src/utils/api.ts`
- 修改：`web/src/services/knowledge-service.ts`
- 修改：`web/src/hooks/use-knowledge-request.ts`
- 创建：`web/src/pages/datasets/category-constants.ts`
- 创建：`web/src/pages/datasets/use-dataset-categories.ts`
- 创建：`web/src/pages/datasets/__tests__/category-constants.test.ts`

**接口：**

- 产出：`IDatasetCategory`、`IDatasetCategorySummary`。
- 产出：`DatasetCategorySelection = 'all' | 'uncategorized' | string`。
- 产出：`toCategoryRequestFilter(selection)`。
- 产出：`useDatasetCategories(ownerIds)`，含 query 和四个 mutation。
- 产出：`useFetchNextKnowledgeListByPage(categorySelection)`。

- [ ] **步骤 1：先写 URL/请求映射测试**

```typescript
import {
  ALL_CATEGORY,
  UNCATEGORIZED_CATEGORY,
  parseCategorySearchParam,
  toCategoryRequestFilter,
} from '../category-constants';

describe('dataset category route mapping', () => {
  it('maps virtual values without leaking all into the API', () => {
    expect(parseCategorySearchParam(null)).toBe(ALL_CATEGORY);
    expect(toCategoryRequestFilter(ALL_CATEGORY)).toBeUndefined();
    expect(toCategoryRequestFilter(UNCATEGORIZED_CATEGORY)).toBe('uncategorized');
    expect(toCategoryRequestFilter('cat-1')).toBe('cat-1');
  });
});
```

- [ ] **步骤 2：运行前端测试确认模块不存在**

```bash
cd web
npm test -- --runInBand src/pages/datasets/__tests__/category-constants.test.ts
```

预期：因 `category-constants.ts` 不存在而失败。

- [ ] **步骤 3：实现类型、服务和 hook**

类型保持后端字段一致：

```typescript
export interface IDatasetCategory {
  id: string;
  tenant_id: string;
  name: string;
  count: number;
  can_manage: boolean;
}

export interface IDatasetCategorySummary {
  categories: IDatasetCategory[];
  total_count: number;
  uncategorized_count: number;
}
```

常量和映射：

```typescript
export const ALL_CATEGORY = 'all';
export const UNCATEGORIZED_CATEGORY = 'uncategorized';
export type DatasetCategorySelection =
  | typeof ALL_CATEGORY
  | typeof UNCATEGORIZED_CATEGORY
  | string;

export const toCategoryRequestFilter = (selection: DatasetCategorySelection) =>
  selection === ALL_CATEGORY ? undefined : selection;
```

`useDatasetCategories(ownerIds: string[] = [])` 使用查询键 `['datasetCategories', ownerIds]`，查询函数调用 `listDatasetCategories({ ext: { owner_ids: ownerIds } })`；成功 mutation 后同时失效：

```typescript
await Promise.all([
  queryClient.invalidateQueries({ queryKey: ['datasetCategories'] }),
  queryClient.invalidateQueries({
    queryKey: [KnowledgeApiAction.FetchKnowledgeListByPage],
  }),
]);
```

所有失败 mutation 调用 `message.error(errorMessage)`，不做乐观更新。知识库列表 hook 把 `category_id: toCategoryRequestFilter(selection)` 放入 `ext`。

- [ ] **步骤 4：运行单测、类型检查和 lint**

```bash
cd web
npm test -- --runInBand src/pages/datasets/__tests__/category-constants.test.ts
npm run type-check
npm run lint -- --quiet
```

预期：全部通过。

- [ ] **步骤 5：提交前端数据层**

```bash
git add web/src/interfaces/database/dataset.ts web/src/interfaces/request/knowledge.ts web/src/utils/api.ts web/src/services/knowledge-service.ts web/src/hooks/use-knowledge-request.ts web/src/pages/datasets/category-constants.ts web/src/pages/datasets/use-dataset-categories.ts web/src/pages/datasets/__tests__/category-constants.test.ts
git commit -m "feat: add dataset category client state"
```

---

### 任务 6：实现左侧分类栏和分类管理

**文件：**

- 创建：`web/src/pages/datasets/dataset-category-sidebar.tsx`
- 创建：`web/src/pages/datasets/dataset-category-dialog.tsx`
- 创建：`web/src/pages/datasets/__tests__/dataset-category-sidebar.test.tsx`
- 修改：`web/src/pages/datasets/index.tsx`
- 修改：`web/src/locales/zh.ts`
- 修改：`web/src/locales/en.ts`

**接口：**

- 消费：任务 5 的 `useDatasetCategories()` 和 `DatasetCategorySelection`。
- 产出：`DatasetCategorySidebarProps`。
- 产出：URL 查询参数 `category=all|uncategorized|<category_id>`。

- [ ] **步骤 1：先写侧栏选择、重命名和删除测试**

```typescript
import { fireEvent, render, screen } from '@testing-library/react';

it('selects a category and resets the page', () => {
  const onSelect = jest.fn();
  render(
    <DatasetCategorySidebar
      summary={{
        total_count: 9,
        uncategorized_count: 1,
        categories: [
          { id: 'cat-1', tenant_id: 'me', name: '财务', count: 2, can_manage: true },
        ],
      }}
      selected="all"
      onSelect={onSelect}
      onCreate={jest.fn()}
      onRename={jest.fn()}
      onDelete={jest.fn()}
    />,
  );
  fireEvent.click(screen.getByRole('button', { name: /财务.*2/ }));
  expect(onSelect).toHaveBeenCalledWith('cat-1');
});
```

再覆盖：全部和未分类没有菜单；`can_manage=false` 不显示菜单；删除确认文案明确“知识库将移至未分类”；分类列表区域带 `overflow-y-auto`。

- [ ] **步骤 2：运行组件测试确认失败**

```bash
cd web
npm test -- --runInBand src/pages/datasets/__tests__/dataset-category-sidebar.test.tsx
```

预期：侧栏组件不存在而失败。

- [ ] **步骤 3：实现侧栏、弹窗和双栏页面**

侧栏 props 固定为：

```typescript
export interface DatasetCategorySidebarProps {
  summary: IDatasetCategorySummary;
  selected: DatasetCategorySelection;
  onSelect: (selection: DatasetCategorySelection) => void;
  onCreate: (name: string) => Promise<boolean>;
  onRename: (category: IDatasetCategory, name: string) => Promise<boolean>;
  onDelete: (category: IDatasetCategory) => Promise<boolean>;
}
```

在 `index.tsx` 中先从现有列表 hook 取得 `filterValue.owner`，传给 `useDatasetCategories((filterValue.owner as string[]) ?? [])`，保证侧栏数量跟负责人筛选一致。随后由 URL 驱动选中项，并重置分页：

```typescript
const selectedCategory = parseCategorySearchParam(searchUrl.get('category'));

const handleCategorySelect = useCallback(
  (category: DatasetCategorySelection) => {
    const next = new URLSearchParams(searchUrl);
    next.set('category', category);
    next.set('page', '1');
    setSearchUrl(next);
  },
  [searchUrl, setSearchUrl],
);
```

页面主体采用：

```tsx
<div className="flex min-h-0 flex-1 px-5 gap-6">
  <aside className="w-56 shrink-0 border-e border-border-button">
    <DatasetCategorySidebar {...categoryProps} />
  </aside>
  <section className="flex min-w-0 flex-1 flex-col">
    {/* 现有 CardContainer、空状态和分页 */}
  </section>
</div>
```

若 URL 中是真实但已删除的分类 ID，在分类 query 完成后替换为 `category=all&page=1`。

- [ ] **步骤 4：运行侧栏测试和前端静态检查**

```bash
cd web
npm test -- --runInBand src/pages/datasets/__tests__/dataset-category-sidebar.test.tsx src/pages/datasets/__tests__/category-constants.test.ts
npm run type-check
npm run lint -- --quiet
```

预期：全部通过。

- [ ] **步骤 5：提交左侧分类栏**

```bash
git add web/src/pages/datasets/dataset-category-sidebar.tsx web/src/pages/datasets/dataset-category-dialog.tsx web/src/pages/datasets/__tests__/dataset-category-sidebar.test.tsx web/src/pages/datasets/index.tsx web/src/locales/zh.ts web/src/locales/en.ts
git commit -m "feat: add dataset category sidebar"
```

---

### 任务 7：支持创建时选择分类和卡片移动

**文件：**

- 创建：`web/src/pages/datasets/dataset-category-picker.tsx`
- 创建：`web/src/pages/datasets/__tests__/dataset-category-picker.test.tsx`
- 修改：`web/src/pages/datasets/dataset-dropdown.tsx`
- 修改：`web/src/pages/datasets/dataset-card.tsx`
- 修改：`web/src/pages/datasets/dataset-creating-dialog.tsx`
- 修改：`web/src/pages/datasets/hooks.ts`
- 修改：`web/src/locales/zh.ts`
- 修改：`web/src/locales/en.ts`

**接口：**

- 消费：任务 5 的分类 DTO 和移动 mutation。
- 产出：`buildCategoryPickerOptions(categories, tenantId)`。
- 产出：创建表单 `category_id?: string`。
- 产出：卡片菜单“移动到分类”。

- [ ] **步骤 1：先写选择器和移动测试**

```typescript
import { buildCategoryPickerOptions } from '../dataset-category-picker';

it('shows uncategorized plus categories from the dataset tenant only', () => {
  const options = buildCategoryPickerOptions(
    [
      { id: 'cat-a', tenant_id: 'team-a', name: '财务', count: 2, can_manage: true },
      { id: 'cat-b', tenant_id: 'team-b', name: '研发', count: 1, can_manage: false },
    ],
    'team-a',
  );
  expect(options.map((item) => item.value)).toEqual(['uncategorized', 'cat-a']);
});
```

组件交互测试锁定：当前分类有选中标记；选择相同分类不发送请求；选择未分类发送 `{ category_id: null }`；移动成功后关闭菜单并刷新 query。

- [ ] **步骤 2：运行测试确认选择器不存在**

```bash
cd web
npm test -- --runInBand src/pages/datasets/__tests__/dataset-category-picker.test.tsx
```

预期：模块不存在而失败。

- [ ] **步骤 3：实现可复用选择器和两个入口**

选择器构造函数：

```typescript
export const buildCategoryPickerOptions = (
  categories: IDatasetCategory[],
  tenantId: string,
): RAGFlowSelectOptionType[] => [
  { label: i18n.t('knowledgeList.uncategorized'), value: UNCATEGORIZED_CATEGORY },
  ...categories
    .filter((category) => category.tenant_id === tenantId)
    .map((category) => ({ label: category.name, value: category.id })),
];
```

卡片菜单使用现有 `DropdownMenuSub`、`DropdownMenuSubTrigger` 和 `DropdownMenuSubContent`；选择后调用：

```typescript
moveDataset({
  datasetId: dataset.id,
  categoryId: value === UNCATEGORIZED_CATEGORY ? null : value,
});
```

创建表单 schema 和默认值增加：

```typescript
category_id: z.string().optional(),
```

提交前把 `UNCATEGORIZED_CATEGORY` 映射为 `undefined`；未选择分类时不改变旧请求结构。

- [ ] **步骤 4：运行前端交互测试和静态检查**

```bash
cd web
npm test -- --runInBand src/pages/datasets/__tests__/dataset-category-picker.test.tsx src/pages/datasets/__tests__/dataset-category-sidebar.test.tsx
npm run type-check
npm run lint -- --quiet
```

预期：全部通过。

- [ ] **步骤 5：提交移动和创建交互**

```bash
git add web/src/pages/datasets/dataset-category-picker.tsx web/src/pages/datasets/__tests__/dataset-category-picker.test.tsx web/src/pages/datasets/dataset-dropdown.tsx web/src/pages/datasets/dataset-card.tsx web/src/pages/datasets/dataset-creating-dialog.tsx web/src/pages/datasets/hooks.ts web/src/locales/zh.ts web/src/locales/en.ts
git commit -m "feat: assign datasets to categories"
```

---

### 任务 8：完成删除回退、兼容性和全量验证

**文件：**

- 修改：`test/testcases/test_http_api/test_dataset_management/test_categories.py`
- 修改：`test/testcases/test_http_api/test_dataset_management/test_list_datasets.py`
- 修改：`web/src/pages/datasets/__tests__/dataset-category-sidebar.test.tsx`
- 修改：`web/src/pages/datasets/index.tsx`（仅在回归测试发现缺口时）
- 修改：`web/src/pages/datasets/use-dataset-categories.ts`（仅在回归测试发现缺口时）

**接口：**

- 消费：任务 1–7 的全部产出。
- 产出：满足设计文档验收标准的完整功能。

- [ ] **步骤 1：补齐端到端回归用例**

后端增加删除分类回退验证：

```python
def test_delete_category_moves_datasets_to_uncategorized(HttpApiAuth):
    category = create_category(HttpApiAuth, {"name": "临时分类"})["data"]
    dataset = create_dataset(HttpApiAuth, {"name": "临时知识库", "category_id": category["id"]})["data"]

    assert delete_category(HttpApiAuth, category["id"])["code"] == 0

    uncategorized = list_datasets(
        HttpApiAuth,
        {"ext": {"category_id": "uncategorized"}},
    )
    row = next(item for item in uncategorized["data"] if item["id"] == dataset["id"])
    assert row["category_id"] is None
```

前端增加陈旧 URL 回退：分类 query 返回中不存在 `category=deleted-id` 时，断言 URL 变为 `category=all&page=1`，并且列表不再带 `deleted-id` 请求。

- [ ] **步骤 2：运行完整分类相关测试**

```bash
uv run pytest \
  test/unit_test/api/db/test_knowledgebase_category_model.py \
  test/unit_test/api/db/services/test_knowledgebase_category_service.py \
  test/unit_test/api/apps/services/test_dataset_categories.py \
  test/unit_test/api/utils/test_doc_validation.py \
  test/testcases/test_http_api/test_dataset_management/test_categories.py \
  test/testcases/test_http_api/test_dataset_management/test_create_dataset.py \
  test/testcases/test_http_api/test_dataset_management/test_update_dataset.py \
  test/testcases/test_http_api/test_dataset_management/test_list_datasets.py -q
```

预期：全部通过。

- [ ] **步骤 3：运行前端完整验证**

```bash
cd web
npm test -- --runInBand \
  src/pages/datasets/__tests__/category-constants.test.ts \
  src/pages/datasets/__tests__/dataset-category-sidebar.test.tsx \
  src/pages/datasets/__tests__/dataset-category-picker.test.tsx
npm run type-check
npm run lint -- --quiet
npm run build
```

预期：测试、类型检查、lint 和生产构建全部通过。

- [ ] **步骤 4：检查迁移和兼容性**

在已启动依赖服务的开发环境执行：

```bash
uv sync --python 3.13 --all-extras
uv run python tools/scripts/db_schema_sync.py \
  --diff \
  --host 127.0.0.1 \
  --port 3306 \
  --user root \
  --password infini_rag_flow \
  --database rag_flow \
  --version v0.26.1
```

以上参数对应仓库 `docker/.env` 的本地默认配置；如果该文件已被团队修改，先从同一文件读取实际值再执行，禁止对生产数据库运行 `--create` 或 `--migrate`。确认差异只有：新建 `knowledgebase_category` 表和给 `knowledgebase` 增加可空 `category_id`。随后使用不带任何分类字段的旧请求创建、更新和列出知识库，确认响应成功且 `category_id` 为 `null`。

- [ ] **步骤 5：检查差异并提交最后的回归修正**

```bash
git diff --check
git status --short
git add test/testcases/test_http_api/test_dataset_management/test_categories.py test/testcases/test_http_api/test_dataset_management/test_list_datasets.py web/src/pages/datasets/__tests__/dataset-category-sidebar.test.tsx web/src/pages/datasets/index.tsx web/src/pages/datasets/use-dataset-categories.ts
git commit -m "test: verify dataset category workflows"
```

若 `index.tsx` 或 hook 没有产生回归修正，不要把它们加入提交；提交前用 `git diff --cached --stat` 确认只包含本任务改动。

---

## 最终验收清单

- [ ] 所有者可以创建、重命名和删除同团队唯一命名的分类。
- [ ] 团队成员看到一致的分类和知识库归属，但不能越权管理他人分类。
- [ ] 知识库只能属于一个分类或未分类。
- [ ] 左侧栏数量不受当前分页限制，并正确响应负责人过滤。
- [ ] 分类、负责人、搜索和分页可以组合使用。
- [ ] 切换分类会把页码重置为 1，并保留可刷新 URL。
- [ ] 删除分类后知识库进入未分类，知识库本身不被删除。
- [ ] 卡片菜单可以移动知识库，创建弹窗可以选择分类。
- [ ] 陈旧分类 URL 自动回退到全部知识库。
- [ ] 旧客户端请求无需增加分类字段即可继续工作。
- [ ] 后端定向测试、前端 Jest、TypeScript、ESLint 和生产构建全部通过。
