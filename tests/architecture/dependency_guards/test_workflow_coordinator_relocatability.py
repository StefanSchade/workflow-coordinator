"""Bounded source-layout relocation proof for Workflow Coordinator A4'."""

from __future__ import annotations

import ast
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import scripts.workflow_coordinator as workflow_coordinator


pytestmark = [
    pytest.mark.ftr("FTR-AGENT-WORKFLOW-COORDINATOR-P5"),
    pytest.mark.guardrail,
]

PACKAGE_DIRECTORY = Path(workflow_coordinator.__file__).resolve().parent
SOURCE_ROOT = PACKAGE_DIRECTORY.parents[1]
TRACKED_PREFIX = Path("scripts/workflow_coordinator")
EXECUTABLE_MODULES = ("operator", "proof_harness", "codex_adapter_smoke")
ACCEPTED_CONTRACT = {
    "contract_id": "workflow_coordinator.agent_operating_contract",
    "version": 1,
    "byte_count": 1688,
    "sha256": "058fde8651898afa170160b35d4c6969459130652a69cac1ae2f6112d8e5a194",
}


def _wfc_child(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
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


def _relocated_package(tmp_path: Path) -> tuple[Path, tuple[Path, ...]]:
    tracked = subprocess.run(
        ("git", "-C", str(SOURCE_ROOT), "ls-files", "--", TRACKED_PREFIX.as_posix()),
        text=True,
        capture_output=True,
        check=True,
    ).stdout.splitlines()
    assert tracked

    source_root = tmp_path / "relocated-source"
    package_root = source_root / "portable_wfc"
    copied: list[Path] = []
    for tracked_name in tracked:
        tracked_path = Path(tracked_name)
        relative_path = tracked_path.relative_to(TRACKED_PREFIX)
        source = SOURCE_ROOT / tracked_path
        destination = package_root / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        copied.append(relative_path)

    assert set(copied) == {
        path.relative_to(package_root) for path in package_root.rglob("*") if path.is_file()
    }
    assert not (source_root / ".git").exists()
    assert not any(source_root.glob("pyproject.toml"))
    return source_root, tuple(sorted(copied))


def _absolute_wfc_imports(tree: ast.AST) -> list[ast.ImportFrom]:
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and node.level == 0
        and (node.module or "").startswith("scripts.workflow_coordinator")
    ]


def _sys_path_references(tree: ast.AST) -> list[ast.Attribute]:
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "sys"
        and node.attr == "path"
    ]


def test_production_uses_one_relative_import_model_without_bootstrap() -> None:
    production_files = tuple(sorted(PACKAGE_DIRECTORY.glob("*.py")))
    assert production_files

    for path in production_files:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        assert not _absolute_wfc_imports(tree), path
        assert not _sys_path_references(tree), path
        assert "__file__" not in source
        assert "PYTHONPATH" not in source
        assert "PYTHONSAFEPATH" not in source

    executable_source = "\n".join(
        (PACKAGE_DIRECTORY / f"{module}.py").read_text(encoding="utf-8")
        for module in EXECUTABLE_MODULES
    )
    assert "__package__" not in executable_source

    for test_path in (
        SOURCE_ROOT / "tests/unit/workflow_coordinator/test_operator.py",
        SOURCE_ROOT / "tests/unit/workflow_coordinator/test_a2_evidence_commands.py",
    ):
        assert '"scripts.workflow_coordinator.evidence.' not in test_path.read_text(
            encoding="utf-8"
        )


def test_current_package_module_startup_uses_explicit_source_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for module in EXECUTABLE_MODULES:
        completed = _wfc_child(
            "-m", f"scripts.workflow_coordinator.{module}", "--help", cwd=SOURCE_ROOT
        )
        assert completed.returncode == 0, completed.stderr

    unrelated_parent_cwd = tmp_path / "unrelated-parent-cwd"
    unrelated_parent_cwd.mkdir()
    monkeypatch.chdir(unrelated_parent_cwd)
    for module in EXECUTABLE_MODULES:
        completed = _wfc_child(
            "-m", f"scripts.workflow_coordinator.{module}", "--help", cwd=SOURCE_ROOT
        )
        assert completed.returncode == 0, completed.stderr


def test_tracked_package_relocates_under_alternate_identity_in_fresh_children(
    tmp_path: Path,
) -> None:
    relocated_source_root, copied = _relocated_package(tmp_path)
    assert len(copied) == 16

    for module in EXECUTABLE_MODULES:
        completed = _wfc_child(
            "-m", f"portable_wfc.{module}", "--help", cwd=relocated_source_root
        )
        assert completed.returncode == 0, completed.stderr

    unrelated_runtime_cwd = tmp_path / "unrelated-runtime-cwd"
    unrelated_runtime_cwd.mkdir()
    probe = r'''
import importlib
import importlib.resources
import json
import os
import pkgutil
import sys
from pathlib import Path

package = importlib.import_module("portable_wfc")
module_names = [package.__name__]
module_names.extend(info.name for info in pkgutil.walk_packages(package.__path__, "portable_wfc."))
for module_name in module_names:
    importlib.import_module(module_name)

os.chdir(sys.argv[1])
source_root = Path(sys.argv[2]).resolve()
prompt = importlib.import_module("portable_wfc.prompt")
contract = prompt.load_agent_operating_contract()
resource = importlib.resources.files(prompt.__package__) / prompt.RESOURCE

origins = {}
for name, module in sorted(sys.modules.items()):
    if name == "portable_wfc" or name.startswith("portable_wfc."):
        origin = module.__spec__.origin
        assert origin is not None
        resolved = Path(origin).resolve()
        assert resolved == source_root or source_root in resolved.parents
        origins[name] = str(resolved)

assert "scripts" not in sys.modules
assert "spreadsheet_handling" not in sys.modules
print(json.dumps({
    "module_names": sorted(module_names),
    "origins": origins,
    "scripts_imported": "scripts" in sys.modules,
    "spreadsheet_handling_imported": "spreadsheet_handling" in sys.modules,
    "resource_package": prompt.__package__,
    "resource_location": str(resource),
    "resource_matches_loaded_contract": resource.read_text(encoding="utf-8") == contract.text,
    "contract": contract.provenance(),
}))
'''
    completed = _wfc_child(
        "-c",
        probe,
        str(unrelated_runtime_cwd),
        str(relocated_source_root),
        cwd=relocated_source_root,
    )
    assert completed.returncode == 0, completed.stderr
    facts = json.loads(completed.stdout)

    expected_modules = {"portable_wfc"} | {
        f"portable_wfc.{path.stem}"
        for path in copied
        if path.suffix == ".py" and path.name != "__init__.py"
    }
    assert set(facts["module_names"]) == expected_modules
    assert set(facts["origins"]) == expected_modules
    assert facts["scripts_imported"] is False
    assert facts["spreadsheet_handling_imported"] is False
    assert facts["resource_package"] == "portable_wfc"
    assert facts["resource_matches_loaded_contract"] is True
    assert Path(facts["resource_location"]).resolve().is_relative_to(relocated_source_root)
    assert facts["contract"] == {
        **ACCEPTED_CONTRACT,
        "resource": "resources/agent_operating_contract_v1.txt",
    }

    neutral_child_cwd = tmp_path / "neutral-negative-control"
    neutral_child_cwd.mkdir()
    negative = _wfc_child(
        "-m", "portable_wfc.operator", "--help", cwd=neutral_child_cwd
    )
    assert negative.returncode != 0
    assert "No module named 'portable_wfc'" in negative.stderr
