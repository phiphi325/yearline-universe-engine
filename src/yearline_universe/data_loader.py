"""Price data loading for the V13 universe engine.

Ticker-agnostic. Supports three providers, tried in order:

1. ``cache``   - a local per-ticker CSV cache (``{cache_dir}/{TICKER}.csv``) of
   fully split/dividend-adjusted OHLCV bars (yfinance ``auto_adjust=True``
   format). Used for reproducible / offline runs.
2. ``yfinance`` - live download via the ``yfinance`` package.
3. ``yahoo_chart`` - direct Yahoo v8 chart API via ``requests`` (honours the
   HTTPS proxy) with optional ``curl_cffi`` browser impersonation.

The default ``provider="auto"`` walks the list until one succeeds, so the same
engine code runs offline (cache) or live (yfinance/chart) without changes.

This is a faithful port of V12's ``load_price_data`` /
``standardize_price_df`` plus a live-capable fallback chain.
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .config import StudyConfig

__all__ = [
    "standardize_price_df",
    "load_price_data",
    "DEFAULT_CACHE_DIR",
]

# Repo-relative default cache directory (../../data/price_cache from this file).
DEFAULT_CACHE_DIR = Path(__file__).resolve().parents[2] / "data" / "price_cache"


# ---------------------------------------------------------------------------
# Standardisation (ported from V12)
# ---------------------------------------------------------------------------

def _flatten_yfinance_columns(df: pd.DataFrame, ticker: str) -> pd.DataFrame:
    if isinstance(df.columns, pd.MultiIndex):
        levels = [list(map(str, lev)) for lev in df.columns.levels]
        if ticker in levels[0]:
            df = df[ticker].copy()
        elif ticker in levels[-1]:
            df = df.xs(ticker, axis=1, level=-1).copy()
        else:
            df.columns = ["_".join(map(str, c)).strip() for c in df.columns]
    return df


def standardize_price_df(raw: pd.DataFrame, ticker: str) -> pd.DataFrame:
    """Return a clean OHLCV frame indexed by tz-naive Date.

    Accepts yfinance auto-adjusted output or a plain OHLCV CSV.
    """
    df = raw.copy()
    df = _flatten_yfinance_columns(df, ticker)
    df.columns = [str(c).strip().title().replace(" ", "_") for c in df.columns]

    rename = {
        "Adj_Close": "Close",
        "Adjclose": "Close",
        "Adjusted_Close": "Close",
    }
    # Only fold Adj Close into Close when a separate Close is absent; otherwise
    # keep the (already adjusted) Close to avoid double-counting.
    if "Close" in df.columns:
        rename = {k: v for k, v in rename.items() if k != "Adj_Close" or "Close" not in df.columns}
        for k in ("Adjclose", "Adjusted_Close"):
            rename.pop(k, None)
    df = df.rename(columns=rename)

    required = ["Open", "High", "Low", "Close"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(
            f"{ticker}: missing required OHLC columns: {missing}; got {list(df.columns)}"
        )

    if "Volume" not in df.columns:
        df["Volume"] = np.nan

    df = df[["Open", "High", "Low", "Close", "Volume"]].copy()
    df = df.apply(pd.to_numeric, errors="coerce")
    df = df.dropna(subset=["Open", "High", "Low", "Close"])
    df = df.sort_index()
    df.index = pd.to_datetime(df.index, utc=True).tz_localize(None)
    df.index.name = "Date"
    return df


def _slice_window(df: pd.DataFrame, config: StudyConfig) -> pd.DataFrame:
    out = df
    if config.start:
        out = out[out.index >= pd.Timestamp(config.start)]
    if config.end:
        # yfinance end is exclusive; mirror that.
        out = out[out.index < pd.Timestamp(config.end)]
    return out.copy()


# ---------------------------------------------------------------------------
# Providers
# ---------------------------------------------------------------------------

def _load_from_cache(ticker: str, config: StudyConfig, cache_dir: Path) -> pd.DataFrame | None:
    csv_path = cache_dir / f"{ticker}.csv"
    if not csv_path.exists():
        return None
    raw = pd.read_csv(csv_path)
    # Identify the date column.
    date_col = next((c for c in raw.columns if str(c).strip().lower() in ("date", "datetime")), raw.columns[0])
    raw = raw.set_index(date_col)
    df = standardize_price_df(raw, ticker)
    return _slice_window(df, config)


def _load_from_yfinance(ticker: str, config: StudyConfig) -> pd.DataFrame | None:
    try:
        import yfinance as yf
    except Exception:
        return None
    raw = yf.download(
        ticker,
        start=config.start,
        end=config.end,
        auto_adjust=config.auto_adjust,
        progress=False,
        threads=False,
    )
    if raw is None or raw.empty:
        return None
    return standardize_price_df(raw, ticker)


def _load_from_yahoo_chart(ticker: str, config: StudyConfig) -> pd.DataFrame | None:
    """Direct Yahoo v8 chart API. Uses curl_cffi if available, else requests."""
    p1 = int(pd.Timestamp(config.start or "1990-01-01").timestamp())
    p2 = int(pd.Timestamp(config.end).timestamp()) if config.end else int(pd.Timestamp.utcnow().timestamp())
    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
        f"?period1={p1}&period2={p2}&interval=1d"
    )
    text = None
    try:
        from curl_cffi import requests as creq  # type: ignore
        r = creq.get(url, impersonate="chrome", timeout=40)
        if r.status_code == 200:
            text = r.text
    except Exception:
        text = None
    if text is None:
        try:
            import requests
            r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=40)
            if r.status_code == 200:
                text = r.text
        except Exception:
            return None
    if not text:
        return None
    try:
        j = json.loads(text)
        res = j["chart"]["result"][0]
        ts = res["timestamp"]
        q = res["indicators"]["quote"][0]
        adj = res["indicators"].get("adjclose", [{}])[0].get("adjclose")
        idx = pd.to_datetime(ts, unit="s")
        df = pd.DataFrame(
            {
                "Open": q["open"],
                "High": q["high"],
                "Low": q["low"],
                "Close": adj if (config.auto_adjust and adj) else q["close"],
                "Volume": q["volume"],
            },
            index=idx,
        )
        return standardize_price_df(df, ticker)
    except Exception:
        return None


_PROVIDERS = {
    "cache": _load_from_cache,
    "yfinance": lambda t, c, d: _load_from_yfinance(t, c),
    "yahoo_chart": lambda t, c, d: _load_from_yahoo_chart(t, c),
}


def load_price_data(
    ticker: str,
    *,
    config: StudyConfig | None = None,
    cache_dir: str | Path | None = None,
    provider: str = "auto",
    force_download: bool = False,
) -> pd.DataFrame:
    """Load standardized OHLCV for one ticker.

    provider:
        "auto"        - cache -> yfinance -> yahoo_chart (default)
        "cache"       - cache only
        "yfinance"    - live yfinance only
        "yahoo_chart" - direct Yahoo chart API only
    """
    config = config or StudyConfig()
    cache_dir = Path(cache_dir) if cache_dir is not None else DEFAULT_CACHE_DIR

    if provider == "auto":
        order = ["yfinance", "yahoo_chart"] if force_download else ["cache", "yfinance", "yahoo_chart"]
    else:
        order = [provider]

    errors: list[str] = []
    for name in order:
        try:
            if name == "cache":
                df = _load_from_cache(ticker, config, cache_dir)
            elif name == "yfinance":
                df = _load_from_yfinance(ticker, config)
            else:
                df = _load_from_yahoo_chart(ticker, config)
        except Exception as exc:  # pragma: no cover - defensive
            errors.append(f"{name}: {exc}")
            df = None
        if df is not None and not df.empty:
            df.attrs["provider"] = name
            df.attrs["ticker"] = ticker
            return df
        errors.append(f"{name}: no data")

    raise ValueError(
        f"{ticker}: could not load price data via {order}. Details: {'; '.join(errors)}"
    )
