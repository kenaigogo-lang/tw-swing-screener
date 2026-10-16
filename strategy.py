"""
選股規則、技術指標與交易模擬。要調整條件，改 RULES 就好。
"""
import numpy as np
import pandas as pd

from data import TAIEX_CODE

RULES = {
    # 交易框架
    "take_profit": 0.10,     # 停利 +10%
    "stop_loss": 0.05,       # 停損 -5%（收盤價跌破才出場）
    "hold_days": 20,         # 最長持有 20 個交易日
    "cost": 0.001425 * 2 + 0.003,  # 買賣手續費（未折扣）＋證交稅，約 0.585%
    "max_picks": 5,
    # 步驟 0：大盤濾網
    "market_ma": 60,
    # 步驟 1：基本面
    "price_min": 25, "price_max": 100,
    "pe_max": 15, "pb_max": 1.5,
    "vol_min": 1000,         # 近 20 日平均成交量（張）
    # 步驟 2：技術面
    "drawdown_min": 0.10,    # 近 60 日高點到近 20 日低點至少跌 10%
    "rebound_max": 0.06,     # 收盤價距 20 日低點漲幅不超過 6%（避免追高）
    "kd_k_max": 40,          # KD 黃金交叉時 K 值需低於 40
    "kd_cross_days": 3,      # 黃金交叉發生在近 3 個交易日內
    "rsi_low": 35,           # RSI(14) 近 5 日曾低於 35 且回升
    # 步驟 3：籌碼面
    "trust_buy_days": 3,     # 投信近 5 日至少 3 日買超
    # 步驟 4：風險報酬
    "support_tolerance": 0.02,  # 20 日低點不可低於停損價 2% 以上
}
FINANCE_INDUSTRY_CODE = "17"


# ---------------------------------------------------------------------------
# 技術指標（對整張「日期 × 代號」寬表一次計算）
# ---------------------------------------------------------------------------

def _kd(close, high, low, n=9):
    lo = low.rolling(n, min_periods=n).min()
    hi = high.rolling(n, min_periods=n).max()
    rsv = ((close - lo) / (hi - lo).replace(0, np.nan) * 100).to_numpy()
    k = np.full(rsv.shape, np.nan)
    d = np.full(rsv.shape, np.nan)
    k_prev = np.full(rsv.shape[1], 50.0)
    d_prev = np.full(rsv.shape[1], 50.0)
    for i in range(rsv.shape[0]):
        r = rsv[i]
        valid = ~np.isnan(r)
        k_now = np.where(valid, k_prev * 2 / 3 + np.nan_to_num(r) / 3, k_prev)
        d_now = np.where(valid, d_prev * 2 / 3 + k_now / 3, d_prev)
        k[i] = np.where(valid, k_now, np.nan)
        d[i] = np.where(valid, d_now, np.nan)
        k_prev, d_prev = k_now, d_now
    return (pd.DataFrame(k, index=close.index, columns=close.columns),
            pd.DataFrame(d, index=close.index, columns=close.columns))


def _rsi(close, n=14):
    diff = close.diff()
    gain = diff.clip(lower=0).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    loss = (-diff.clip(upper=0)).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    return 100 - 100 / (1 + gain / loss.replace(0, np.nan))


def indicators(panel: dict, inst: dict, rules=RULES) -> dict:
    c, h, l, v = panel["close"], panel["high"], panel["low"], panel["vol"]
    ind = {
        "close": c,
        "ma5": c.rolling(5).mean(),
        "ma20": c.rolling(20).mean(),
        "ma60": c.rolling(rules["market_ma"]).mean(),
        "vol5_prev": v.shift(1).rolling(5).mean(),
        "vol": v,
        "vol20": v.rolling(20).mean(),
        "hi60": h.rolling(60, min_periods=55).max(),
        "lo20": l.rolling(20).min(),
        "rsi": _rsi(c),
    }
    k, d = _kd(c, h, l)
    ind["k"], ind["d"] = k, d
    cross = (k > d) & (k.shift(1) <= d.shift(1)) & (k < rules["kd_k_max"])
    ind["kd_cross"] = cross.astype(float).rolling(rules["kd_cross_days"]).max().eq(1) & (k > d)
    ind["rsi_turn"] = (ind["rsi"].rolling(5).min() < rules["rsi_low"]) & (ind["rsi"] > ind["rsi"].shift(1))

    # 法人資料對齊到股價的交易日
    f = inst["foreign"].reindex(index=c.index, columns=c.columns)
    t = inst["trust"].reindex(index=c.index, columns=c.columns)
    ind["trust_days5"] = (t > 0).astype(float).rolling(5).sum()
    ind["ft_sum5"] = (f + t).rolling(5).sum()
    ind["ft_last"] = f + t
    ind["trust_sum5"] = t.rolling(5).sum()
    ind["foreign_sum5"] = f.rolling(5).sum()
    return ind


# ---------------------------------------------------------------------------
# 篩選
# ---------------------------------------------------------------------------

def market_ok(ind: dict, date) -> tuple[bool, float, float]:
    c = ind["close"].at[date, TAIEX_CODE]
    ma = ind["ma60"].at[date, TAIEX_CODE]
    return bool(c >= ma), float(c), float(ma)


def screen(date, ind: dict, pe: pd.DataFrame, fund: pd.DataFrame,
           names: pd.DataFrame, rules=RULES, use_fund_filters: bool = True) -> pd.DataFrame:
    """
    對 date 這一天評估所有股票，回傳每檔的指標與各步驟是否通過。
    use_fund_filters=False 時（回測用，因為拿不到歷史當下的財報）略過 EPS／營收條件，
    只用「本益比 > 0」代表近四季 EPS 為正。
    """
    row = {k: v.loc[date] for k, v in ind.items()}
    df = pd.DataFrame(row)
    df = df[df.index != TAIEX_CODE]
    df = df.join(pe.set_index("code")[["pe", "pb"]], how="left")
    df = df.join(names, how="left").join(fund, how="left")

    r = rules
    df["tp_price"] = df["close"] * (1 + r["take_profit"])
    df["sl_price"] = df["close"] * (1 - r["stop_loss"])
    df["drawdown"] = 1 - df["lo20"] / df["hi60"]
    df["rebound"] = df["close"] / df["lo20"] - 1
    df["upside"] = df["hi60"] / df["close"] - 1

    not_fin = ((df["industry_code"].fillna("").astype(str) != FINANCE_INDUSTRY_CODE)
               & ~df["industry"].fillna("").astype(str).str.contains("金融"))

    s1 = (not_fin
          & df["close"].gt(r["price_min"]) & df["close"].lt(r["price_max"])
          & df["pe"].gt(0) & df["pe"].lt(r["pe_max"])
          & df["pb"].lt(r["pb_max"])
          & df["vol20"].gt(r["vol_min"]))
    if use_fund_filters:
        # 最新一期 EPS > 0，且營收未衰退（最新月或今年累計年增率 > 0）
        s1 &= df["eps"].gt(0) & (df["rev_yoy"].gt(0) | df["rev_cum_yoy"].gt(0))
    df["s1_fundamental"] = s1

    df["s2_drawdown"] = df["drawdown"].ge(r["drawdown_min"])
    df["s2_rebound"] = df["close"].gt(df["ma5"]) & df["vol"].gt(df["vol5_prev"])
    df["s2_not_high"] = df["rebound"].le(r["rebound_max"])
    df["s2_indicator"] = df["kd_cross"].fillna(False).astype(bool) | df["rsi_turn"].fillna(False).astype(bool)
    df["s2_technical"] = df["s2_drawdown"] & df["s2_rebound"] & df["s2_not_high"] & df["s2_indicator"]

    df["s3_chips"] = (df["trust_days5"].ge(r["trust_buy_days"])
                      | (df["ft_sum5"].gt(0) & df["ft_last"].gt(0)))

    df["s4_upside"] = df["tp_price"].le(df["hi60"])
    df["s4_support"] = df["lo20"].ge(df["sl_price"] * (1 - r["support_tolerance"]))
    df["s4_risk_reward"] = df["s4_upside"] & df["s4_support"]

    df["picked"] = df["s1_fundamental"] & df["s2_technical"] & df["s3_chips"] & df["s4_risk_reward"]
    # 觀察名單：基本面、籌碼、已修正都符合，但反彈尚未確認
    df["watch"] = (df["s1_fundamental"] & df["s2_drawdown"] & df["s3_chips"]
                   & df["s4_upside"] & ~df["picked"])
    # 排序：法人近 5 日合計買超佔 20 日均量的比例
    df["chip_strength"] = df["ft_sum5"] / df["vol20"]
    return df.sort_values("chip_strength", ascending=False)


def pick_list(df: pd.DataFrame, rules=RULES) -> pd.DataFrame:
    return df[df["picked"]].head(rules["max_picks"])


# ---------------------------------------------------------------------------
# 交易模擬：訊號日收盤後選出 → 隔日開盤價進場 → 停利／停損／時間到出場
# ---------------------------------------------------------------------------

def simulate(code: str, signal_date, panel: dict, rules=RULES) -> dict:
    dates = panel["close"].index
    after = dates[dates > signal_date]
    if len(after) == 0 or code not in panel["close"]:
        return {"status": "待進場"}
    o, h, c = panel["open"][code], panel["high"][code], panel["close"][code]
    entry_date = after[0]
    entry = o.at[entry_date]
    if np.isnan(entry):
        return {"status": "待進場"}
    tp = entry * (1 + rules["take_profit"])
    sl = entry * (1 - rules["stop_loss"])
    res = {"entry_date": entry_date.date().isoformat(), "entry": round(entry, 2),
           "tp": round(tp, 2), "sl": round(sl, 2)}
    held = after[: rules["hold_days"]]
    for i, d in enumerate(held, start=1):
        if np.isnan(c.at[d]):
            continue  # 停牌
        exit_price, status = None, None
        if o.at[d] >= tp:           # 跳空開高直接過停利價，以開盤價成交
            exit_price, status = o.at[d], "停利"
        elif h.at[d] >= tp:
            exit_price, status = tp, "停利"
        elif c.at[d] <= sl:
            exit_price, status = c.at[d], "停損"
        elif i == rules["hold_days"]:
            exit_price, status = c.at[d], "時間到"
        if status:
            ret = exit_price / entry - 1 - rules["cost"]
            return {**res, "status": status, "exit_date": d.date().isoformat(),
                    "exit_price": round(exit_price, 2), "ret": round(ret * 100, 2), "days": i}
    last = held[-1]
    return {**res, "status": "持有中", "last_close": round(c.at[last], 2),
            "ret": round((c.at[last] / entry - 1 - rules["cost"]) * 100, 2), "days": len(held)}


def stats(trades: pd.DataFrame) -> dict:
    """已出場交易的勝率與期望值（報酬已扣成本）。"""
    closed = trades[trades["status"].isin(["停利", "停損", "時間到"])]
    if closed.empty:
        return {"n": 0}
    wins = closed[closed["ret"] > 0]
    losses = closed[closed["ret"] <= 0]
    return {
        "n": len(closed),
        "win_rate": round(len(wins) / len(closed) * 100, 1),
        "tp_rate": round((closed["status"] == "停利").mean() * 100, 1),
        "sl_rate": round((closed["status"] == "停損").mean() * 100, 1),
        "time_rate": round((closed["status"] == "時間到").mean() * 100, 1),
        "avg_win": round(wins["ret"].mean(), 2) if len(wins) else 0.0,
        "avg_loss": round(losses["ret"].mean(), 2) if len(losses) else 0.0,
        "expectancy": round(closed["ret"].mean(), 2),
        "avg_days": round(closed["days"].mean(), 1),
    }
