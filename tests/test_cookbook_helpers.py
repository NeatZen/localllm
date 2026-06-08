import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

from routes.cookbook_helpers import (
    _safe_env_prefix,
    _validate_cache_repo_id,
    _validate_gpus,
    _validate_ssh_port,
    windows_powershell_exe,
)


def test_safe_env_prefix_accepts_quoted_venv_path():
    assert (
        _safe_env_prefix("source '~/vllm-env/bin/activate'")
        == '[ -f "$HOME/vllm-env/bin/activate" ] && source "$HOME/vllm-env/bin/activate" || true'
    )


def test_safe_env_prefix_leaves_compound_conda_prefix_unchanged():
    prefix = 'eval "$(conda shell.bash hook)" && conda activate qwen35'
    assert _safe_env_prefix(prefix) == prefix


def test_safe_env_prefix_rejects_freeform_shell():
    with pytest.raises(HTTPException):
        _safe_env_prefix("echo ok; curl https://example.invalid")


def test_safe_env_prefix_accepts_powershell_activation_path():
    assert (
        _safe_env_prefix("& 'C:\\Users\\me\\venv\\Scripts\\Activate.ps1'")
        == "& 'C:\\Users\\me\\venv\\Scripts\\Activate.ps1'"
    )


def test_validate_ssh_port_rejects_shell_payload():
    with pytest.raises(HTTPException):
        _validate_ssh_port("22; touch /tmp/pwned")
    assert _validate_ssh_port("2222") == "2222"


def test_validate_gpus_accepts_indexes_only():
    assert _validate_gpus("0,1,2") == "0,1,2"
    with pytest.raises(HTTPException):
        _validate_gpus("0; rm -rf /")


def test_validate_cache_repo_id_accepts_ollama_refs():
    assert _validate_cache_repo_id("gemma:latest", is_ollama=True) == "gemma:latest"
    assert _validate_cache_repo_id("qwen2.5-3b-heretic:latest", is_ollama=True) == "qwen2.5-3b-heretic:latest"
    assert _validate_cache_repo_id("library/gemma:2b", is_ollama=True) == "library/gemma:2b"


def test_validate_cache_repo_id_rejects_ollama_refs_without_flag():
    with pytest.raises(HTTPException):
        _validate_cache_repo_id("gemma:latest", is_ollama=False)


def test_validate_cache_repo_id_accepts_hf_and_local_ids():
    assert _validate_cache_repo_id("Qwen/Qwen3-8B") == "Qwen/Qwen3-8B"
    assert _validate_cache_repo_id("DeepSeek-Coder-V2-Lite-Instruct-GGUF", is_local_dir=True) == "DeepSeek-Coder-V2-Lite-Instruct-GGUF"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only")
def test_windows_powershell_exe_resolves_full_path():
    exe = windows_powershell_exe()
    assert exe.lower().endswith((".exe", "powershell"))
    assert Path(exe).is_file()
