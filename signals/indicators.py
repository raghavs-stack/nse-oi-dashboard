# ════════════════════════════════════════════════════════════════
#  signals/indicators.py
#  Technical indicator classes ported from haripm2211
#  components/indicators.py and components/strategy_engine.py.
# ════════════════════════════════════════════════════════════════

from config import RSI_PERIOD, VWAP_WINDOW, RSI_BULLISH, RSI_BEARISH


class RollingRSI:
    """
    Wilder's RSI using exponential smoothing.
    Returns None until (period + 1) prices have been fed.
    """
    def __init__(self, period: int = RSI_PERIOD):
        self.period   = period
        self.prices   = []
        self.avg_gain = None
        self.avg_loss = None

    def update(self, price: float):
        self.prices.append(price)
        n = len(self.prices)
        if n < self.period + 1:
            return None
        if n == self.period + 1:
            changes       = [self.prices[i] - self.prices[i-1] for i in range(1, n)]
            self.avg_gain = sum(max(0,  c) for c in changes) / self.period
            self.avg_loss = sum(max(0, -c) for c in changes) / self.period
        else:
            chg = price - self.prices[-2]
            self.avg_gain = (self.avg_gain * (self.period - 1) + max(0,  chg)) / self.period
            self.avg_loss = (self.avg_loss * (self.period - 1) + max(0, -chg)) / self.period
        if self.avg_loss == 0:
            return 100.0
        rs = self.avg_gain / self.avg_loss
        return round(100 - (100 / (1 + rs)), 2)

    def reset(self):
        self.prices = []; self.avg_gain = self.avg_loss = None


class RollingVWAP:
    """
    Rolling VWAP over a fixed window.
    When volume is None, defaults to 1.0 per bar (volume-agnostic SMA).
    """
    def __init__(self, window: int = VWAP_WINDOW):
        self.window  = window
        self.prices  = []
        self.volumes = []

    def update(self, price: float, volume: float = None):
        self.prices.append(price)
        self.volumes.append(volume if volume is not None else 1.0)
        if len(self.prices) > self.window:
            self.prices.pop(0); self.volumes.pop(0)
        if len(self.prices) < 2:
            return None
        tv = sum(self.volumes)
        return round(sum(p * v for p, v in zip(self.prices, self.volumes)) / tv, 2) if tv else None

    def reset(self):
        self.prices = []; self.volumes = []


class StrategyEngine:
    """
    Combines RollingRSI + RollingVWAP into a directional signal.
    Direct port of haripm2211 components/strategy_engine.py.

    Signal rules:
      RSI ≥ RSI_BULLISH AND price > VWAP  →  BULLISH
      RSI ≤ RSI_BEARISH AND price < VWAP  →  BEARISH
      otherwise                           →  NEUTRAL
    """
    def __init__(self):
        self.rsi         = RollingRSI()
        self.vwap        = RollingVWAP()
        self.last_signal = "NEUTRAL"
        self.last_rsi    = None
        self.last_vwap   = None

    def on_tick(self, price: float, volume: float = None):
        """Returns (rsi: float|None, vwap: float|None, signal: str)."""
        rsi  = self.rsi.update(price)
        vwap = self.vwap.update(price, volume)
        self.last_rsi  = rsi
        self.last_vwap = vwap
        if rsi is None or vwap is None:
            return None, None, "NEUTRAL"
        trend  = "UP" if price > vwap else "DOWN"
        signal = "NEUTRAL"
        if rsi >= RSI_BULLISH and trend == "UP":
            signal = "BULLISH"
        elif rsi <= RSI_BEARISH and trend == "DOWN":
            signal = "BEARISH"
        self.last_signal = signal
        return rsi, vwap, signal

    def reset(self):
        self.rsi.reset(); self.vwap.reset()
        self.last_signal = "NEUTRAL"
        self.last_rsi = self.last_vwap = None
