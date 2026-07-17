# Image Auth and Admin Health Proxy Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make authenticated document images load on ordinary pages and keep local administrator health probes out of `ALL_PROXY`.

**Architecture:** Reuse the existing authenticated Blob image loader whenever a frontend Authorization token exists, while retaining direct URLs for tokenless requests. Configure the backend launcher with an explicit loopback `NO_PROXY` list so administrator probes connect directly without disabling the external catch-all proxy.

**Tech Stack:** React 18, TypeScript, Jest, Testing Library, Bash, Python 3.13, Pytest.

## Global Constraints

- Do not modify image storage, VLM configuration, Chunk schemas, or administrator status response formats.
- Preserve the existing `ALL_PROXY` value for external model access.
- Do not modify the user's existing `uv.lock` change.
- Do not reparse documents or mutate stored Chunk data.

---

### Task 1: Authenticate document image requests on ordinary pages

**Files:**
- Create: `web/src/components/image/index.test.tsx`
- Modify: `web/src/components/image/index.tsx:1-118`

**Interfaces:**
- Consumes: `getAuthorization(): string`, `buildDocumentImageUrl(id, t): string`, and the existing `fetchDocumentImage(url, authorization)` cache.
- Produces: `useDocumentImageUrl(id, t): string`, which returns a Blob URL for every authenticated request and a direct API URL for tokenless requests.

- [ ] **Step 1: Write the failing authenticated-image test**

```tsx
import { renderHook, waitFor } from '@testing-library/react';
import { useDocumentImageUrl } from './index';

const mockGetAuthorization = jest.fn();

jest.mock('@/utils/authorization-util', () => ({
  getAuthorization: () => mockGetAuthorization(),
}));

jest.mock('@/utils/common-util', () => ({
  getSearchValue: () => '',
}));

describe('useDocumentImageUrl', () => {
  beforeEach(() => {
    mockGetAuthorization.mockReturnValue('Bearer test-token');
    global.fetch = jest.fn().mockResolvedValue({
      ok: true,
      blob: async () => new Blob(['image'], { type: 'image/jpeg' }),
    }) as jest.Mock;
    URL.createObjectURL = jest.fn().mockReturnValue('blob:test-image');
  });

  it('fetches an ordinary-page image with Authorization', async () => {
    const { result } = renderHook(() => useDocumentImageUrl('image-id'));

    await waitFor(() => expect(result.current).toBe('blob:test-image'));
    expect(global.fetch).toHaveBeenCalledWith(
      '/api/v1/documents/images/image-id',
      { headers: { Authorization: 'Bearer test-token' } },
    );
  });
});
```

- [ ] **Step 2: Run the targeted test and verify RED**

Run: `cd web && npx jest src/components/image/index.test.tsx --runInBand --no-cache`

Expected: FAIL because `getSearchValue('shared_id')` is empty, so the hook returns the direct URL and never calls `fetch`.

- [ ] **Step 3: Implement the minimal authenticated loading rule**

Remove the unused `getSearchValue` import and change the hook conditions to depend only on Authorization:

```tsx
export const useDocumentImageUrl = (id: string, t?: string | number) => {
  const directUrl = useMemo(() => buildDocumentImageUrl(id, t), [id, t]);
  const [imageUrl, setImageUrl] = useState(() =>
    getAuthorization() ? '' : directUrl,
  );

  useEffect(() => {
    const authorization = getAuthorization();
    if (!authorization) {
      setImageUrl(directUrl);
      return;
    }

    let ignore = false;
    setImageUrl('');
    const { promise, release } = fetchDocumentImage(directUrl, authorization);
    promise
      .then((url) => {
        if (!ignore) {
          setImageUrl(url);
        }
      })
      .catch(() => {
        if (!ignore) {
          setImageUrl('');
        }
      });

    return () => {
      ignore = true;
      release();
    };
  }, [directUrl]);

  return imageUrl;
};
```

- [ ] **Step 4: Add the tokenless direct-URL regression test**

```tsx
it('uses the direct image URL when Authorization is absent', () => {
  mockGetAuthorization.mockReturnValue('');

  const { result } = renderHook(() => useDocumentImageUrl('public-image'));

  expect(result.current).toBe('/api/v1/documents/images/public-image');
  expect(global.fetch).not.toHaveBeenCalled();
});
```

- [ ] **Step 5: Run frontend tests and type checking**

Run: `cd web && npx jest src/components/image/index.test.tsx --runInBand --no-cache`

Expected: 2 tests passed, 0 failed.

Run: `cd web && npm run type-check`

Expected: exit code 0.

- [ ] **Step 6: Commit the frontend fix**

```bash
git add web/src/components/image/index.tsx web/src/components/image/index.test.tsx
HUSKY=0 git commit -m "fix(web): authenticate document image requests"
```

### Task 2: Bypass the catch-all proxy for local health probes

**Files:**
- Create: `test/unit_test/docker/test_launch_backend_service_proxy.py`
- Modify: `docker/launch_backend_service.sh:47-49`

**Interfaces:**
- Consumes: inherited shell proxy variables, including an optional `ALL_PROXY`.
- Produces: `NO_PROXY` and `no_proxy` containing `localhost,127.0.0.1,::1`, while leaving `ALL_PROXY` unchanged.

- [ ] **Step 1: Write a failing shell-environment regression test**

```python
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
LAUNCH_SCRIPT = REPO_ROOT / "docker" / "launch_backend_service.sh"


def _proxy_setup_block() -> str:
    source = LAUNCH_SCRIPT.read_text(encoding="utf-8")
    start = source.index("# Unset HTTP proxies")
    end = source.index("export PYTHONPATH", start)
    return source[start:end]


def test_launcher_bypasses_loopback_without_clearing_all_proxy():
    command = f"""
export ALL_PROXY=http://127.0.0.1:17890
export NO_PROXY=unexpected
export no_proxy=unexpected
{_proxy_setup_block()}
printf '%s\n%s\n%s\n' "$ALL_PROXY" "$NO_PROXY" "$no_proxy"
"""
    result = subprocess.run(
        ["bash", "-c", command],
        check=True,
        capture_output=True,
        text=True,
    )

    assert result.stdout.splitlines() == [
        "http://127.0.0.1:17890",
        "localhost,127.0.0.1,::1",
        "localhost,127.0.0.1,::1",
    ]
```

- [ ] **Step 2: Run the targeted test and verify RED**

Run: `.venv/bin/pytest -q test/unit_test/docker/test_launch_backend_service_proxy.py`

Expected: FAIL because the launcher currently clears `NO_PROXY` and `no_proxy` to empty strings.

- [ ] **Step 3: Implement the minimal loopback bypass**

Replace the current proxy exports with:

```bash
# Clear protocol-specific proxies while preserving an optional ALL_PROXY for
# external model access. Loopback service probes must always connect directly.
export http_proxy=""
export https_proxy=""
export HTTP_PROXY=""
export HTTPS_PROXY=""
export NO_PROXY="localhost,127.0.0.1,::1"
export no_proxy="$NO_PROXY"
```

- [ ] **Step 4: Run the proxy regression test and shell syntax check**

Run: `.venv/bin/pytest -q test/unit_test/docker/test_launch_backend_service_proxy.py`

Expected: 1 passed, 0 failed.

Run: `bash -n docker/launch_backend_service.sh`

Expected: exit code 0.

- [ ] **Step 5: Verify the health functions with the production proxy shape**

Run:

```bash
ALL_PROXY=http://127.0.0.1:17890 \
NO_PROXY=localhost,127.0.0.1,::1 \
no_proxy=localhost,127.0.0.1,::1 \
POLARS_SKIP_CPU_CHECK=1 .venv/bin/python - <<'PY'
from common import settings
settings.init_settings()
from api.utils.health_utils import check_minio_alive, check_ragflow_server_alive
print(check_ragflow_server_alive())
print(check_minio_alive())
PY
```

Expected: both dictionaries contain `"status": "alive"`.

- [ ] **Step 6: Commit the launcher fix**

```bash
git add docker/launch_backend_service.sh test/unit_test/docker/test_launch_backend_service_proxy.py
HUSKY=0 git commit -m "fix(admin): bypass proxy for local health checks"
```

### Task 3: Final regression verification

**Files:**
- Verify: `web/src/components/image/index.tsx`
- Verify: `docker/launch_backend_service.sh`
- Verify: `web/src/components/image/index.test.tsx`
- Verify: `test/unit_test/docker/test_launch_backend_service_proxy.py`

**Interfaces:**
- Consumes: the completed frontend and launcher changes from Tasks 1 and 2.
- Produces: fresh test, type-check, shell syntax, runtime health, and diff evidence for handoff.

- [ ] **Step 1: Run all targeted automated checks together**

```bash
(cd web && npx jest src/components/image/index.test.tsx --runInBand --no-cache) && \
.venv/bin/pytest -q test/unit_test/docker/test_launch_backend_service_proxy.py && \
bash -n docker/launch_backend_service.sh
```

Expected: 2 Jest tests and 1 Pytest test pass; Bash exits 0.

- [ ] **Step 2: Run frontend type checking**

Run: `cd web && npm run type-check`

Expected: exit code 0.

- [ ] **Step 3: Inspect the final scope**

Run: `git diff --check HEAD~2..HEAD && git status --short`

Expected: no whitespace errors; only intentionally ignored setup artifacts, if any, remain untracked.
