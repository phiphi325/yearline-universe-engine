#!/usr/bin/env python3
"""Producer-side contract test (CI): validate every committed YearlineContext / YearlineTrendSeries
fixture against the **pinned in-code JSON schemas** + version pins, and assert the committed
``*_schema.json`` files have not drifted from the adapter's in-code schemas.

This is the bytes option-mgmt-2026 vendors (OM-Y1), so it is the cross-repo contract's producer side.
Run on every push/PR (``.github/workflows/ci.yml``) and before publishing a nightly artifact.

Uses ``jsonschema`` for deep validation when available; always performs the structural checks
(required keys, ``additionalProperties:false``, ``must_not_auto_execute``) so it is useful even without
the optional dep. Exit code 0 = all conform; 1 = at least one drift/failure (with a printed report).

Educational research only; not financial advice; ``must_not_auto_execute``.
"""
from __future__ import annotations

import glob
import json
import os
import sys

from yearline_universe.adapter import (
    YEARLINE_CONTEXT_JSON_SCHEMA, YEARLINE_TREND_SERIES_JSON_SCHEMA,
    ADAPTER_VERSION, TREND_SERIES_VERSION,
)

DIRS = ["exports/yearline_context", "docs/phased_design/phase_09/artifacts"]


def _norm(schema):
    """Round-trip through JSON so in-code Python None/True compare to null/true forms."""
    return json.loads(json.dumps(schema))


def _classify(name: str) -> str:
    if name.endswith("_schema.json"):
        return "schema"
    if "trend_series" in name:
        return "series"
    return "context"


def _structural(inst, schema, path, errors):
    if not isinstance(inst, dict):
        errors.append(f"{path}: top-level must be an object"); return
    for k in schema.get("required", []):
        if k not in inst:
            errors.append(f"{path}: missing required key {k!r}")
    props = schema.get("properties", {})
    if schema.get("additionalProperties") is False:
        for k in inst:
            if k not in props:
                errors.append(f"{path}: unexpected key {k!r} (additionalProperties:false)")
    if inst.get("must_not_auto_execute") is not True:
        errors.append(f"{path}: must_not_auto_execute must be true")
    try:
        import jsonschema
        jsonschema.validate(inst, schema)
    except ImportError:
        pass
    except Exception as e:                                   # jsonschema.ValidationError etc.
        errors.append(f"{path}: jsonschema validation failed ({type(e).__name__}: {e})")


def main() -> int:
    errors: list[str] = []
    checked = {"context": 0, "series": 0, "schema": 0}
    seen_dirs = 0

    for d in DIRS:
        if not os.path.isdir(d):
            continue
        seen_dirs += 1
        for path in sorted(glob.glob(os.path.join(d, "*.json"))):
            name = os.path.basename(path)
            kind = _classify(name)
            try:
                data = json.load(open(path, encoding="utf-8"))
            except Exception as e:
                errors.append(f"{path}: unreadable JSON ({e})")
                continue

            if kind == "schema":
                expected = (YEARLINE_CONTEXT_JSON_SCHEMA if name.startswith("yearline_context")
                            else YEARLINE_TREND_SERIES_JSON_SCHEMA)
                if data != _norm(expected):
                    errors.append(f"{path}: committed schema DRIFTS from the in-code schema "
                                  f"(re-export it from adapter.py)")
            elif kind == "context":
                _structural(data, YEARLINE_CONTEXT_JSON_SCHEMA, path, errors)
                if data.get("adapter_version") != ADAPTER_VERSION:
                    errors.append(f"{path}: adapter_version {data.get('adapter_version')!r} "
                                  f"!= pinned {ADAPTER_VERSION!r}")
            else:  # series
                _structural(data, YEARLINE_TREND_SERIES_JSON_SCHEMA, path, errors)
                if data.get("series_version") != TREND_SERIES_VERSION:
                    errors.append(f"{path}: series_version {data.get('series_version')!r} "
                                  f"!= pinned {TREND_SERIES_VERSION!r}")
            checked[kind] += 1

    if seen_dirs == 0:
        print("no fixture directories found — nothing to validate", file=sys.stderr)
        return 1

    print(f"checked: {checked['context']} context, {checked['series']} series, "
          f"{checked['schema']} schema file(s) across {seen_dirs} dir(s)")
    print(f"pins: ADAPTER_VERSION={ADAPTER_VERSION} · TREND_SERIES_VERSION={TREND_SERIES_VERSION}")
    if errors:
        print("\nCONTRACT VALIDATION FAILED:")
        for e in errors:
            print("  -", e)
        return 1
    print("OK — all committed contract artifacts conform to the pinned schemas + version pins")
    return 0


if __name__ == "__main__":
    sys.exit(main())
