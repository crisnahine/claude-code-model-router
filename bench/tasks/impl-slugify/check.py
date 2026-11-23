import importlib.util
import sys
from pathlib import Path

_spec = importlib.util.spec_from_file_location("passcheck", Path(__file__).resolve().parent.parent / "passcheck.py")
passcheck = importlib.util.module_from_spec(_spec)
sys.modules["passcheck"] = passcheck
_spec.loader.exec_module(passcheck)


try:
    slugify = passcheck.load(Path(sys.argv[1]), "text_utils").slugify
    cases = {
        "Hello, World!": "hello-world",
        "  Café au lait  ": "cafe-au-lait",
        "a--b__c": "a-b-c",
        "!!!": "",
        "": "",
        "Ünïcödé 2024": "unicode-2024",
        "日本 abc": "abc",
        "Already-a-slug": "already-a-slug",
        "MiXeD 42 Case": "mixed-42-case",
    }
    ok = all(slugify(text) == want for text, want in cases.items())
except BaseException:  # a SystemExit from work-dir code is a failure
    ok = False
sys.exit(0 if ok else 1)
