"""Screen photo reader (7.3). Phase 1: QR/barcode + quality gate +
stub verdict. The real Spark structured read lands in Phase 2; the
spike (eyewitness/tools/spike.py) proves it against the live API."""
from __future__ import annotations

from .quality import assess_image

PROMPT_VERSION = "screen_read_v1"


def decode_codes(data: bytes) -> list[str]:
    """QR/barcode payloads via zxing-cpp when installed, else []."""
    try:
        import zxingcpp
        from PIL import Image
        import io
        with Image.open(io.BytesIO(data)) as img:
            results = zxingcpp.read_barcodes(img.convert("RGB"))
        return [r.text for r in results if getattr(r, "text", None)]
    except Exception:
        return []


def read_screen(data: bytes) -> dict:
    """{verdict, retake, reading, injection_flag} (reader contract)."""
    check = assess_image(data)
    if check["verdict"] == "retake":
        return {"verdict": "retake", "retake": check["retake"],
                "reading": {"quality": check["quality"]},
                "injection_flag": False}
    codes = decode_codes(data)
    return {"verdict": "ok", "retake": None,
            "reading": {"stub": True, "prompt_version": PROMPT_VERSION,
                        "codes": codes, "quality": check["quality"]},
            "injection_flag": False}
