import os
import subprocess
from pathlib import Path


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

    assert result.stdout.splitlines() == [
        "http://127.0.0.1:17890",
        "localhost,127.0.0.1,::1",
        "localhost,127.0.0.1,::1",
    ]
