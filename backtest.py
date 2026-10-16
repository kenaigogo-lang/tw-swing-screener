"""
歷史回測：用過去 N 年的每日資料套用同一套規則，計算「隔日開盤進場、+10% 停利、-5% 停損、20 日出場」的勝率。

限制：拿不到「歷史當下」的財報與月營收，所以回測只用本益比、淨值比、技術面與法人條件。
第一次執行要抓大量歷史資料（2 年約 1.5 小時），之後有快取就很快。

用法：python backtest.py --years 2
"""
import argparse
import json
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd

from data import DATA_DIR, fundamentals, inst_panel, load_day, price_panel, recent_trading_days
from strategy import RULES, indicators, market_ok, pick_list, screen, simulate, stats


def main(years: float):
    n = int(years * 245) + 70
    print(f"1/4 抓取 {n} 個交易日股價（已快取的會跳過）…")
    days = recent_trading_days(n)

    print("2/4 抓取本益比與法人資料…")
    def prefetch(kind):
        for i, d in enumerate(days):
            load_day(kind, d)
            if i % 50 == 0:
                print(f"    {kind} {i}/{len(days)}")
    with ThreadPoolExecutor(2) as ex:
        list(ex.map(prefetch, ["pe", "inst"]))

    print("3/4 計算指標…")
    panel = price_panel(days)
    inst = inst_panel(days, fetch=False)
    ind = indicators(panel, inst)
    fund = fundamentals()[["industry_code", "industry"]]

    print("4/4 逐日模擬…")
    trades, busy_until = [], {}
    for d in days[60:-1]:
        ts = pd.Timestamp(d)
        ok, *_ = market_ok(ind, ts)
        pe = load_day("pe", d, fetch=False)
        if not ok or pe is None or pe.empty:
            continue
        res = screen(ts, ind, pe, fund, panel["names"], use_fund_filters=False)
        for code, r in pick_list(res).iterrows():
            if busy_until.get(code, "") >= d.isoformat():
                continue  # 同一檔還在持有中，不重複進場
            t = simulate(code, ts, panel)
            if t["status"] == "待進場":
                continue
            busy_until[code] = t.get("exit_date", "9999")
            trades.append({"signal_date": d.isoformat(), "code": code, "name": r["name"], **t})

    df = pd.DataFrame(trades)
    df.to_csv(DATA_DIR / "backtest_trades.csv", index=False)
    s = stats(df) if not df.empty else {"n": 0}
    summary = {"start": days[60].isoformat(), "end": days[-1].isoformat(),
               "signals": len(df), "stats": s, "rules": RULES}
    if not df.empty:
        df["year"] = df["signal_date"].str[:4]
        summary["by_year"] = {y: stats(g) for y, g in df.groupby("year")}
    (DATA_DIR / "backtest_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary["stats"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    np.seterr(all="ignore")
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", type=float, default=2)
    main(ap.parse_args().years)
