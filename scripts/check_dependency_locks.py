from __future__ import annotations

import difflib
import shutil
import subprocess
import tempfile
from pathlib import Path

UV_VERSION = "0.12.1"
ROOT = Path(__file__).resolve().parents[1]
LOCKS = (
    ("requirements.runtime.txt", "requirements.runtime.lock.txt"),
    ("requirements.txt", "requirements.lock.txt"),
    ("mock_platform/requirements.txt", "mock_platform/requirements.lock.txt"),
    ("local_e2e_mock/requirements.txt", "local_e2e_mock/requirements.lock.txt"),
)


def _compile_command(source: str, target: str) -> str:
    return f"uv pip compile --universal --python-version 3.12 --generate-hashes {source} -o {target}"


def _normalized_lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines()


def main() -> int:
    version = subprocess.run(
        ["uv", "--version"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if not version.startswith(f"uv {UV_VERSION} ") and version != f"uv {UV_VERSION}":
        raise SystemExit(f"Expected uv {UV_VERSION}, found {version!r}.")

    stale = False
    with tempfile.TemporaryDirectory(prefix="omnisell-lock-check-") as temp_dir:
        temp_root = Path(temp_dir)
        for source, target in LOCKS:
            generated = temp_root / target
            generated.parent.mkdir(parents=True, exist_ok=True)
            # uv treats an existing output file as the preferred locked solution.
            # Seed the temporary output so the check validates reproducibility
            # without silently upgrading unrelated transitive dependencies.
            shutil.copyfile(ROOT / target, generated)
            command = _compile_command(source, target)
            compile_result = subprocess.run(
                [
                    "uv",
                    "pip",
                    "compile",
                    "--universal",
                    "--python-version",
                    "3.12",
                    "--generate-hashes",
                    "--quiet",
                    "--custom-compile-command",
                    command,
                    source,
                    "-o",
                    str(generated),
                ],
                cwd=ROOT,
                check=True,
                capture_output=True,
                text=True,
            )
            # uv occasionally emits useful non-fatal diagnostics on stderr.
            # Keep successful CI output concise while preserving them on drift.
            compile_diagnostics = compile_result.stderr or compile_result.stdout
            committed_lines = _normalized_lines(ROOT / target)
            generated_lines = _normalized_lines(generated)
            if committed_lines == generated_lines:
                continue
            stale = True
            if compile_diagnostics:
                print(compile_diagnostics.rstrip())
            print(f"{target} is stale. Regenerate it with:")
            print(f"  {command}")
            print(
                "\n".join(
                    difflib.unified_diff(
                        committed_lines,
                        generated_lines,
                        fromfile=target,
                        tofile=f"generated/{target}",
                        lineterm="",
                        n=3,
                    )
                )
            )

    if stale:
        return 1
    print("Dependency lock files are reproducible and up to date.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
