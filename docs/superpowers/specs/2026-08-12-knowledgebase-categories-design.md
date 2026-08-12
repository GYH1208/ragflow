# Knowledge Base Categories Design

## Goal

Add team-shared, manually managed categories to the knowledge base list so users can navigate a large number of knowledge bases efficiently.

The feature uses a folder-like model:

- A knowledge base belongs to at most one category.
- Categories are shared within the knowledge base owner's tenant/team.
- Category names are unique within a tenant/team.
- Existing and uncategorized knowledge bases appear under a virtual **Uncategorized** entry.
- Deleting a category never deletes its knowledge bases.

## Scope

This design covers:

- Persistent category storage and additive schema changes.
- Category CRUD, ordering, counts, filtering, and knowledge base assignment APIs.
- A left category sidebar on the desktop knowledge base list.
- Category selection during knowledge base creation.
- Moving an existing knowledge base from its card menu.
- Permissions, compatibility, error handling, and tests.

This design does not cover nested categories, multiple categories per knowledge base, automatic categorization, bulk moving, category colors/icons, or changes to document tags.

## Data Model

### `knowledgebase_category`

Add a new model and table with these fields:

- `id`: 32-character primary key.
- `tenant_id`: owner tenant/team identifier, indexed.
- `name`: trimmed category name, limited to 128 characters.
- `created_by`: user that created the category.
- Standard creation and update timestamps inherited from the project base model.

Add a unique database index on `(tenant_id, name)`. Names are compared case-insensitively after trimming. The service layer must perform a friendly preflight duplicate-name check, while the database constraint remains the final concurrency-safe guard.

### `knowledgebase.category_id`

Add one nullable, indexed `category_id` field to the existing `knowledgebase` table.

- `NULL` means uncategorized.
- Existing rows remain valid and require no content migration.
- The field is additive and does not change the meaning of any existing field.
- Referential integrity is enforced in the service layer, consistent with the project's current identifier-based model relationships.

Schema synchronization uses the project's existing Peewee migration tooling. Deployment must add the category table before adding or serving category-aware behavior.

## Tenant Scope and Permissions

A category belongs to the same tenant/team that owns its knowledge bases. Users who can access a team's knowledge bases can see that team's categories.

Mutating actions—creating, renaming, sorting, deleting categories, and assigning knowledge bases—use the same effective management permission as modifying the target knowledge base/team content. The server validates both:

1. The current user can manage the target knowledge base or tenant/team.
2. The category and knowledge base have the same `tenant_id`.

Moving a knowledge base across tenant/team category boundaries is rejected.

## API Design

Add category endpoints under the dataset API namespace:

- `GET /api/v1/datasets/categories`
  - Returns visible categories with knowledge base counts.
  - Includes an uncategorized count as virtual summary data, not a stored category row.
  - Respects the same owner/team visibility rules as the knowledge base list.
- `POST /api/v1/datasets/categories`
  - Creates a category for a tenant/team the current user can manage.
- `PUT /api/v1/datasets/categories/{category_id}`
  - Renames a category.
- `DELETE /api/v1/datasets/categories/{category_id}`
  - In one database transaction, clears `category_id` from member knowledge bases and deletes the category.

Extend existing dataset operations additively:

- Dataset create accepts an optional `category_id`.
- Dataset update accepts an optional `category_id`; explicit `null` moves it to Uncategorized.
- Dataset list accepts a category filter in its existing `ext` filter object.
  - A concrete ID selects that category.
  - A dedicated uncategorized value selects rows whose `category_id` is `NULL`.
  - An omitted value preserves the current all-categories behavior.
- Dataset list responses include nullable `category_id`.

Existing callers that do not send category data continue to behave exactly as before.

## Backend Components and Data Flow

Add a focused category service responsible for:

- Tenant/team access checks.
- Name normalization and uniqueness validation.
- Category CRUD and stable creation-time ordering.
- Aggregate counts using the same visibility predicates as the dataset list.
- Transactional category deletion and knowledge base unassignment.

Extend the dataset service only where knowledge base creation, update, serialization, and list filtering need category awareness. Category rules remain in the category service rather than being distributed across route handlers.

For list loading:

1. The client requests category summaries and the selected page of knowledge bases.
2. The selected category, search string, owner filter, page, and page size form the query key and request filters.
3. The server applies visibility, owner, search, parser, and category predicates before pagination.
4. Category counts are computed independently of the selected page so sidebar counts remain correct.

## Frontend Design

Use the approved persistent left-sidebar layout while preserving the existing global navigation, toolbar, and knowledge base cards.

### Category sidebar

The sidebar contains:

1. **All knowledge bases**, with a count.
2. **Uncategorized**, with a count.
3. Team categories in stable creation-time order, each with a count.
4. A **Create category** action at the bottom.

The selected item uses the project's existing blue selected state. The sidebar scrolls independently when categories exceed the viewport. The knowledge base content area keeps its existing pagination.

Selecting a category:

- Updates the route/query state so the view is refresh-safe and shareable.
- Resets the knowledge base page to 1.
- Combines with search and owner filters.
- Fetches a server-filtered page rather than filtering only the current client page.

### Category management

Each real category exposes a compact overflow menu for rename and delete. All forms trim names before submission.

- Duplicate names show a clear inline/server error.
- Delete confirmation states that contained knowledge bases will move to Uncategorized.
- All and Uncategorized are virtual system entries and cannot be renamed or deleted.

### Knowledge base cards and creation

Add **Move to category** to the existing knowledge base card menu. It opens a category picker containing Uncategorized and categories valid for the knowledge base's tenant/team. Successful movement refreshes both the current list and category counts.

The knowledge base creation dialog gains an optional category selector. When omitted, the new knowledge base is uncategorized. The server remains authoritative if the selected category becomes unavailable before submission.

## Error Handling and Concurrency

- Duplicate create or rename returns a conflict-style application error with a translatable message.
- Missing or deleted categories return a not-found/data error.
- Cross-tenant assignment and insufficient permissions return a forbidden error.
- Failed client mutations retain the previous UI state and display the server message.
- Query invalidation occurs only after successful mutations.
- The unique database index resolves simultaneous duplicate category requests safely.
- Category deletion and unassignment run in one transaction, preventing knowledge bases from retaining a deleted category ID.
- If a bookmarked category no longer exists, the client falls back to All knowledge bases and removes the stale route filter.

## Compatibility and Rollout

The change is additive:

- One new table.
- One nullable field on `knowledgebase`.
- Optional request and response properties.
- No reinterpretation or removal of existing fields.

Existing knowledge bases appear in Uncategorized immediately after rollout. Existing clients continue to list, create, and update knowledge bases without category input. Document tag APIs and behavior remain unchanged.

## Testing Strategy

### Backend

Cover:

- Category create, list, rename, and delete.
- Name trimming and tenant-scoped uniqueness.
- Same names allowed in different tenants/teams.
- Visibility and mutation permissions for owner and team members.
- Rejection of cross-tenant category assignment.
- Dataset creation and movement with valid, null, missing, and inaccessible categories.
- Combined category, owner, keyword, and pagination filtering.
- Accurate counts independent of page size.
- Transactional deletion moving member knowledge bases to Uncategorized.
- Existing dataset API requests without category fields.

### Frontend

Cover:

- Sidebar rendering, counts, selection, independent overflow, and route persistence.
- Category selection resetting pagination.
- Combined category, owner, and search filters.
- Create, rename, delete confirmation, duplicate-name, permission, and stale-category errors.
- Moving a card and refreshing both list and counts.
- Selecting a category during knowledge base creation.
- All and Uncategorized being non-editable.
- Existing empty, search-empty, and paginated list states.

## Acceptance Criteria

The feature is complete when:

- A permitted team member can create a uniquely named team category.
- A knowledge base can belong to exactly one category or Uncategorized.
- All team members see consistent category assignments.
- The left sidebar filters server-side results and displays correct counts.
- Search, owner filtering, category filtering, and pagination work together.
- Categories can be renamed and deleted safely.
- Deleting a category never deletes a knowledge base.
- Existing knowledge bases and older API clients work without data migration or behavior regressions.
