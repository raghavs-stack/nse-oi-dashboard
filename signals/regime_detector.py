# ════════════════════════════════════════════════════════════════
#  signals/regime_detector.py  — v5.10
#
#  Market regime classification for HIGH-PROBABILITY OPTION BUYING
#  ──────────────────────────────────────────────────────────────────
#  Algorithm: Rescaled Range (R/S) Hurst exponent on LOG RETURNS
#  Reference: Hurst (1951), Peters (1994) "Fractal Market Analysis"
#
#  H > 0.58 → TRENDING   — persistent momentum → buy directional options
#  0.42 < H ≤ 0.58 → RANGING  — near-random → iron condors / avoid
#  H ≤ 0.42 → MEAN_REVERTING — anti-persistent → fade moves / sell
#
#  Key NSE insight (Citadel/quant view):
#    Strong trending days (H~0.7–0.9) occur ~40% of sessions.
#    On those days, ATM option buyers capture 80%+ of annual profits.
#    The other 60% (ranging/MR) destroy premium buyers via theta decay.
#    → The Hurst gate is the single highest-value filter for option BUYERS.
#
#  NumPy 2.x compatible — no deprecated ptp() usage.
#  Returns RANGING (safe default) while warming up (< 30 observations).
# ════════════════════════════════════════════════════════════════

from __future__ import annotations
import math
from typing import Optional

import numpy as np


# ── Thresholds ────────────────────────────────────────────────────
HURST_TRENDING    = 0.58
HURST_MR          = 0.42
MIN_HURST_WINDOW  = 30
BREAKOUT_SIGMA    = 2.0
PANIC_VIX         = 28.0


def hurst_exponent(series: list, min_window: int = MIN_HURST_WINDOW) -> Optional[float]:
    """
    R/S Hurst exponent on log returns. NumPy 2.x compatible.
    H > 0.58 = trending; H < 0.42 = mean-reverting; 0.42–0.58 = ranging.
    """
    try:
        prices = [float(p) for p in series if p > 0]
        if len(prices) < min_window + 1:
            return None
        log_rets = np.array([
            math.log(prices[i] / prices[i - 1])
            for i in range(1, len(prices))
        ], dtype=float)
        n = len(log_rets)
        if n < 20:
            return None
        block_sizes, rs_vals = [], []
        n_steps = max(4, n // 20)
        for size in range(10, n // 2, n_steps):
            rs_block = []
            for start in range(0, n - size + 1, size):
                block = log_rets[start: start + size]
                if len(block) < 8:
                    continue
                b   = block - block.mean()
                cum = np.cumsum(b)
                R   = float(cum.max() - cum.min())   # ptp() replaced
                S   = float(block.std(ddof=1))
                if S > 0:
                    rs_block.append(R / S)
            if rs_block:
                block_sizes.append(math.log(size))
                rs_vals.append(math.log(float(np.mean(rs_block))))
        if len(block_sizes) < 4:
            return None
        slope, _ = np.polyfit(block_sizes, rs_vals, 1)
        return round(max(0.05, min(0.95, float(slope))), 3)
    except Exception:
        return None


def lag1_autocorr(series: list, window: int = 20) -> Optional[float]:
    """Pearson lag-1 autocorrelation. Positive = momentum."""
    try:
        prices = [float(p) for p in series[-window - 2:] if p > 0]
        if len(prices) < window + 1:
            return None
        rets = np.diff(np.log(np.array(prices, dtype=float)))
        if len(rets) < window:
            return None
        rets = rets[-window:]
        corr = float(np.corrcoef(rets[:-1], rets[1:])[0, 1])
        return round(corr, 3) if not math.isnan(corr) else None
    except Exception:
        return None


def vol_regime(spot_series: list, vix: float = 0.0) -> dict:
    """20/60-cycle realized vol ratio → vol regime label."""
    _empty = {"regime": "NORMAL", "vol_z": 0.0, "expanding": False,
               "rv20": 0.0, "rv60": 0.0}
    try:
        prices = np.array([float(p) for p in spot_series if p > 0], dtype=float)
        if len(prices) < 22:
            return _empty
        log_rets = np.diff(np.log(prices))
        rv20 = float(np.std(log_rets[-20:], ddof=1)) * 100.0 if len(log_rets) >= 20 else 0.0
        rv60 = float(np.std(log_rets[-60:], ddof=1)) * 100.0 if len(log_rets) >= 60 else rv20
        vol_z     = (rv20 - rv60) / rv60 if rv60 > 0 else 0.0
        expanding = rv20 > rv60 * 1.25
        if vix > PANIC_VIX:
            label = "PANIC"
        elif vol_z > BREAKOUT_SIGMA:
            label = "BREAKOUT"
        elif vol_z > 0.8:
            label = "ELEVATED"
        elif vol_z < -0.4:
            label = "CALM"
        else:
            label = "NORMAL"
        return {"regime": label, "vol_z": round(vol_z, 2), "expanding": expanding,
                "rv20": round(rv20, 4), "rv60": round(rv60, 4)}
    except Exception:
        return _empty


def classify_regime(
    spot_series: list,
    vix:         float = 0.0,
    bias:        str   = "NEUTRAL",
) -> dict:
    """
    Master regime classifier. Call once per cycle.
    Returns regime, hurst, autocorr, vol_info, buy_options, regime_score, regime_note.
    """
    H     = hurst_exponent(spot_series)
    ac    = lag1_autocorr(spot_series)
    vinfo = vol_regime(spot_series, vix)
    vol_lbl   = vinfo.get("regime", "NORMAL")
    expanding = vinfo.get("expanding", False)

    if vol_lbl == "PANIC":
        regime, buy_options = "PANIC", (bias == "BEARISH")
        note, score = "VIX spike — only PE buys on fear premium", 40

    elif vol_lbl in ("BREAKOUT", "ELEVATED") and expanding:
        regime, buy_options = "BREAKOUT", True
        note, score = "Vol expanding — ATM straddle or directional buy", 75

    elif H is not None and H >= HURST_TRENDING and (ac is None or ac > 0.10):
        # Require BOTH high Hurst AND positive autocorrelation to avoid
        # false-positive trending signals from short noisy series.
        ac_pos = (ac is not None and ac > 0.10)
        ac_neg = (ac is not None and ac < -0.10)
        if bias == "BULLISH" and (ac_pos or ac is None):
            regime = "TRENDING_UP"
        elif bias == "BEARISH" and (ac_neg or ac is None):
            regime = "TRENDING_DOWN"
        else:
            regime = "TRENDING"
        buy_options = True
        ac_str = f"{ac:.2f}" if ac is not None else "N/A"
        note  = f"Persistent trend (H={H:.2f}, ac={ac_str}) — options have edge"
        score = 85

    elif H is not None and H <= HURST_MR:
        regime, buy_options = "MEAN_REVERTING", False
        note  = f"Mean-reverting (H={H:.2f}) — options decay fast, avoid buying"
        score = 20

    else:
        h_str = f"{H:.2f}" if H is not None else "N/A"
        regime, buy_options = "RANGING", False
        note  = f"Near-random walk (H={h_str}) — spreads/condors better"
        score = 45

    return {
        "regime":       regime,
        "hurst":        H,
        "autocorr":     ac,
        "vol_info":     vinfo,
        "buy_options":  buy_options,
        "regime_score": score,
        "regime_note":  note,
    }
