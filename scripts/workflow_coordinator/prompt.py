"""Small, deterministic Slice-3 prompt assembly with visible bounded omissions."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Mapping

from .model import Phase, Run

COMPONENT_LIMIT_BYTES = 65_536
CONTEXT_LIMIT_BYTES = 196_608
DIFF_LIMIT_BYTES = 65_536
DIFF_FILE_LIMIT = 12


@dataclass(frozen=True)
class PromptComponent:
    """A versioned natural-language source selected by a phase, never a template."""

    component_id: str
    path: str
    revision: str
    content: str

    @property
    def byte_size(self) -> int:
        return len(self.content.encode("utf-8"))

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.content.encode("utf-8")).hexdigest()

    def reference(self) -> dict[str, object]:
        return {"id": self.component_id, "path": self.path, "revision": self.revision,
                "digest": self.digest, "bytes": self.byte_size, "retrieval": f"git show {self.revision}:{self.path}"}


@dataclass(frozen=True)
class ContextItem:
    reference: str
    content: str

    @property
    def byte_size(self) -> int:
        return len(self.content.encode("utf-8"))


@dataclass(frozen=True)
class PromptPackage:
    text: str
    selected_components: tuple[dict[str, object], ...]
    omissions: tuple[dict[str, object], ...]
    inline_diff: str | None
    diff_reference: dict[str, object] | None


def _selected_ids(phase: Phase) -> tuple[str, ...]:
    """Keep the FTR's role, modifier, policy selection ordering explicit."""

    return (*phase.role_components, *phase.modifier_components, *phase.repository_policy_components)


def _header(run: Run, phase: Phase) -> str:
    routes = ", ".join(sorted(phase.routes))
    return (
        "[identity]\n"
        f"work_item_id={run.work_item_id}\nrun_id={run.run_id}\n"
        f"hop_id=H{run.hop_used + 1:03d}\nbase_head={run.current_head}\n"
        f"phase={run.phase}\nscope={','.join(phase.authorized_scope)}\n"
        f"permitted_routes={routes}\nevidence={','.join(phase.evidence)}\n"
        "output_contract=structured_result_v1\n"
    )


def assemble_prompt(
    run: Run,
    phase: Phase,
    components: Mapping[str, PromptComponent],
    *,
    task_payload: str,
    context: tuple[ContextItem, ...] = (),
    diff: str | None = None,
    diff_base: str | None = None,
    diff_head: str | None = None,
    changed_paths: tuple[str, ...] = (),
) -> PromptPackage:
    """Compose one fresh prompt; all optional content is included or referenced."""

    selected: list[dict[str, object]] = []
    omissions: list[dict[str, object]] = []
    blocks = [_header(run, phase)]
    used = 0
    for component_id in _selected_ids(phase):
        component = components.get(component_id)
        if component is None:
            raise ValueError(f"selected prompt component is unavailable: {component_id}")
        reference = component.reference()
        selected.append(reference)
        if component.byte_size > COMPONENT_LIMIT_BYTES:
            omissions.append({"kind": "component", "reason": "component_limit", **reference})
        elif used + component.byte_size > CONTEXT_LIMIT_BYTES:
            omissions.append({"kind": "component", "reason": "total_limit", **reference})
        else:
            blocks.append(component.content)
            used += component.byte_size
    blocks.append("[workflow_policy]\n" + f"durable_artifact={phase.durable_artifact.value}\n")
    blocks.append("[task]\n" + task_payload)
    for item in context:
        reference = {"reference": item.reference, "bytes": item.byte_size, "retrieval": item.reference}
        if used + item.byte_size > CONTEXT_LIMIT_BYTES:
            omissions.append({"kind": "context", "reason": "total_limit", **reference})
        else:
            blocks.append("[context]\n" + item.content)
            used += item.byte_size
    inline_diff = None
    diff_reference = None
    if diff is not None:
        diff_bytes = len(diff.encode("utf-8"))
        diff_reference = {"base": diff_base, "head": diff_head, "changed_paths": list(changed_paths),
                          "bytes": diff_bytes, "file_count": len(changed_paths),
                          "retrieval": f"git diff {diff_base}..{diff_head}"}
        if diff_bytes > DIFF_LIMIT_BYTES or len(changed_paths) > DIFF_FILE_LIMIT:
            omissions.append({"kind": "diff", "reason": "diff_limit", **diff_reference})
        else:
            inline_diff = diff
            blocks.append("[diff]\n" + diff)
    blocks.append("[result_output_contract]\nReturn one JSON structured_result_v1 envelope; prose cannot replace fields.")
    return PromptPackage("\n\n".join(blocks) + "\n", tuple(selected), tuple(omissions), inline_diff, diff_reference)
