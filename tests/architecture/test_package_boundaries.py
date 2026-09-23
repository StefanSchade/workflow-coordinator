"""Standalone package-boundary and installed-relocatability guards."""

from __future__ import annotations

import ast
import importlib
import importlib.resources
import os
import pkgutil
import subprocess
import sys
from pathlib import Path

import pytest

import workflow_coordinator
from workflow_coordinator import prompt


pytestmark = [
    pytest.mark.ftr("FTR-AGENT-WORKFLOW-COORDINATOR-P5"),
    pytest.mark.guardrail,
]

PACKAGE_DIRECTORY = Path(workflow_coordinator.__file__).resolve().parent
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
EXECUTABLE_MODULES = ("operator", "proof_harness", "codex_adapter_smoke")
ACCEPTED_CONTRACT = {
    "contract_id": "workflow_coordinator.agent_operating_contract",
    "version": 4,
    "byte_count": 4842,
    "sha256": "3eb98dddce9a976d7adeb0be35f893cdb04e2d8da1125b2622302ad0a5eeb43c",
}


def _child(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    environment.pop("PYTHONSAFEPATH", None)
    return subprocess.run(
        (sys.executable, *args),
        cwd=cwd,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )


def test_production_uses_relative_imports_without_source_bootstrap() -> None:
    production_files = tuple(sorted(PACKAGE_DIRECTORY.glob("*.py")))
    assert production_files
    for path in production_files:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        absolute_internal = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
            and node.level == 0
            and (node.module or "").startswith(
                ("workflow_coordinator", "scripts", "spreadsheet_handling")
            )
        ]
        sys_path = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "sys"
            and node.attr == "path"
        ]
        assert not absolute_internal, path
        assert not sys_path, path
        assert "scripts.workflow_coordinator" not in source
        assert "__file__" not in source
        assert "PYTHONPATH" not in source
        assert "PYTHONSAFEPATH" not in source

    for test_path in (
        REPOSITORY_ROOT / "tests/unit/test_operator.py",
        REPOSITORY_ROOT / "tests/unit/test_a2_evidence_commands.py",
    ):
        assert '"workflow_coordinator.evidence.' not in test_path.read_text(encoding="utf-8")


def test_all_modules_import_under_final_identity_only() -> None:
    names = [workflow_coordinator.__name__]
    names.extend(
        item.name
        for item in pkgutil.walk_packages(workflow_coordinator.__path__, "workflow_coordinator.")
    )
    for name in names:
        module = importlib.import_module(name)
        origin = Path(module.__spec__.origin or "").resolve()
        assert PACKAGE_DIRECTORY == origin.parent or PACKAGE_DIRECTORY in origin.parents
    assert "scripts" not in sys.modules
    assert "spreadsheet_handling" not in sys.modules


def test_installed_module_help_seams_are_provider_free(tmp_path: Path) -> None:
    cwd = tmp_path / "unrelated-cwd"
    cwd.mkdir()
    for module in EXECUTABLE_MODULES:
        completed = _child("-m", f"workflow_coordinator.{module}", "--help", cwd=cwd)
        assert completed.returncode == 0, completed.stderr


def test_a3_resource_identity_and_provenance_are_exact() -> None:
    contract = prompt.load_agent_operating_contract()
    assert prompt.__package__ == "workflow_coordinator"
    resource = importlib.resources.files(prompt.__package__) / prompt.RESOURCE
    data = resource.read_bytes()
    assert len(data) == 4842
    assert contract.provenance() == {
        **ACCEPTED_CONTRACT,
        "resource": "resources/agent_operating_contract_v4.txt",
    }
    assert data.decode("utf-8") == contract.text
    assert sorted(
        path.name
        for path in resource.parent.iterdir()
        if path.name.startswith("agent_operating_contract_v")
    ) == ["agent_operating_contract_v4.txt"]
