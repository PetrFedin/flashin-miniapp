#!/usr/bin/env python3
"""Require governance evidence from the exact protected-branch push workflow."""

from __future__ import annotations

from typing import Any, Mapping


def validate_protected_branch_workflow(
    report: Mapping[str, Any],
    *,
    workflow_path: str,
) -> list[str]:
    errors: list[str] = []
    workflow = report.get("workflow")
    branch = report.get("branch")
    if not isinstance(workflow, Mapping):
        return ["repository governance protected-branch workflow evidence is missing"]
    if workflow.get("event") != "push":
        errors.append("repository governance workflow must be a push run on the protected branch")
    expected_path = f".github/workflows/{workflow_path.lstrip('/')}"
    if workflow.get("path") != expected_path:
        errors.append("repository governance workflow path is not the exact tracked workflow")
    if isinstance(branch, Mapping):
        if workflow.get("head_sha") != branch.get("head_sha"):
            errors.append("repository governance workflow is not for the protected branch head")
    else:
        errors.append("repository governance protected branch evidence is missing")
    return list(dict.fromkeys(errors))
