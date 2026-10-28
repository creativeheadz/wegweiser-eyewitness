"""Phase 2 guards: create_app imports and customer-facing language.

The AST test exists because a plain `import app.eyewitness.models`
inside create_app rebinds the local Flask instance to the package
(the staging 502 of 2026-10-28). The suite builds its app without
create_app, so only this parse sees the fault.

The language test holds customer-facing text to the house rules: no
em or en dashes and none of the crutch words. It scans the evidence
page templates and the modules that hold wording a customer reads.
"""
import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent

BANNED = re.compile(
    r"\b(actually|quietly|silently|genuinely|honestly|honest|properly)\b",
    re.IGNORECASE)
DASHES = ("\u2014", "\u2013")  # em dash, en dash

CUSTOMER_FACING = [
    "app/templates/eyewitness/customer.html",
    "app/templates/eyewitness/expired.html",
    "app/eyewitness/requests.py",
    "app/eyewitness/readers/quality.py",
    "app/eyewitness/readers/screen.py",
    "app/eyewitness/readers/sticker.py",
]


def test_create_app_has_no_plain_app_import():
    tree = ast.parse((ROOT / "app/__init__.py").read_text(encoding="utf-8"))
    create_app = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "create_app")
    offenders = []
    for node in ast.walk(create_app):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "app" or alias.name.startswith("app."):
                    offenders.append(alias.name)
    assert not offenders, (
        "plain import inside create_app rebinds the Flask instance: "
        + ", ".join(offenders))


def test_customer_facing_text_house_language():
    offenders = []
    for rel in CUSTOMER_FACING:
        path = ROOT / rel
        for number, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), 1):
            if any(dash in line for dash in DASHES):
                offenders.append(f"{rel}:{number}: dash in customer text")
            if BANNED.search(line):
                offenders.append(f"{rel}:{number}: {line.strip()[:100]}")
    assert not offenders, "\n".join(offenders)
