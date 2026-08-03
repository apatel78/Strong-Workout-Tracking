from __future__ import annotations

import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import test_sync  # noqa: E402


def main() -> int:
    tests = [(n, f) for n, f in vars(test_sync).items() if n.startswith("test_") and callable(f)]
    passed, failed = 0, []
    for name, fn in tests:
        try:
            fn()
        except Exception:
            failed.append((name, traceback.format_exc()))
            print(f"FAIL {name}")
        else:
            passed += 1
            print(f"ok   {name}")

    print(f"\n{passed} passed, {len(failed)} failed, {len(tests)} total")
    for name, tb in failed:
        print(f"\n--- {name} ---\n{tb}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    sys.exit(main())
