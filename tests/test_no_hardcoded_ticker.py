"""Guard: no ticker symbol is hardcoded as a string literal in library logic.

Parses each module with ``ast`` and collects string *constants* whose value is
exactly a ticker symbol. Docstrings don't match (their value is the whole doc
paragraph, not a bare symbol), so prose references to MSFT in docs are allowed.
"""
import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "yearline_universe"
TICKERS = {"MSFT", "AAPL", "NVDA", "GOOGL", "GOOG", "AMZN", "META", "TSLA", "JPM", "XOM", "JNJ", "WMT", "CAT"}


def test_no_hardcoded_ticker_string_literals():
    offenders = []
    for path in SRC.glob("*.py"):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if node.value.strip() in TICKERS:
                    offenders.append(f"{path.name}:{node.lineno} -> {node.value!r}")
    assert not offenders, "Hardcoded ticker literal(s) found in library code:\n" + "\n".join(offenders)
