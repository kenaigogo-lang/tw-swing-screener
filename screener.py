"""
每日執行：更新資料 → 篩選 → 記錄推薦 → 追蹤過去推薦的停利／停損結果 → 產生 docs/index.html

用法：python screener.py
"""
import datetime as dt
import json

import numpy as np
import pandas as pd

import report
from data import DATA_DIR, fundamentals, inst_panel, load_day, price_panel, recent_trading_days
from strategy import RULES, indicators, market_ok, pick_list, screen, simulate, stats

N_DAYS = 75          # 指標需要約 60 個交易日，多抓一些緩衝
PICKS_FILE = DATA_DIR / "picks.csv"
PICK_COLS = ["signal_date", "code", "name", "signal_close", "status", "entry_date", "entry",
             "tp", "sl", "exit_date", "exit_price", "last_close", "ret", "days"]


def latest_complete_day(days):
    """找出股價、本益比、法人資料都已公布的最新交易日。"""
    for d in reversed(days[-3:]):
        if load_day("pe", d) is not None and load_day("inst", d) is not None:
            return d
    raise RuntimeError("最近 3 個交易日的本益比或法人資料都抓不到，請稍後再試")


def add_business_days(d: dt.date, n: int) -> dt.date:
    """往後推 n 個平日（不含國定假日，所以只是估計值）。"""
    while n > 0:
        d += dt.timedelta(days=1)
        if d.weekday() < 5:
            n -= 1
    return d


def update_picks(picks: pd.DataFrame, today_picks: pd.DataFrame, as_of, panel) -> pd.DataFrame:
    # 1. 新增今日推薦（同一檔仍在追蹤中就不重複加入）
    active = set(picks.loc[picks["status"].isin(["待進場", "持有中"]), "code"])
    if not (picks["signal_date"] == as_of.isoformat()).any():
        new = [{"signal_date": as_of.isoformat(), "code": code, "name": r["name"],
                "signal_close": r["close"], "status": "待進場"}
               for code, r in today_picks.iterrows() if code not in active]
        if new:
            picks = pd.concat([picks, pd.DataFrame(new)], ignore_index=True)

    # 2. 更新尚未結束的交易
    for i, r in picks[picks["status"].isin(["待進場", "持有中"])].iterrows():
        res = simulate(str(r["code"]), pd.Timestamp(r["signal_date"]), panel)
        for k, v in res.items():
            picks.at[i, k] = v
    return picks[PICK_COLS]


def main():
    print("1/5 更新股價資料…")
    days = recent_trading_days(N_DAYS)
    as_of = latest_complete_day(days)
    days = [d for d in days if d <= as_of]
    print(f"    資料日期：{as_of}（共 {len(days)} 個交易日）")

    print("2/5 讀取法人、本益比與基本面…")
    panel = price_panel(days)
    inst = inst_panel(days[-10:])
    pe = load_day("pe", as_of)
    fund = fundamentals()

    print("3/5 篩選…")
    ind = indicators(panel, inst)
    ts = pd.Timestamp(as_of)
    mkt_ok, taiex, taiex_ma = market_ok(ind, ts)
    result = screen(ts, ind, pe, fund, panel["names"])
    picks_today = pick_list(result) if mkt_ok else result.iloc[0:0]
    watch = result[result["watch"] | (result["picked"] & (not mkt_ok))].head(10)
    funnel = {
        "全部股票": len(result),
        "① 基本面": int(result["s1_fundamental"].sum()),
        "② ＋已修正": int((result["s1_fundamental"] & result["s2_drawdown"]).sum()),
        "③ ＋反彈確認": int((result["s1_fundamental"] & result["s2_technical"]).sum()),
        "④ ＋法人買超": int((result["s1_fundamental"] & result["s2_technical"] & result["s3_chips"]).sum()),
        "⑤ ＋風險報酬": int(result["picked"].sum()),
    }

    print("4/5 追蹤過去推薦…")
    picks = pd.read_csv(PICKS_FILE, dtype={"code": str}) if PICKS_FILE.exists() \
        else pd.DataFrame(columns=PICK_COLS)
    picks = picks.astype({"status": object})
    picks = update_picks(picks, picks_today, as_of, panel)
    PICKS_FILE.parent.mkdir(parents=True, exist_ok=True)
    picks.to_csv(PICKS_FILE, index=False)

    print("5/5 產生報表…")
    bt_file = DATA_DIR / "backtest_summary.json"
    entry_day = add_business_days(as_of, 1)
    ctx = {
        "as_of": as_of,
        "generated": dt.datetime.now(dt.timezone(dt.timedelta(hours=8))),
        "market": {"ok": mkt_ok, "taiex": taiex, "ma": taiex_ma},
        "picks": picks_today,
        "watch": watch,
        "funnel": funnel,
        "entry_day": entry_day,
        "deadline": add_business_days(entry_day, RULES["hold_days"] - 1),
        "tracking": picks,
        "live_stats": stats(picks.assign(ret=pd.to_numeric(picks["ret"]), days=pd.to_numeric(picks["days"]))),
        "backtest": json.loads(bt_file.read_text(encoding="utf-8")) if bt_file.exists() else None,
        "rules": RULES,
    }
    out = report.render(ctx)
    print(f"    完成：{out}")
    print(f"    今日推薦 {len(picks_today)} 檔，觀察名單 {len(watch)} 檔")


if __name__ == "__main__":
    np.seterr(all="ignore")
    main()
