"""The one check command. Pre-commit and CI both run exactly this.

Usage: uv run python tools/check.py [--fix]
"""

import subprocess
import sys
import time

FIX_STEPS: list[tuple[str, list[str]]] = [
    ("format", ["ruff", "format"]),
    ("lint", ["ruff", "check", "--fix"]),
]

CHECK_STEPS: list[tuple[str, list[str]]] = [
    ("format", ["ruff", "format", "--check"]),
    ("lint + complexity", ["ruff", "check"]),
    ("types", ["mypy"]),
    ("dead code", ["vulture"]),
    ("module boundaries", ["lint-imports"]),
    ("file layout", [sys.executable, "tools/check_structure.py"]),
    ("tests", ["pytest", "-q"]),
]


def run(name: str, command: list[str]) -> bool:
    """Run one step, report its result, and return whether it passed."""
    started = time.monotonic()
    result = subprocess.run(command, check=False)
    elapsed = time.monotonic() - started
    status = "ok" if result.returncode == 0 else "FAILED"
    print(f"--- {name}: {status} ({elapsed:.1f}s)", flush=True)
    return result.returncode == 0


def main() -> int:
    """Run every step, even after a failure, so one run shows all problems."""
    steps = FIX_STEPS + CHECK_STEPS if "--fix" in sys.argv else CHECK_STEPS
    failed = [name for name, command in steps if not run(name, command)]
    if failed:
        print(f"\nFAILED: {', '.join(failed)}", file=sys.stderr)
        return 1
    print("\nAll checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
