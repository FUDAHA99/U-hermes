"""Build/setup offline-model gate; no downloads and no model API calls."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from recruit_ocr import OCRSession, verify_models
from recruit_core import RecruitError


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    try:
        paths = verify_models()
        # Imports native runtime and actually loads the three local sessions;
        # hashing alone cannot detect missing DLLs or incompatible wheels.
        OCRSession()
    except Exception as exc:
        print(str(exc) if isinstance(exc, RecruitError) else "Offline OCR component initialization failed.")
        return 1
    size = sum(path.stat().st_size for path in paths.values())
    print(f"Offline OCR ready: 3 verified local models, {size} bytes; CPU only.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
