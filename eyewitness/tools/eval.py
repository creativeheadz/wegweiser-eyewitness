#!/usr/bin/env python3
"""Golden-set evaluation stub (brief section 10).

Phase 4 fills this in: run each reader over EYEWITNESS_GOLDEN_DIR and
report accuracy per reader plus cost per ticket from the Meta call
log. No paid calls happen in tests; this tool is run deliberately.
"""
import os
import sys


def main() -> int:
    golden = os.environ.get("EYEWITNESS_GOLDEN_DIR", "")
    if not golden or not os.path.isdir(golden):
        print("EYEWITNESS_GOLDEN_DIR not set or missing; nothing to evaluate")
        return 2
    print(f"golden dir: {golden} (reader eval lands in Phase 4)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
