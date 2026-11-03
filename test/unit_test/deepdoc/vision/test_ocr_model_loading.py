"""Exercise OCR session configuration with real ONNX Runtime inference."""

import importlib.util
import shutil
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import numpy as np
import onnxruntime as ort
import pytest
from onnxruntime.datasets import get_example


@pytest.fixture
def loader(monkeypatch, tmp_path):
    """Skip application startup and image transforms, keeping the loader real."""
    repo_root = Path(__file__).resolve().parents[4]
    package = ModuleType("ocr_session_test")
    package.__path__ = [str(repo_root / "deepdoc" / "vision")]
    common = ModuleType("common")
    common.__path__ = [str(repo_root / "common")]
    monkeypatch.setitem(sys.modules, "ocr_session_test", package)
    monkeypatch.setitem(sys.modules, "common", common)
    stubs = {
        "common.file_utils": {"get_project_base_directory": lambda: str(repo_root)},
        "common.misc_utils": {"pip_install_torch": lambda: None},
        "common.settings": {},
        "ocr_session_test.operators": {},
        "ocr_session_test.postprocess": {"build_post_process": lambda *_args: None},
    }
    for name, attributes in stubs.items():
        module = ModuleType(name)
        module.__dict__.update(attributes)
        monkeypatch.setitem(sys.modules, name, module)
    monkeypatch.setitem(
        sys.modules, "torch", SimpleNamespace(cuda=SimpleNamespace(is_available=lambda: False))
    )
    for name in ("OCR_INTRA_OP_NUM_THREADS", "OCR_INTER_OP_NUM_THREADS", "OCR_GPUMEM_ARENA_SHRINKAGE"):
        monkeypatch.delenv(name, raising=False)

    module_name = "ocr_session_test.ocr"
    spec = importlib.util.spec_from_file_location(module_name, repo_root / "deepdoc" / "vision" / "ocr.py")
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, module_name, module)
    spec.loader.exec_module(module)
    # The runtime's bundled model is tiny and requires no downloads or OCR weights.
    shutil.copyfile(get_example("mul_1.onnx"), tmp_path / "test.onnx")
    yield module, str(tmp_path)
    module.loaded_models.clear()


def test_cpu_session_applies_memory_and_thread_settings(loader, monkeypatch):
    module, model_dir = loader
    monkeypatch.setenv("OCR_INTRA_OP_NUM_THREADS", "2")
    monkeypatch.setenv("OCR_INTER_OP_NUM_THREADS", "3")

    session, _ = module.load_model(model_dir, "test")
    options = session.get_session_options()

    assert options.enable_cpu_mem_arena is False
    assert options.intra_op_num_threads == 2
    assert options.inter_op_num_threads == 3


def test_cpu_inference_runs_without_an_arena(loader):
    module, model_dir = loader
    session, run_options = module.load_model(model_dir, "test")

    result = session.run(None, {"X": np.ones((3, 2), dtype=np.float32)}, run_options)

    np.testing.assert_array_equal(result[0], [[1, 2], [3, 4], [5, 6]])


def test_repeated_load_reuses_the_session_and_run_options(loader):
    module, model_dir = loader
    first = module.load_model(model_dir, "test")

    second = module.load_model(model_dir, "test")

    assert second[0] is first[0]
    assert second[1] is first[1]


@pytest.mark.parametrize("shrink", [False, True])
def test_cuda_path_applies_session_settings_and_preserves_gpu_options(loader, monkeypatch, shrink):
    module, model_dir = loader
    monkeypatch.setitem(
        sys.modules,
        "torch",
        SimpleNamespace(cuda=SimpleNamespace(is_available=lambda: True, device_count=lambda: 4)),
    )
    monkeypatch.setenv("OCR_GPU_MEM_LIMIT_MB", "256")
    monkeypatch.setenv("OCR_ARENA_EXTEND_STRATEGY", "kSameAsRequested")
    monkeypatch.setenv("OCR_GPUMEM_ARENA_SHRINKAGE", "1" if shrink else "0")
    original_constructor = ort.InferenceSession
    supplied = {}

    def cpu_backed_session(model_path, **kwargs):
        # CUDA hardware is unavailable in unit tests. Keep actual session options
        # and inference real while recording the requested GPU provider contract.
        supplied.update(kwargs)
        return original_constructor(
            model_path, sess_options=kwargs.get("sess_options"), providers=["CPUExecutionProvider"]
        )

    monkeypatch.setattr(ort, "InferenceSession", cpu_backed_session)
    session, run_options = module.load_model(model_dir, "test", device_id=2)

    assert session.get_session_options().enable_cpu_mem_arena is False
    assert session.get_session_options().intra_op_num_threads == 2
    assert supplied["providers"] == ["CUDAExecutionProvider"]
    assert supplied["provider_options"] == [
        {"device_id": 2, "gpu_mem_limit": 268435456, "arena_extend_strategy": "kSameAsRequested"}
    ]
    if shrink:
        assert run_options.get_run_config_entry("memory.enable_memory_arena_shrinkage") == "gpu:2"
    else:
        result = session.run(None, {"X": np.ones((3, 2), dtype=np.float32)}, run_options)
        np.testing.assert_array_equal(result[0], [[1, 2], [3, 4], [5, 6]])
