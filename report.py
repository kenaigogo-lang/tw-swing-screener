"""
產生手機也好讀的 HTML 報表：docs/index.html（GitHub Pages 直接發布這個資料夾）
"""
import math
from html import escape
from pathlib import Path

import pandas as pd

OUT = Path(__file__).parent / "docs" / "index.html"


def _f(v, fmt="{:.2f}", na="—"):
    try:
        if v is None or (isinstance(v, float) and math.isnan(v)):
            return na
        return fmt.format(v)
    except (TypeError, ValueError):
        return na


def _reasons(r) -> list[str]:
    out = [f"本益比 {_f(r.pe, '{:.1f}')} 倍、股價淨值比 {_f(r.pb)} 倍，評價偏低"]
    if not pd.isna(r.eps):
        ym = str(r.rev_month)
        ym = f"{ym[:-2]}/{ym[-2:]}" if ym.isdigit() and len(ym) >= 5 else ym
        rev = f"，{escape(ym)} 月營收年增 {_f(r.rev_yoy, '{:+.1f}')}%、今年累計 {_f(r.rev_cum_yoy, '{:+.1f}')}%" \
            if not pd.isna(r.rev_yoy) else ""
        out.append(f"最新財報 EPS {_f(r.eps)} 元（{escape(str(r.eps_period))}）{rev}")
    out.append(f"近 60 日高點 {_f(r.hi60)} 回檔 {_f(r.drawdown * 100, '{:.0f}')}%，"
               f"目前距 20 日低點 {_f(r.lo20)} 僅 +{_f(r.rebound * 100, '{:.1f}')}%")
    sig = ["收盤站上 5 日均線且量增"]
    if r.kd_cross:
        sig.append(f"KD 低檔黃金交叉（K {_f(r.k, '{:.0f}')}／D {_f(r.d, '{:.0f}')}）")
    if r.rsi_turn:
        sig.append(f"RSI 由低檔回升至 {_f(r.rsi, '{:.0f}')}")
    out.append("、".join(sig))
    out.append(f"投信近 5 日 {_f(r.trust_days5, '{:.0f}')} 日買超（合計 {_f(r.trust_sum5, '{:+,.0f}')} 張），"
               f"外資近 5 日 {_f(r.foreign_sum5, '{:+,.0f}')} 張")
    return out


def _risks(r) -> list[str]:
    out = []
    if not pd.isna(r.rev_yoy) and r.rev_yoy < 0:
        out.append("最新月營收年減，留意獲利動能")
    if r.upside < 0.15:
        out.append(f"距 60 日高點僅 {_f(r.upside * 100, '{:.0f}')}%，停利價附近有前高壓力")
    if not pd.isna(r.ma60) and r.close < r.ma60:
        out.append("股價仍在季線之下，中期趨勢偏弱")
    if r.pe < 8:
        out.append("本益比特別低，留意是否為景氣循環高峰或一次性業外收益")
    out.append("整體市場的系統性風險；請自行確認近期是否有除權息")
    return out


def _pick_card(code, r, ctx) -> str:
    li = lambda xs: "".join(f"<li>{x}</li>" for x in xs)
    return f"""
<article class="card">
  <header class="card-h">
    <div><span class="name">{escape(str(r['name']))}</span> <span class="code">{code}</span>
      <span class="tag">{escape(str(r['market']))}</span></div>
    <div class="price">{_f(r.close)}<small>{ctx['as_of']:%m/%d} 收盤</small></div>
  </header>
  <div class="kv">
    <div><b>{_f(r.pe, '{:.1f}')}</b><span>本益比</span></div>
    <div><b>{_f(r.pb)}</b><span>淨值比</span></div>
    <div><b>{_f(r.eps)}</b><span>最新 EPS</span></div>
    <div><b>{_f(r.vol20, '{:,.0f}')}</b><span>20日均量(張)</span></div>
  </div>
  <div class="plan">
    <div><span>進場</span><b>{ctx['entry_day']:%m/%d} 開盤</b></div>
    <div class="up"><span>停利 +10%</span><b>{_f(r.tp_price)}</b></div>
    <div class="down"><span>停損 -5%</span><b>{_f(r.sl_price)}</b></div>
    <div><span>時間停損</span><b>約 {ctx['deadline']:%m/%d}</b></div>
  </div>
  <p class="note">停利／停損價以 {ctx['as_of']:%m/%d} 收盤價試算，實際以進場價 ×1.10／×0.95 為準。</p>
  <h4>推薦理由</h4><ul>{li(_reasons(r))}</ul>
  <h4>主要風險</h4><ul class="risk">{li(_risks(r))}</ul>
</article>"""


def _stat_tiles(s: dict, label: str) -> str:
    if not s or not s.get("n"):
        return f'<p class="muted">{label}：還沒有已出場的交易。</p>'
    good = s["win_rate"] >= 36
    warn = '<p class="muted">樣本少於 30 筆，數字參考性有限。</p>' if s["n"] < 30 else ""
    return f"""
<div class="tiles">
  <div><b>{s['n']}</b><span>已出場筆數</span></div>
  <div class="{'up' if good else 'down'}"><b>{s['win_rate']}%</b><span>勝率（兩平約 36%）</span></div>
  <div class="{'up' if s['expectancy'] > 0 else 'down'}"><b>{s['expectancy']:+.2f}%</b><span>每筆期望值</span></div>
  <div><b>{s['avg_days']}</b><span>平均持有天數</span></div>
</div>
<p class="muted">出場分布：停利 {s['tp_rate']}%・停損 {s['sl_rate']}%・時間到 {s['time_rate']}%；
平均獲利 {s['avg_win']:+.2f}%、平均虧損 {s['avg_loss']:+.2f}%（皆已扣手續費與證交稅）</p>{warn}"""


def _tracking_table(t: pd.DataFrame) -> str:
    if t.empty:
        return '<p class="muted">還沒有推薦紀錄，之後每次推薦都會自動追蹤到出場。</p>'
    order = {"持有中": 0, "待進場": 1}
    t = t.assign(_o=t["status"].map(order).fillna(2)).sort_values(["_o", "signal_date"], ascending=[True, False]).head(40)
    rows = []
    for _, r in t.iterrows():
        ret = pd.to_numeric(r["ret"], errors="coerce")
        cls = "" if pd.isna(ret) else ("up" if ret > 0 else "down")
        rows.append(f"<tr><td>{escape(str(r['signal_date']))[5:]}</td>"
                    f"<td>{escape(str(r['name']))}<br><small>{r['code']}</small></td>"
                    f"<td>{escape(str(r['status']))}</td><td>{_f(pd.to_numeric(r['entry'], errors='coerce'))}</td>"
                    f"<td class='{cls}'>{_f(ret, '{:+.1f}%')}</td><td>{_f(pd.to_numeric(r['days'], errors='coerce'), '{:.0f}')}</td></tr>")
    return ("<div class='tbl'><table><thead><tr><th>訊號日</th><th>股票</th><th>狀態</th>"
            "<th>進場價</th><th>報酬</th><th>天數</th></tr></thead><tbody>"
            + "".join(rows) + "</tbody></table></div>")


def _watch_table(w: pd.DataFrame) -> str:
    if w.empty:
        return '<p class="muted">目前沒有接近條件的股票。</p>'
    rows = []
    for code, r in w.iterrows():
        miss = []
        if not r.s2_rebound: miss.append("未站上5日線或量不足")
        if not r.s2_not_high: miss.append("離低點已遠")
        if not r.s2_indicator: miss.append("KD/RSI 未轉強")
        if not r.s4_support: miss.append("下檔支撐太遠")
        if r.picked: miss.append("大盤濾網未過")
        rows.append(f"<tr><td>{escape(str(r['name']))}<br><small>{code}</small></td><td>{_f(r.close)}</td>"
                    f"<td>{_f(r.pe, '{:.1f}')}／{_f(r.pb)}</td><td>{'、'.join(miss) or '—'}</td></tr>")
    return ("<div class='tbl'><table><thead><tr><th>股票</th><th>收盤</th><th>PE／PB</th><th>還差什麼</th>"
            "</tr></thead><tbody>" + "".join(rows) + "</tbody></table></div>")


def render(ctx: dict) -> Path:
    m = ctx["market"]
    picks = ctx["picks"]
    if not m["ok"]:
        pick_html = '<p class="banner down">大盤位於季線下，今日不建議新進場。符合其他條件的股票列在觀察名單。</p>'
    elif picks.empty:
        pick_html = '<p class="banner">今天沒有股票通過全部條件。寧缺勿濫，可以看看下方觀察名單。</p>'
    else:
        pick_html = "".join(_pick_card(code, r, ctx) for code, r in picks.iterrows())

    fmax = max(ctx["funnel"].values()) or 1
    funnel = "".join(
        f'<div class="fn"><span>{k}</span><i style="width:{max(v / fmax * 100, 1.5):.1f}%"></i><b>{v}</b></div>'
        for k, v in ctx["funnel"].items())

    bt = ctx["backtest"]
    bt_html = (f'<p class="muted">回測期間 {bt["start"]} ～ {bt["end"]}，共 {bt["signals"]} 個訊號。'
               f'回測拿不到歷史當下的財報與月營收，所以只用本益比、淨值比、技術面與法人條件。</p>'
               + _stat_tiles(bt["stats"], "回測")) if bt else \
        '<p class="muted">還沒跑過回測。到 GitHub 的 Actions 頁面手動執行「回測」即可。</p>'

    r = ctx["rules"]
    html = f"""<!doctype html>
<html lang="zh-Hant"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>台股波段選股</title>
<style>
:root{{--bg:#f6f7f9;--card:#fff;--text:#1d2129;--muted:#6b7280;--line:#e5e7eb;--accent:#2563eb;
--up:#d92d20;--down:#16803c;--chip:#eef2ff}}
@media (prefers-color-scheme:dark){{:root{{--bg:#0f1115;--card:#181b21;--text:#e6e8eb;--muted:#9aa1ab;
--line:#2a2f37;--accent:#7aa2ff;--up:#ff6b5e;--down:#4cc38a;--chip:#232a3b}}}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--text);line-height:1.6;
font-family:"Microsoft JhengHei","Noto Sans TC","PingFang TC","Segoe UI",Tahoma,sans-serif}}
main{{max-width:760px;margin:0 auto;padding:16px}}
h1{{font-size:1.4rem;margin:.2em 0}} h2{{font-size:1.1rem;margin:1.8em 0 .6em}}
h4{{margin:.9em 0 .2em;font-size:.95rem}}
.muted,.note{{color:var(--muted);font-size:.85rem}}
.up{{color:var(--up)}} .down{{color:var(--down)}}
.banner{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px 14px}}
.mkt{{display:flex;gap:10px;flex-wrap:wrap;align-items:center;background:var(--card);border:1px solid var(--line);
border-radius:12px;padding:12px 14px}}
.pill{{padding:2px 10px;border-radius:99px;font-weight:700;background:var(--chip)}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:14px;margin:12px 0}}
.card-h{{display:flex;justify-content:space-between;align-items:flex-start;gap:8px}}
.name{{font-size:1.2rem;font-weight:700}} .code{{color:var(--muted)}}
.tag{{font-size:.75rem;background:var(--chip);border-radius:6px;padding:1px 6px;margin-left:4px}}
.price{{font-size:1.4rem;font-weight:700;text-align:right}}
.price small{{display:block;font-size:.75rem;color:var(--muted);font-weight:400}}
.kv,.plan,.tiles{{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin-top:10px}}
.kv div,.plan div,.tiles div{{background:var(--bg);border-radius:10px;padding:8px;text-align:center}}
.kv b,.plan b,.tiles b{{display:block;font-size:1.05rem}}
.kv span,.plan span,.tiles span{{font-size:.72rem;color:var(--muted)}}
ul{{margin:.2em 0;padding-left:1.2em}} li{{margin:.15em 0}} ul.risk li{{color:var(--muted)}}
.fn{{display:grid;grid-template-columns:7.5em 1fr 3em;align-items:center;gap:8px;font-size:.85rem;margin:4px 0}}
.fn i{{display:block;height:10px;border-radius:5px;background:var(--accent)}} .fn b{{text-align:right}}
.tbl{{overflow-x:auto;background:var(--card);border:1px solid var(--line);border-radius:12px}}
table{{border-collapse:collapse;width:100%;font-size:.88rem}}
th,td{{padding:8px 10px;border-bottom:1px solid var(--line);text-align:left;white-space:nowrap}}
td small{{color:var(--muted)}}
details{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:10px 14px}}
summary{{cursor:pointer;font-weight:700}}
footer{{margin:2em 0 1em;color:var(--muted);font-size:.8rem}}
@media (max-width:520px){{.kv,.plan,.tiles{{grid-template-columns:repeat(2,1fr)}}}}
</style></head><body><main>
<h1>台股波段選股</h1>
<p class="muted">資料日期 {ctx['as_of']:%Y/%m/%d}（收盤）・產生時間 {ctx['generated']:%m/%d %H:%M}・上市＋上櫃</p>

<div class="mkt"><span>大盤濾網</span>
  <span class="pill {'up' if m['ok'] else 'down'}">{'通過' if m['ok'] else '未通過'}</span>
  <span>加權指數 {m['taiex']:,.0f}／季線 {m['ma']:,.0f}</span></div>

<h2>今日推薦（{len(picks)} 檔）</h2>
{pick_html}

<h2>篩選漏斗</h2>
<div class="banner">{funnel}</div>

<h2>觀察名單</h2>
<p class="muted">基本面、法人、已修正都符合，但反彈還沒確認的股票。</p>
{_watch_table(ctx['watch'])}

<h2>實際追蹤績效</h2>
<p class="muted">每檔推薦都以「隔日開盤價」進場，自動追蹤到停利、停損或滿 {r['hold_days']} 個交易日。</p>
{_stat_tiles(ctx['live_stats'], '實際追蹤')}
{_tracking_table(ctx['tracking'])}

<h2>歷史回測</h2>
{bt_html}

<h2>篩選規則</h2>
<details><summary>展開完整規則</summary>
<ol>
<li><b>大盤濾網</b>：加權指數收盤 ≥ {r['market_ma']} 日均線</li>
<li><b>基本面</b>：排除金融保險業；{r['price_min']} 元 &lt; 股價 &lt; {r['price_max']} 元；0 &lt; 本益比 &lt; {r['pe_max']}；
股價淨值比 &lt; {r['pb_max']}；最新一期 EPS &gt; 0；最新月營收或今年累計營收年增率 &gt; 0；20 日均量 &gt; {r['vol_min']:,} 張</li>
<li><b>技術面</b>：近 60 日高點到 20 日低點跌幅 ≥ {r['drawdown_min']:.0%}；收盤站上 5 日均線且量大於前 5 日均量；
距 20 日低點漲幅 ≤ {r['rebound_max']:.0%}；KD 在 K &lt; {r['kd_k_max']} 黃金交叉（近 {r['kd_cross_days']} 日內）或 RSI(14) 近 5 日曾 &lt; {r['rsi_low']} 且回升</li>
<li><b>籌碼面</b>：投信近 5 日至少 {r['trust_buy_days']} 日買超，或外資＋投信近 5 日合計買超且最新一日買超</li>
<li><b>風險報酬</b>：停利價 ≤ 60 日高點；20 日低點不低於停損價 {r['support_tolerance']:.0%} 以上</li>
<li><b>出場</b>：停利 +{r['take_profit']:.0%}（觸價即出）；收盤跌破 -{r['stop_loss']:.0%} 停損；滿 {r['hold_days']} 個交易日出場。
報酬扣除手續費與證交稅約 {r['cost']:.3%}</li>
</ol>
<p class="muted">排序依「法人近 5 日合計買超 ÷ 20 日均量」，最多列 {r['max_picks']} 檔。資料來源：臺灣證券交易所、證券櫃檯買賣中心。</p>
</details>

<footer>本頁由程式依固定規則自動產生，僅供研究參考，不構成投資建議。下單前請自行確認最新資訊。</footer>
</main></body></html>"""
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(html, encoding="utf-8")
    return OUT
