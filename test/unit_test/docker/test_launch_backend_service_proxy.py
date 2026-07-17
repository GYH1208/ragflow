import os
import subprocess
from pathlib import Path

from requests.utils import should_bypass_proxies


REPO_ROOT = Path(__file__).resolve().parents[3]
LAUNCHER = REPO_ROOT / "docker" / "launch_backend_service.sh"


def _proxy_setup_block() -> str:
    launcher = LAUNCHER.read_text(encoding="utf-8")
    start = launcher.index("# Unset HTTP proxies")
    end = launcher.index("export PYTHONPATH", start)
    return launcher[start:end]


def test_launcher_preserves_all_proxy_and_bypasses_loopback_hosts():
    env = os.environ.copy()
    env.update(
        {
            "ALL_PROXY": "http://127.0.0.1:17890",
            "MINIO_HOST": "minio",
            "NO_PROXY": "unexpected.example",
            "no_proxy": "unexpected.example",
        }
    )
    command = (
        f"{_proxy_setup_block()}\n"
        "printf '%s\\n%s\\n%s\\n' \"$ALL_PROXY\" \"$NO_PROXY\" \"$no_proxy\""
    )

    result = subprocess.run(
        ["bash", "-c", command],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )

    all_proxy, no_proxy, lowercase_no_proxy = result.stdout.splitlines()

    assert all_proxy == "http://127.0.0.1:17890"
    assert no_proxy == "localhost,127.0.0.1,::1,minio"
    assert lowercase_no_proxy == no_proxy
    assert should_bypass_proxies(
        "http://127.0.0.1:9380/api/v1/system/ping", no_proxy
    )
    assert should_bypass_proxies(
        "http://minio:9000/minio/health/live", no_proxy
    )
