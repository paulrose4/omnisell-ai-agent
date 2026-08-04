from __future__ import annotations

import ast
import re
from dataclasses import dataclass

MAX_CODE_LENGTH = 10_000

ALLOWED_IMPORTS = {
    "collections",
    "datetime",
    "decimal",
    "functools",
    "hashlib",
    "itertools",
    "json",
    "math",
    "random",
    "re",
    "requests",
    "statistics",
    "string",
    "time",
    "typing",
}

BLOCKED_PATTERNS = [
    r"\bos\.system\s*\(",
    r"\bsubprocess\b",
    r"\beval\s*\(",
    r"\bexec\s*\(",
    r"\bcompile\s*\(",
    r"\b__import__\s*\(",
    r"\bopen\s*\(",
    r"\binput\s*\(",
    r"\bglobals\s*\(",
    r"\blocals\s*\(",
    r"\bvars\s*\(",
    r"\bgetattr\s*\(",
    r"\bsetattr\s*\(",
    r"\bdelattr\s*\(",
    r"\bimportlib\b",
    r"\bsocket\b",
    r"\bshutil\b",
    r"\bpathlib\b",
    r"\bpickle\b",
    r"\bmarshal\b",
    r"\bctypes\b",
    r"\bmultiprocessing\b",
    r"\bthreading\b",
    r"\bfrom\s+os\s+import\b",
]


@dataclass(frozen=True)
class PolicyViolation:
    reason: str
    detail: str


class SandboxPolicyError(ValueError):
    """Raised when code violates sandbox policy."""

    def __init__(self, violations: list[PolicyViolation]) -> None:
        self.violations = violations
        message = "; ".join(f"{item.reason}: {item.detail}" for item in violations)
        super().__init__(message)


def validate_code(code: str) -> None:
    """Validate code against length, blocked pattern, and import policy rules."""
    violations: list[PolicyViolation] = []

    if len(code) > MAX_CODE_LENGTH:
        violations.append(PolicyViolation("max_code_length", f"{len(code)} > {MAX_CODE_LENGTH}"))

    for pattern in BLOCKED_PATTERNS:
        if re.search(pattern, code):
            violations.append(PolicyViolation("blocked_pattern", pattern))

    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        violations.append(PolicyViolation("syntax_error", str(exc)))
        raise SandboxPolicyError(violations) from exc

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root_name = alias.name.split(".", 1)[0]
                if root_name not in ALLOWED_IMPORTS:
                    violations.append(PolicyViolation("blocked_import", alias.name))
        elif isinstance(node, ast.ImportFrom):
            root_name = (node.module or "").split(".", 1)[0]
            if root_name not in ALLOWED_IMPORTS:
                violations.append(PolicyViolation("blocked_import", node.module or "<relative>"))

    if violations:
        raise SandboxPolicyError(violations)


if __name__ == "__main__":
    validate_code("import json\nprint(json.dumps({'ok': True}))")
    print("sandbox policy ok")
