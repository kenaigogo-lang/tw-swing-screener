"""
資料層：從臺灣證券交易所（上市）與櫃買中心（上櫃）抓取每日資料，並快取在 data/ 資料夾。

快取規則：
- 每種資料、每個日期存成一個 data/<kind>/<YYYYMMDD>.csv.gz
- 檔案存在但沒有資料列 = 該日非交易日（只對「今天以前」的日期做這種標記）
- 已快取的日期不會重抓，所以每天只需要抓最新一天
"""
import datetime as dt
import json
import threading
import time
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import requests

DATA_DIR = Path(__file__).parent / "data"
TZ = ZoneInfo("Asia/Taipei")
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
}
# 每個主機兩次請求之間的最小間隔（秒），證交所請求太密集會被暫時封鎖
MIN_GAP = {"twse": 3.0, "tpex": 1.5}
_last_call: dict[str, float] = {}
_locks = {k: threading.Lock() for k in MIN_GAP}
_session = requests.Session()  # 重複使用連線，減少握手時間
_session.headers.update(HEADERS)

TAIEX_CODE = "^TAIEX"
KINDS = ("price", "pe", "inst")


def today_tw() -> dt.date:
    return dt.datetime.now(TZ).date()


def _get_json(url: str, host: str, retries: int = 3):
    """
    依主機限速抓 JSON，失敗重試。
    重試後仍失敗就丟出例外中止程式，避免把「被封鎖」誤記成「非交易日」寫進快取。
    """
    for attempt in range(retries):
        with _locks[host]:
            wait = MIN_GAP[host] - (time.time() - _last_call.get(host, 0))
            if wait > 0:
                time.sleep(wait)
            _last_call[host] = time.time()
        try:
            # 串流讀取並設定總時限，避免連線卡住時程式無限等待
            with _session.get(url, timeout=(10, 30), stream=True) as r:
                r.raise_for_status()
                buf, start = bytearray(), time.time()
                for chunk in r.iter_content(65536):
                    buf += chunk
                    if time.time() - start > 90:
                        raise TimeoutError("下載超過 90 秒")
            return json.loads(buf.decode("utf-8"))
        except Exception as e:  # 網路錯誤、被擋、回傳非 JSON
            print(f"  [重試 {attempt + 1}/{retries}] {url} -> {e}")
            time.sleep(10 * (attempt + 1))
    raise RuntimeError(f"無法取得資料：{url}")


def to_num(s) -> float:
    """把 '1,234.5'、'--'、'' 之類的字串轉成數字，無法轉換時回傳 NaN。"""
    if s is None:
        return float("nan")
    if isinstance(s, (int, float)):
        return float(s)
    s = str(s).strip().replace(",", "")
    if s in ("", "-", "--", "---", "----", "N/A"):
        return float("nan")
    try:
        return float(s)
    except ValueError:
        return float("nan")


def is_stock_code(code: str) -> bool:
    """只保留 4 碼純數字的普通股（排除 ETF、權證、特別股等）。"""
    return len(code) == 4 and code.isdigit() and not code.startswith("0")


# ---------------------------------------------------------------------------
# 各資料來源的解析器：回傳 DataFrame，無資料回傳 None（代表非交易日或尚未公布）
# ---------------------------------------------------------------------------

def _twse_price(d: dt.date):
    j = _get_json(f"https://www.twse.com.tw/exchangeReport/MI_INDEX?response=json"
                  f"&date={d:%Y%m%d}&type=ALLBUT0999", "twse")
    if not j or "tables" not in j:
        return None
    rows, taiex = [], None
    for t in j["tables"]:
        fields = t.get("fields") or []
        data = t.get("data") or []
        if "收盤指數" in fields:
            for r in data:
                if r[0] == "發行量加權股價指數":
                    taiex = to_num(r[1])
        if "證券代號" in fields and "收盤價" in fields:
            ix = {f: i for i, f in enumerate(fields)}
            for r in data:
                code = r[ix["證券代號"]].strip()
                if not is_stock_code(code):
                    continue
                rows.append({
                    "code": code, "name": r[ix["證券名稱"]].strip(), "market": "上市",
                    "open": to_num(r[ix["開盤價"]]), "high": to_num(r[ix["最高價"]]),
                    "low": to_num(r[ix["最低價"]]), "close": to_num(r[ix["收盤價"]]),
                    "vol": to_num(r[ix["成交股數"]]) / 1000,  # 換算成「張」
                })
    if not rows:
        return None
    if taiex is not None:
        rows.append({"code": TAIEX_CODE, "name": "加權指數", "market": "指數",
                     "open": taiex, "high": taiex, "low": taiex, "close": taiex, "vol": 0})
    return pd.DataFrame(rows)


def _tpex_price(d: dt.date):
    j = _get_json(f"https://www.tpex.org.tw/www/zh-tw/afterTrading/dailyQuotes?"
                  f"date={d:%Y/%m/%d}&response=json", "tpex")
    if not j or not j.get("tables"):
        return None
    t = j["tables"][0]
    if not t.get("data"):
        return None
    ix = {f: i for i, f in enumerate(t["fields"])}
    rows = []
    for r in t["data"]:
        code = r[ix["代號"]].strip()
        if not is_stock_code(code):
            continue
        rows.append({
            "code": code, "name": r[ix["名稱"]].strip(), "market": "上櫃",
            "open": to_num(r[ix["開盤"]]), "high": to_num(r[ix["最高"]]),
            "low": to_num(r[ix["最低"]]), "close": to_num(r[ix["收盤"]]),
            "vol": to_num(r[ix["成交股數"]]) / 1000,
        })
    return pd.DataFrame(rows) if rows else None


def _twse_pe(d: dt.date):
    j = _get_json(f"https://www.twse.com.tw/exchangeReport/BWIBBU_d?response=json"
                  f"&date={d:%Y%m%d}&selectType=ALL", "twse")
    if not j or j.get("stat") != "OK" or not j.get("data"):
        return None
    ix = {f: i for i, f in enumerate(j["fields"])}
    rows = [{"code": r[ix["證券代號"]].strip(), "pe": to_num(r[ix["本益比"]]),
             "pb": to_num(r[ix["股價淨值比"]])}
            for r in j["data"] if is_stock_code(r[ix["證券代號"]].strip())]
    return pd.DataFrame(rows) if rows else None


def _tpex_pe(d: dt.date):
    j = _get_json(f"https://www.tpex.org.tw/www/zh-tw/afterTrading/peQryDate?"
                  f"date={d:%Y/%m/%d}&response=json", "tpex")
    if not j or not j.get("tables") or not j["tables"][0].get("data"):
        return None
    t = j["tables"][0]
    ix = {f: i for i, f in enumerate(t["fields"])}
    rows = [{"code": r[ix["股票代號"]].strip(), "pe": to_num(r[ix["本益比"]]),
             "pb": to_num(r[ix["股價淨值比"]])}
            for r in t["data"] if is_stock_code(r[ix["股票代號"]].strip())]
    return pd.DataFrame(rows) if rows else None


def _twse_inst(d: dt.date):
    j = _get_json(f"https://www.twse.com.tw/rwd/zh/fund/T86?date={d:%Y%m%d}"
                  f"&selectType=ALL&response=json", "twse")
    if not j or j.get("stat") != "OK" or not j.get("data"):
        return None
    ix = {f: i for i, f in enumerate(j["fields"])}
    rows = []
    for r in j["data"]:
        code = r[ix["證券代號"]].strip()
        if not is_stock_code(code):
            continue
        foreign = (to_num(r[ix["外陸資買賣超股數(不含外資自營商)"]])
                   + to_num(r[ix["外資自營商買賣超股數"]]))
        rows.append({"code": code, "foreign": foreign / 1000,
                     "trust": to_num(r[ix["投信買賣超股數"]]) / 1000})
    return pd.DataFrame(rows) if rows else None


def _tpex_inst(d: dt.date):
    j = _get_json(f"https://www.tpex.org.tw/www/zh-tw/insti/dailyTrade?type=Daily&sect=EW"
                  f"&date={d:%Y/%m/%d}&response=json", "tpex")
    if not j or not j.get("tables") or not j["tables"][0].get("data"):
        return None
    rows = []
    # 欄位順序：代號、名稱、外資(不含自營)x3、外資自營x3、外資合計x3、投信x3、自營商…
    for r in j["tables"][0]["data"]:
        code = r[0].strip()
        if not is_stock_code(code):
            continue
        rows.append({"code": code, "foreign": to_num(r[10]) / 1000,
                     "trust": to_num(r[13]) / 1000})
    return pd.DataFrame(rows) if rows else None


FETCHERS = {
    "price": (_twse_price, _tpex_price),
    "pe": (_twse_pe, _tpex_pe),
    "inst": (_twse_inst, _tpex_inst),
}
COLUMNS = {
    "price": ["code", "name", "market", "open", "high", "low", "close", "vol"],
    "pe": ["code", "pe", "pb"],
    "inst": ["code", "foreign", "trust"],
}


def _path(kind: str, d: dt.date) -> Path:
    return DATA_DIR / kind / f"{d:%Y%m%d}.csv.gz"


def load_day(kind: str, d: dt.date, fetch: bool = True):
    """
    讀取某日某種資料。
    回傳 DataFrame（有資料）、空 DataFrame（非交易日）、None（尚未公布或抓取失敗）。
    """
    p = _path(kind, d)
    if p.exists():
        return pd.read_csv(p, dtype={"code": str})
    if not fetch or d > today_tw() or d.weekday() >= 5:
        return None
    twse_fn, tpex_fn = FETCHERS[kind]
    t0 = time.time()
    twse_df = twse_fn(d)
    if twse_df is None:
        # 上市沒資料：過去日期視為非交易日並記錄；今天可能只是還沒公布
        if d < today_tw() and kind == "price":
            p.parent.mkdir(parents=True, exist_ok=True)
            pd.DataFrame(columns=COLUMNS[kind]).to_csv(p, index=False)
            return pd.DataFrame(columns=COLUMNS[kind])
        return None
    tpex_df = tpex_fn(d)
    if tpex_df is None:
        print(f"  [警告] {d} 櫃買中心 {kind} 抓不到，這次先不快取")
        return twse_df
    df = pd.concat([twse_df, tpex_df], ignore_index=True)[COLUMNS[kind]]
    p.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(p, index=False, float_format="%.4g")
    print(f"    {kind} {d} 已下載（{time.time() - t0:.1f} 秒）")
    return df


def recent_trading_days(n: int, end: dt.date | None = None) -> list[dt.date]:
    """從 end 往回找 n 個有股價資料的交易日（舊到新）。"""
    end = end or today_tw()
    days, d = [], end
    limit = end - dt.timedelta(days=int(n * 1.6) + 30)
    while len(days) < n and d >= limit:
        if d.weekday() < 5:
            df = load_day("price", d)
            if df is not None and len(df):
                days.append(d)
        d -= dt.timedelta(days=1)
    return sorted(days)


def price_panel(days: list[dt.date]) -> dict[str, pd.DataFrame]:
    """把多日股價組成「日期 × 代號」的寬表：open/high/low/close/vol 各一張。"""
    frames = []
    for d in days:
        df = load_day("price", d, fetch=False)
        if df is not None and len(df):
            frames.append(df.assign(date=pd.Timestamp(d)))
    long = pd.concat(frames, ignore_index=True)
    panel = {c: long.pivot_table(index="date", columns="code", values=c, aggfunc="last")
             for c in ["open", "high", "low", "close", "vol"]}
    panel["names"] = (long.sort_values("date").groupby("code")[["name", "market"]].last())
    return panel


def inst_panel(days: list[dt.date], fetch: bool = True) -> dict[str, pd.DataFrame]:
    """法人買賣超寬表（單位：張）。"""
    frames = []
    for d in days:
        df = load_day("inst", d, fetch=fetch)
        if df is not None and len(df):
            frames.append(df.assign(date=pd.Timestamp(d)))
    if not frames:
        return {"foreign": pd.DataFrame(), "trust": pd.DataFrame()}
    long = pd.concat(frames, ignore_index=True)
    return {c: long.pivot_table(index="date", columns="code", values=c, aggfunc="last")
            for c in ["foreign", "trust"]}


# ---------------------------------------------------------------------------
# 最新基本面快照（產業別、最新財報 EPS、最新月營收），不分日期、每次執行重抓
# ---------------------------------------------------------------------------

def _openapi(url: str, host: str):
    try:
        j = _get_json(url, host)
    except RuntimeError as e:
        print(f"  [警告] {e}")
        return []
    return j if isinstance(j, list) else []


def fundamentals() -> pd.DataFrame:
    rows: dict[str, dict] = {}

    def put(code, **kw):
        code = str(code).strip()
        if is_stock_code(code):
            rows.setdefault(code, {"code": code}).update(kw)

    # 產業別代碼（17 = 金融保險業）
    for r in _openapi("https://openapi.twse.com.tw/v1/opendata/t187ap03_L", "twse"):
        put(r.get("公司代號"), industry_code=str(r.get("產業別", "")).strip())
    for r in _openapi("https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O", "tpex"):
        put(r.get("SecuritiesCompanyCode"), industry_code=str(r.get("SecuritiesIndustryCode", "")).strip())

    # 最新一期財報 EPS
    for r in _openapi("https://openapi.twse.com.tw/v1/opendata/t187ap14_L", "twse"):
        put(r.get("公司代號"), eps=to_num(r.get("基本每股盈餘(元)")),
            eps_period=f"{r.get('年度')}Q{r.get('季別')}", industry=r.get("產業別"))
    for r in _openapi("https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap14_O", "tpex"):
        put(r.get("SecuritiesCompanyCode"), eps=to_num(r.get("基本每股盈餘")),
            eps_period=f"{r.get('Year')}Q{r.get('季別')}", industry=r.get("產業別"))

    # 最新月營收年增率與今年累計年增率
    for url, host in [("https://openapi.twse.com.tw/v1/opendata/t187ap05_L", "twse"),
                      ("https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap05_O", "tpex")]:
        for r in _openapi(url, host):
            put(r.get("公司代號"), rev_month=r.get("資料年月"),
                rev_yoy=to_num(r.get("營業收入-去年同月增減(%)")),
                rev_cum_yoy=to_num(r.get("累計營業收入-前期比較增減(%)")),
                industry=r.get("產業別") or rows.get(str(r.get("公司代號")).strip(), {}).get("industry"))

    df = pd.DataFrame(rows.values())
    for c in ["industry_code", "industry", "eps", "eps_period", "rev_month", "rev_yoy", "rev_cum_yoy"]:
        if c not in df:
            df[c] = None
    return df.set_index("code")
