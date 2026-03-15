# ════════════════════════════════════════════════════════════════
#  signals/ml_signal.py  — v5.9  ML Ensemble Signal Layer
#
#  Integrates four ML libraries into the OI signal pipeline:
#
#   Library         Model               Role
#   ─────────────── ─────────────────── ───────────────────────────
#   sklearn         RandomForest        Robust baseline (100 trees)
#   sklearn         LogisticRegression  Fast interpretable baseline
#   xgboost         XGBClassifier       Best-in-class tabular data
#   mxnet / gluon   3-layer MLP         Non-linear pattern capture
#
#  Each model predicts: will spot move in the current Bias direction
#  in the next cycle? (1 = correct direction, 0 = wrong)
#
#  Ensemble: soft voting (average of 4 probabilities → confidence ∈ [0,1])
#
#  Integration:
#   • 10th factor in score_signal() — up to +10 / -5 pts
#   • Written into _ctx and state JSON for display in Streamlit + terminal
#   • Models are re-trained every ML_RETRAIN_CYCLES live cycles
#   • Graceful: returns confidence=0 when not yet trained or libs missing
#
#  Library fallbacks (all auto-detected at import time):
#   xgboost missing → sklearn GradientBoostingClassifier
#   mxnet   missing → sklearn MLPClassifier
#
#  Training data:
#   • Reads all SYMBOL_OI_*.csv files in CWD
#   • Requires MIN_TRAIN_ROWS rows after label computation
#   • Falls back to synthetic data generation if insufficient history
#
#  Features (12 per cycle):
#   pcr_local, pcr_vs_neutral, score_norm, atm_iv_norm,
#   ivr_norm, ivp_norm, spot_vs_maxpain_pct,
#   spot_vs_resistance_pct, spot_vs_support_pct,
#   time_min_norm, is_bearish, is_bullish
# ════════════════════════════════════════════════════════════════

from __future__ import annotations

import os
import glob
import pickle
import logging
from datetime import datetime
from typing import Optional

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

# ── Optional library detection ────────────────────────────────────
_HAS_XGBOOST = False
_HAS_MXNET   = False

try:
    import xgboost as xgb
    _HAS_XGBOOST = True
except ImportError:
    from sklearn.ensemble import GradientBoostingClassifier as _GBC  # noqa: F401

try:
    import mxnet as mx
    from mxnet.gluon import nn as gluon_nn
    from mxnet import nd as mxnd
    _HAS_MXNET = True
except ImportError:
    from sklearn.neural_network import MLPClassifier as _MLP  # noqa: F401

from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import Pipeline

# ── Constants ─────────────────────────────────────────────────────
FEATURE_COLS = [
    "pcr_local", "pcr_vs_neutral", "score_norm",
    "atm_iv_norm", "ivr_norm", "ivp_norm",
    "spot_vs_maxpain_pct", "spot_vs_resistance_pct", "spot_vs_support_pct",
    "time_min_norm", "is_bearish", "is_bullish",
]
N_FEATURES       = len(FEATURE_COLS)
MIN_TRAIN_ROWS   = 50       # minimum labeled rows required to train
MODEL_CACHE_DIR  = "."      # where to save .pkl model files


# ════════════════════════════════════════════════════════════════
#  Feature engineering
# ════════════════════════════════════════════════════════════════
def _to_float(val, default: float = 0.0) -> float:
    """Safely convert a CSV cell to float; return default on failure."""
    try:
        f = float(val)
        return default if (np.isnan(f) or np.isinf(f)) else f
    except (TypeError, ValueError):
        return default


def build_feature_row(
    pcr_local: float,
    score: int,
    atm_iv: float,
    ivr: float,
    ivp: float,
    spot: float,
    max_pain: float,
    resistance: float,
    support: float,
    bias: str,
) -> np.ndarray:
    """
    Convert one cycle's raw values to the 12-element feature vector.
    All features are normalised to roughly [0, 1] to help LR and MLP.
    """
    pcr   = _to_float(pcr_local, 1.0)
    sc    = _to_float(score,     50.0)
    iv    = _to_float(atm_iv,    15.0)
    ivr_v = _to_float(ivr,       50.0)
    ivp_v = _to_float(ivp,       50.0)
    sp    = _to_float(spot,       1.0)
    mp    = _to_float(max_pain,  sp)
    res   = _to_float(resistance, sp)
    sup   = _to_float(support,   sp)

    # Avoid division-by-zero
    sp = sp if sp != 0 else 1.0

    # Time of day: minutes since 09:15, normalised over 375-min session
    now = datetime.now()
    t_min = max(0, (now.hour * 60 + now.minute) - (9 * 60 + 15))
    t_norm = min(1.0, t_min / 375.0)

    return np.array([
        min(3.0, pcr),                          # pcr_local
        pcr - 1.0,                              # pcr_vs_neutral
        sc / 100.0,                             # score_norm
        min(1.0, iv / 35.0),                    # atm_iv_norm
        ivr_v / 100.0,                          # ivr_norm
        ivp_v / 100.0,                          # ivp_norm
        (sp - mp)  / sp * 100.0,               # spot_vs_maxpain_pct
        (res - sp) / sp * 100.0,               # spot_vs_resistance_pct
        (sp - sup) / sp * 100.0,               # spot_vs_support_pct
        t_norm,                                  # time_min_norm
        1.0 if bias == "BEARISH" else 0.0,      # is_bearish
        1.0 if bias == "BULLISH" else 0.0,      # is_bullish
    ], dtype=np.float32)


def _build_dataset_from_csv(symbol: str) -> tuple[np.ndarray, np.ndarray]:
    """
    Scan all SYMBOL_OI_*.csv files, compute features and next-cycle labels.

    Label: 1  if spot moved in Bias direction in the next row
           0  otherwise
    Rows with Bias == "NEUTRAL" are dropped (unlabeled by design).

    Returns (X: [N, 12], y: [N,]) or (empty, empty) if insufficient data.
    """
    pattern = f"{symbol}_OI_*.csv"
    files   = sorted(glob.glob(pattern))
    if not files:
        log.debug("[ML] No CSV files found for %s", symbol)
        return np.empty((0, N_FEATURES)), np.empty(0)

    frames = []
    for f in files:
        try:
            df = pd.read_csv(f)
            if len(df) >= 2:
                frames.append(df)
        except Exception as e:
            log.debug("[ML] Could not read %s: %s", f, e)

    if not frames:
        return np.empty((0, N_FEATURES)), np.empty(0)

    rows_X, rows_y = [], []
    for df in frames:
        df = df.reset_index(drop=True)
        for i in range(len(df) - 1):
            r    = df.iloc[i]
            r_nx = df.iloc[i + 1]
            bias = str(r.get("Bias", "NEUTRAL"))
            if bias == "NEUTRAL":
                continue

            spot_now  = _to_float(r.get("Spot"), 0)
            spot_next = _to_float(r_nx.get("Spot"), spot_now)
            moved     = spot_next - spot_now

            # Label: did the next cycle move in the bias direction?
            if bias == "BULLISH":
                label = 1 if moved > 0 else 0
            else:  # BEARISH
                label = 1 if moved < 0 else 0

            feat = build_feature_row(
                pcr_local  = _to_float(r.get("PCR_Local"),  1.0),
                score      = _to_float(r.get("Score"),      50.0),
                atm_iv     = _to_float(r.get("ATM_IV"),     15.0),
                ivr        = _to_float(r.get("IVR"),        50.0),
                ivp        = _to_float(r.get("IVP"),        50.0),
                spot       = spot_now,
                max_pain   = _to_float(r.get("MaxPain"),    spot_now),
                resistance = _to_float(r.get("Resistance"), spot_now),
                support    = _to_float(r.get("Support"),    spot_now),
                bias       = bias,
            )
            rows_X.append(feat)
            rows_y.append(label)

    if len(rows_X) < MIN_TRAIN_ROWS:
        return np.empty((0, N_FEATURES)), np.empty(0)

    return np.array(rows_X, dtype=np.float32), np.array(rows_y, dtype=np.int32)


def _synthetic_dataset(n: int = 300) -> tuple[np.ndarray, np.ndarray]:
    """
    Generate a synthetic balanced training set when real data is insufficient.
    Encodes basic market intuitions as noisy rules:
      - PCR < 0.8 + BULLISH  → label=1 more often
      - PCR > 1.2 + BEARISH  → label=1 more often
      - Score > 65            → label=1 more often
      - ATM IV high + trending → label=1 more often
    """
    rng = np.random.default_rng(42)
    X, y = [], []

    for _ in range(n):
        pcr    = rng.uniform(0.6, 1.6)
        score  = rng.uniform(15, 85)
        iv     = rng.uniform(8,  35)
        ivr    = rng.uniform(0, 100)
        ivp    = rng.uniform(0, 100)
        bias_r = rng.choice(["BULLISH", "BEARISH"])
        mp_dev = rng.uniform(-200, 200)
        res_d  = rng.uniform(50,  400)
        sup_d  = rng.uniform(50,  400)
        t_norm = rng.uniform(0,  1)
        spot   = 22500.0

        feat = np.array([
            pcr,
            pcr - 1.0,
            score / 100.0,
            min(1.0, iv / 35.0),
            ivr / 100.0,
            ivp / 100.0,
            mp_dev / spot * 100.0,
            res_d  / spot * 100.0,
            sup_d  / spot * 100.0,
            t_norm,
            1.0 if bias_r == "BEARISH" else 0.0,
            1.0 if bias_r == "BULLISH" else 0.0,
        ], dtype=np.float32)

        # Synthetic label probability
        p = 0.50
        if bias_r == "BULLISH" and pcr < 0.85:  p += 0.12
        if bias_r == "BEARISH" and pcr > 1.15:  p += 0.12
        if score > 65:                           p += 0.10
        if 18 <= iv <= 28:                       p += 0.06
        if ivr > 70:                             p += 0.06
        if 9 * 60 + 30 <= t_norm * 375 + 9 * 60 + 15 <= 11 * 60:
            p += 0.05  # morning session bonus
        p = min(0.85, p)
        label = int(rng.random() < p)
        X.append(feat)
        y.append(label)

    return np.array(X, dtype=np.float32), np.array(y, dtype=np.int32)


# ════════════════════════════════════════════════════════════════
#  Model factories
# ════════════════════════════════════════════════════════════════
def _make_rf() -> Pipeline:
    """sklearn RandomForest wrapped in a StandardScaler pipeline."""
    return Pipeline([
        ("scaler", StandardScaler()),
        ("clf",    RandomForestClassifier(
            n_estimators=120,
            max_depth=6,
            min_samples_leaf=4,
            class_weight="balanced",
            random_state=42,
            n_jobs=-1,
        )),
    ])


def _make_lr() -> Pipeline:
    """sklearn LogisticRegression — interpretable, fast linear baseline."""
    return Pipeline([
        ("scaler", StandardScaler()),
        ("clf",    LogisticRegression(
            C=1.0,
            max_iter=1000,
            class_weight="balanced",
            solver="lbfgs",
            random_state=42,
        )),
    ])


def _make_xgb():
    """
    XGBoost XGBClassifier — best overall on structured financial data.
    Falls back to sklearn GradientBoostingClassifier when xgboost is absent.
    """
    if _HAS_XGBOOST:
        return xgb.XGBClassifier(
            n_estimators=150,
            max_depth=4,
            learning_rate=0.05,
            subsample=0.8,
            colsample_bytree=0.8,
            use_label_encoder=False,
            eval_metric="logloss",
            random_state=42,
            verbosity=0,
        )
    else:
        from sklearn.ensemble import GradientBoostingClassifier
        return Pipeline([
            ("scaler", StandardScaler()),
            ("clf",    GradientBoostingClassifier(
                n_estimators=120,
                max_depth=4,
                learning_rate=0.05,
                subsample=0.8,
                random_state=42,
            )),
        ])


# ── MXNet / Gluon MLP ────────────────────────────────────────────
class _GluonMLP:
    """
    3-layer MLP built with MXNet Gluon.
    Architecture: 12 → 32 → 16 → 1 (sigmoid output = P(label=1))

    Gluon reference: https://mxnet.apache.org/api/python/docs/api/gluon/
    Training uses SGD with momentum and binary cross-entropy loss.
    """

    def __init__(self):
        self.net     = None
        self.trainer = None
        self._ctx    = mx.cpu()

    def _build_net(self):
        net = gluon_nn.Sequential()
        with net.name_scope():
            net.add(gluon_nn.Dense(32, activation="relu"))
            net.add(gluon_nn.Dropout(0.2))
            net.add(gluon_nn.Dense(16, activation="relu"))
            net.add(gluon_nn.Dense(1))
        net.initialize(mx.init.Xavier(), ctx=self._ctx)
        return net

    def fit(self, X: np.ndarray, y: np.ndarray):
        self.net = self._build_net()
        self.trainer = mx.gluon.Trainer(
            self.net.collect_params(), "sgd",
            {"learning_rate": 0.01, "momentum": 0.9, "wd": 1e-4},
        )
        loss_fn = mx.gluon.loss.SigmoidBinaryCrossEntropyLoss()

        X_nd = mxnd.array(X,             ctx=self._ctx)
        y_nd = mxnd.array(y.reshape(-1, 1).astype(np.float32), ctx=self._ctx)

        n_epochs = 60
        batch_sz = 32
        n = len(X)
        idx = np.arange(n)

        for _ in range(n_epochs):
            np.random.shuffle(idx)
            for start in range(0, n, batch_sz):
                b = idx[start: start + batch_sz]
                Xb = X_nd[b]
                yb = y_nd[b]
                with mx.autograd.record():
                    pred = self.net(Xb)
                    loss = loss_fn(pred, yb)
                loss.backward()
                self.trainer.step(batch_size=len(b))

    def predict_proba_pos(self, X: np.ndarray) -> np.ndarray:
        """Return P(label=1) for each sample."""
        if self.net is None:
            return np.full(len(X), 0.5, dtype=np.float32)
        X_nd  = mxnd.array(X, ctx=self._ctx)
        logit = self.net(X_nd).asnumpy().ravel()
        return 1.0 / (1.0 + np.exp(-logit))   # sigmoid


class _SklearnMLP:
    """sklearn MLPClassifier fallback when MXNet is not installed."""

    def __init__(self):
        from sklearn.neural_network import MLPClassifier
        self._pipe = Pipeline([
            ("scaler", StandardScaler()),
            ("clf",    MLPClassifier(
                hidden_layer_sizes=(32, 16),
                activation="relu",
                max_iter=300,
                early_stopping=True,
                random_state=42,
            )),
        ])
        self._fitted = False

    def fit(self, X, y):
        self._pipe.fit(X, y)
        self._fitted = True

    def predict_proba_pos(self, X: np.ndarray) -> np.ndarray:
        if not self._fitted:
            return np.full(len(X), 0.5, dtype=np.float32)
        return self._pipe.predict_proba(X)[:, 1].astype(np.float32)


def _make_mlp():
    return _GluonMLP() if _HAS_MXNET else _SklearnMLP()


# ════════════════════════════════════════════════════════════════
#  MLPredictor  — main class used by the dashboard
# ════════════════════════════════════════════════════════════════
class MLPredictor:
    """
    Trains and runs an ensemble of 4 models (RF, LR, XGB, MLP).

    Usage (integrated into SymbolEngine):

        predictor = MLPredictor("NIFTY")
        predictor.train()                    # call once at session start

        result = predictor.predict(
            pcr_local=1.12, score=63, atm_iv=22.5, ivr=85, ivp=72,
            spot=23150, max_pain=23300, resistance=23500, support=23000,
            bias="BEARISH",
        )
        # result = {
        #   "confidence": 0.73,   # ensemble P(correct direction)
        #   "signal":     "CONFIRM",  # CONFIRM | NEUTRAL | CONTRA
        #   "score_pts":  7,      # 0-10 contribution to signal scorer
        #   "models":     {"rf": 0.71, "lr": 0.68, "xgb": 0.76, "mlp": 0.77},
        #   "trained":    True,
        #   "data_rows":  384,
        #   "using_synthetic": False,
        # }
    """

    def __init__(self, symbol: str = "NIFTY"):
        self.symbol          = symbol
        self._rf:  Optional[Pipeline] = None
        self._lr:  Optional[Pipeline] = None
        self._xgb                     = None
        self._mlp                     = None
        self._trained        = False
        self._data_rows      = 0
        self._using_synthetic = False
        self._cache_path     = os.path.join(
            MODEL_CACHE_DIR, f"{symbol}_ml_models.pkl")

    # ── Training ─────────────────────────────────────────────────
    def train(self, force: bool = False) -> bool:
        """
        Build feature matrix from CSV history, train all 4 models.
        Returns True if training succeeded.
        If insufficient real data, trains on synthetic dataset.
        """
        if self._trained and not force:
            return True

        print(f"  [ML] Training ensemble for {self.symbol}…", end=" ", flush=True)

        X, y = _build_dataset_from_csv(self.symbol)
        self._using_synthetic = False

        if len(X) < MIN_TRAIN_ROWS:
            print(f"({len(X)} real rows < {MIN_TRAIN_ROWS} min) → synthetic", end=" ")
            X, y = _synthetic_dataset(400)
            self._using_synthetic = True

        self._data_rows = len(X)

        # ── 1. sklearn RandomForest ──────────────────────────────
        self._rf = _make_rf()
        self._rf.fit(X, y)

        # ── 2. sklearn LogisticRegression ────────────────────────
        self._lr = _make_lr()
        self._lr.fit(X, y)

        # ── 3. XGBoost / GradientBoosting ───────────────────────
        self._xgb = _make_xgb()
        self._xgb.fit(X, y)

        # ── 4. MXNet Gluon MLP / sklearn MLP ───────────────────
        self._mlp = _make_mlp()
        self._mlp.fit(X, y)

        self._trained = True
        src = "synthetic" if self._using_synthetic else f"{self._data_rows} real rows"
        lib_xgb = "xgboost" if _HAS_XGBOOST else "sklearn-GBT"
        lib_mlp = "mxnet-gluon" if _HAS_MXNET else "sklearn-MLP"
        print(f"done  [{src}]  libs: sklearn RF+LR, {lib_xgb}, {lib_mlp}")
        return True

    # ── Prediction ────────────────────────────────────────────────
    def predict(
        self,
        pcr_local: float,
        score: int,
        atm_iv: float,
        ivr: float,
        ivp: float,
        spot: float,
        max_pain: float,
        resistance: float,
        support: float,
        bias: str,
    ) -> dict:
        """
        Run ensemble prediction for the current cycle.
        Always returns a valid dict (confidence=0.5 when not trained).
        """
        _null = {
            "confidence": 0.5, "signal": "NEUTRAL", "score_pts": 0,
            "models": {}, "trained": False,
            "data_rows": 0, "using_synthetic": False,
        }

        if not self._trained or bias == "NEUTRAL":
            return _null

        X = build_feature_row(
            pcr_local=pcr_local, score=score, atm_iv=atm_iv,
            ivr=ivr, ivp=ivp, spot=spot, max_pain=max_pain,
            resistance=resistance, support=support, bias=bias,
        ).reshape(1, -1)

        # Gather per-model probabilities
        probs = {}
        try:
            probs["rf"]  = float(self._rf.predict_proba(X)[0, 1])
        except Exception:
            probs["rf"]  = 0.5
        try:
            probs["lr"]  = float(self._lr.predict_proba(X)[0, 1])
        except Exception:
            probs["lr"]  = 0.5
        try:
            if _HAS_XGBOOST:
                probs["xgb"] = float(self._xgb.predict_proba(X)[0, 1])
            else:
                probs["xgb"] = float(self._xgb.predict_proba(X)[0, 1])
        except Exception:
            probs["xgb"] = 0.5
        try:
            probs["mlp"] = float(self._mlp.predict_proba_pos(X)[0])
        except Exception:
            probs["mlp"] = 0.5

        # Soft-vote ensemble
        conf = float(np.mean(list(probs.values())))

        # Classify signal
        if conf >= 0.65:
            signal = "CONFIRM"     # ML agrees with rule-based bias
        elif conf <= 0.38:
            signal = "CONTRA"      # ML disagrees (red flag)
        else:
            signal = "NEUTRAL"     # Uncertain

        # Score contribution: up to +10, down to -5
        if signal == "CONFIRM":
            score_pts = int((conf - 0.50) / 0.50 * 10)  # 0-10 pts
            score_pts = min(10, max(0, score_pts))
        elif signal == "CONTRA":
            score_pts = -5
        else:
            score_pts = 0

        return {
            "confidence":      round(conf, 3),
            "signal":          signal,
            "score_pts":       score_pts,
            "models":          {k: round(v, 3) for k, v in probs.items()},
            "trained":         True,
            "data_rows":       self._data_rows,
            "using_synthetic": self._using_synthetic,
        }

    # ── Human-readable status line ────────────────────────────────
    def status_line(self) -> str:
        if not self._trained:
            return "[ML] Not yet trained"
        src = "SYNTHETIC" if self._using_synthetic else "REAL"
        lib_xgb = "xgb" if _HAS_XGBOOST else "gbt"
        lib_mlp = "gluon" if _HAS_MXNET else "mlp"
        return (f"[ML] RF·LR·{lib_xgb}·{lib_mlp} | "
                f"{self._data_rows} rows ({src}) | trained ✓")


# ════════════════════════════════════════════════════════════════
#  Module-level singleton (single-symbol mode)
# ════════════════════════════════════════════════════════════════
_default_predictor: Optional[MLPredictor] = None


def get_predictor(symbol: str = "NIFTY") -> MLPredictor:
    """Return the module-level singleton predictor, creating if necessary."""
    global _default_predictor
    if _default_predictor is None or _default_predictor.symbol != symbol:
        _default_predictor = MLPredictor(symbol)
    return _default_predictor
