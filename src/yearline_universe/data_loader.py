"""Price data loading for the V13 universe engine.

Ticker-agnostic. Supports four providers, tried in order:

1. ``cache``   - a local per-ticker CSV cache (``{cache_dir}/{TICKER}.csv``) of
   fully split/dividend-adjusted OHLCV bars (yfinance ``auto_adjust=True``
   format). Used for reproducible / offline runs.
2. ``tiingo``  - live download from the Tiingo daily-prices REST API (keyed via the
   ``TIINGO_API_KEY`` env var; uses the **adjusted** fields to match ``auto_adjust``).
   A reliable, authenticated source for a scheduled cron (not IP-blocked like Yahoo).
   No key ⇒ this provider returns ``None`` and the chain falls through (back-compat).
3. ``yfinance`` - live download via the ``yfinance`` package.
4. ``yahoo_chart`` - direct Yahoo v8 chart API via ``requests`` (honours the
   HTTPS proxy) with optional ``curl_cffi`` browser impersonation.

The default ``provider="auto"`` walks the list until one succeeds, so the same
engine code runs offline (cache) or live (tiingo/yfinance/chart) without changes.
Because ``tiingo`` no-ops without a key, ``auto`` behaves exactly as before unless
``TIINGO_API_KEY`` is set. Reference: ``docs/reference/data_providers.md``.

This is a faithful port of V12's ``load_price_data`` /
``standardize_price_df`` plus a live-capable fallback chain.
"""

from __future__ import annotations

import io
import json
import os
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


def _tiingo_frame_from_csv(text: str, ticker: str, config: StudyConfig) -> pd.DataFrame | None:
    """Parse a Tiingo ``/tiingo/daily/{ticker}/prices`` CSV into a standardized OHLCV frame.

    Tiingo CSV columns: ``date,close,high,low,open,volume,adjClose,adjHigh,adjLow,adjOpen,
    adjVolume,divCash,splitFactor``. To match the engine's ``auto_adjust=True`` convention we map
    the **adjusted** fields (``adj*``) onto ``Open/High/Low/Close/Volume`` when available; otherwise
    the raw fields (so a non-adjusted feed still parses, with a caveat — see the data-providers ref).
    """
    try:
        df = pd.read_csv(io.StringIO(text))
    except Exception:
        return None
    if df is None or df.empty or "date" not in df.columns:
        return None
    df = df.set_index("date")
    adj = ["adjOpen", "adjHigh", "adjLow", "adjClose", "adjVolume"]
    raw = ["open", "high", "low", "close", "volume"]
    use_adj = bool(getattr(config, "auto_adjust", True)) and all(c in df.columns for c in adj)
    src = adj if use_adj else raw
    if not all(c in df.columns for c in src):
        return None
    out = df[src].copy()
    out.columns = ["Open", "High", "Low", "Close", "Volume"]
    return _slice_window(standardize_price_df(out, ticker), config)


def _load_from_tiingo(ticker: str, config: StudyConfig) -> pd.DataFrame | None:
    """Live download from Tiingo's daily-prices API. Keyed via ``TIINGO_API_KEY``.

    Returns ``None`` (so the provider chain falls through) when: no key is set; the request fails;
    a non-2xx / empty response; or Tiingo returns a JSON error/throttle body instead of CSV.
    """
    key = os.environ.get("TIINGO_API_KEY")
    if not key:
        return None                                       # no key ⇒ fall through the chain (back-compat)
    start = str(config.start)[:10] if getattr(config, "start", None) else "1990-01-01"
    url = f"https://api.tiingo.com/tiingo/daily/{ticker}/prices?startDate={start}&format=csv"
    if getattr(config, "end", None):
        url += f"&endDate={str(config.end)[:10]}"
    try:
        import requests
        # Key in the Authorization header (not the URL) so it can't leak into logs/proxies.
        r = requests.get(url, headers={"Authorization": f"Token {key}"}, timeout=40)
    except Exception:
        return None
    if getattr(r, "status_code", None) != 200 or not getattr(r, "text", ""):
        return None
    head = r.text.lstrip()[:1]
    if head in ("{", "["):                                # JSON body ⇒ Tiingo error/throttle, not CSV data
        return None
    return _tiingo_frame_from_csv(r.text, ticker, config)


_PROVIDERS = {
    "cache": _load_from_cache,
    "tiingo": lambda t, c, d: _load_from_tiingo(t, c),
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
        "auto"        - cache -> tiingo -> yfinance -> yahoo_chart (default; tiingo no-ops without a key)
        "cache"       - cache only
        "tiingo"      - live Tiingo only (requires TIINGO_API_KEY)
        "yfinance"    - live yfinance only
        "yahoo_chart" - direct Yahoo chart API only
    """
    config = config or StudyConfig()
    cache_dir = Path(cache_dir) if cache_dir is not None else DEFAULT_CACHE_DIR

    if provider == "auto":
        live = ["tiingo", "yfinance", "yahoo_chart"]
        order = live if force_download else ["cache", *live]
    else:
        order = [provider]

    errors: list[str] = []
    for name in order:
        fn = _PROVIDERS.get(name)
        if fn is None:
            errors.append(f"{name}: unknown provider")
            continue
        try:
            df = fn(ticker, config, cache_dir)
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
