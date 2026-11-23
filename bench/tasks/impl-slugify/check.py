import importlib.util
import sys
from pathlib import Path

_spec = importlib.util.spec_from_file_location("passcheck", Path(__file__).resolve().parent.parent / "passcheck.py")
passcheck = importlib.util.module_from_spec(_spec)
sys.modules["passcheck"] = passcheck
_spec.loader.exec_module(passcheck)


CASES = {
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
with passcheck.agent_code():
    slugify = passcheck.load(Path(sys.argv[1]), "text_utils").slugify
    got = [slugify(text) for text in CASES]
passcheck.finish(got == list(CASES.values()), passcheck.differs(got, list(CASES.values())))
