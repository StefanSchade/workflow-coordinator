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
    "version": 6,
    "byte_count": 5754,
    "sha256": "7abf57ab315965c4266674efdb71c6cd1415989cdb771b9232501563862ca8f7",
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


def _package_dotted_path(path: Path) -> list[str]:
    """The dotted path of the package *containing* ``path`` -- its own
    filename stem dropped, whether that stem is a regular module name or
    ``__init__`` (PEP 328's ``__package__``)."""

    relative = path.relative_to(PACKAGE_DIRECTORY.parent).with_suffix("")
    return list(relative.parts)[:-1]


def _import_targets(path: Path, node: ast.AST) -> set[str]:
    """Every fully-dotted module name one import statement could resolve to,
    absolute imports as-is and relative ones resolved against ``path``'s own
    package (PEP 328) -- this repository's internal imports are always
    relative (see ``test_production_uses_relative_imports_without_source_bootstrap``)."""

    if isinstance(node, ast.Import):
        return {alias.name for alias in node.names}
    if not isinstance(node, ast.ImportFrom):
        return set()
    if not node.level:
        base = node.module or ""
        return {base} | ({f"{base}.{alias.name}" for alias in node.names} if base else set())
    own_package = _package_dotted_path(path)
    strip = node.level - 1
    base_parts = own_package[: len(own_package) - strip] if strip else own_package
    base = ".".join(base_parts)
    if node.module:
        full = f"{base}.{node.module}" if base else node.module
        return {full}
    return {f"{base}.{alias.name}" if base else alias.name for alias in node.names}


def test_skill_module_imports_are_bounded() -> None:
    """SI-4, SI-10: ``frontdoor/skill.py`` imports only the standard library
    and ``implementation_identity.VERSION``, nothing else from the package."""

    path = PACKAGE_DIRECTORY / "frontdoor" / "skill.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    allowed_stdlib = set(sys.stdlib_module_names)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".")[0]
                assert top in allowed_stdlib, f"skill.py imports non-stdlib module: {alias.name}"
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                targets = _import_targets(path, node)
                assert targets == {"workflow_coordinator.implementation_identity"}, (
                    f"skill.py may only import from implementation_identity, found: {targets}"
                )
                imported_names = {alias.name for alias in node.names}
                assert imported_names == {"VERSION"}, (
                    f"skill.py may only import VERSION from implementation_identity, found: {imported_names}"
                )
            else:
                top = (node.module or "").split(".")[0]
                assert top in allowed_stdlib, f"skill.py imports non-stdlib module: {node.module}"


def test_only_cli_imports_skill_module() -> None:
    """SI-10: no module but ``frontdoor/cli.py`` imports ``frontdoor/skill.py``."""

    skill_module = "workflow_coordinator.frontdoor.skill"
    cli_path = PACKAGE_DIRECTORY / "frontdoor" / "cli.py"
    for path in sorted(PACKAGE_DIRECTORY.rglob("*.py")):
        if path.name == "skill.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            targets = _import_targets(path, node)
            if skill_module in targets:
                assert path == cli_path, (
                    f"{path} imports frontdoor.skill; only frontdoor/cli.py may"
                )


def test_a3_resource_identity_and_provenance_are_exact() -> None:
    contract = prompt.load_agent_operating_contract()
    assert prompt.__package__ == "workflow_coordinator"
    resource = importlib.resources.files(prompt.__package__) / prompt.RESOURCE
    data = resource.read_bytes()
    assert len(data) == 5754
    assert contract.provenance() == {
        **ACCEPTED_CONTRACT,
        "resource": "resources/agent_operating_contract_v6.txt",
    }
    assert data.decode("utf-8") == contract.text
    assert sorted(
        path.name
        for path in resource.parent.iterdir()
        if path.name.startswith("agent_operating_contract_v")
    ) == ["agent_operating_contract_v6.txt"]
