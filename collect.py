#!/usr/bin/env python3
"""밸런스VIP 인사이드 — 깃허브 액션용 수집기.

Claude 클라우드에서 막힌 출처(CNN·CNBC·야후)만 모아 data/latest.json 에 저장한다.
Claude 의 예약 작업이 이 파일을 읽어 대시보드에 합친다.
개인 정보·키·비밀번호는 쓰지 않는다(공개 저장소).
"""
import json, os, time, urllib.request, urllib.parse
from datetime import datetime, timezone, timedelta

KST = timezone(timedelta(hours=9))
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
      "Accept": "application/json,text/plain,*/*"}
OUT = {"asof": datetime.now(KST).strftime("%Y-%m-%dT%H:%M+09:00"), "errors": {},
       "quotes": {}, "intra": {}, "daily": {}}


def get(url, tries=3):
    err = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:
            err = e
            time.sleep(2 * (i + 1))
    raise err


# 대시보드 키 → 야후 심볼
YAHOO = {
    "spx": "^GSPC", "ixic": "^IXIC", "dji": "^DJI", "sox": "^SOX", "es": "ES=F", "nq": "NQ=F",
    "vix": "^VIX", "tnx": "^TNX", "dxy": "DX-Y.NYB", "krw": "KRW=X", "wti": "CL=F", "gold": "GC=F",
    "btc": "BTC-USD", "ewy": "EWY", "n225": "^N225", "hsi": "^HSI", "sh": "000001.SS", "twii": "^TWII",
}


def yahoo(sym, rng, interval):
    u = ("https://query1.finance.yahoo.com/v8/finance/chart/" + urllib.parse.quote(sym)
         + f"?range={rng}&interval={interval}&includePrePost=false")
    r = get(u)["chart"]["result"][0]
    q = r["indicators"]["quote"][0]
    return r["meta"], r.get("timestamp") or [], q


def r4(v):
    return None if v is None else round(float(v), 4)


def collect_yahoo():
    for k, sym in YAHOO.items():
        try:
            meta, ts, q = yahoo(sym, "1d", "5m")
            off = meta.get("gmtoffset", 0)
            pts = []
            for t, c in zip(ts, q.get("close") or []):
                if c is None:
                    continue
                lt = datetime.fromtimestamp(t + off, timezone.utc)
                pts.append([lt.strftime("%H%M"), r4(c)])
            day = datetime.fromtimestamp(ts[-1] + off, timezone.utc) if ts else None
            prev = meta.get("chartPreviousClose") or meta.get("previousClose")
            price = meta.get("regularMarketPrice")
            OUT["intra"][k] = {"sym": sym, "date": day.strftime("%Y%m%d") if day else None,
                               "tz": meta.get("exchangeTimezoneName"), "prev": r4(prev), "i": pts}
            chg = None
            if price is not None and prev:
                chg = round((price / prev - 1) * 100, 2)
            OUT["quotes"][k] = {"price": r4(price), "prev": r4(prev), "chg": chg,
                                "t": datetime.fromtimestamp(meta.get("regularMarketTime", 0), timezone.utc)
                                .strftime("%Y-%m-%dT%H:%M:%SZ")}
        except Exception as e:
            OUT["errors"]["intra:" + k] = str(e)[:200]
        try:
            meta, ts, q = yahoo(sym, "9mo", "1d")
            off = meta.get("gmtoffset", 0)
            rows = []
            for t, o, h, l, c in zip(ts, q.get("open") or [], q.get("high") or [],
                                     q.get("low") or [], q.get("close") or []):
                if None in (o, h, l, c):
                    continue
                d = datetime.fromtimestamp(t + off, timezone.utc).strftime("%Y%m%d")
                rows.append([d, r4(o), r4(h), r4(l), r4(c)])
            OUT["daily"][k] = rows
        except Exception as e:
            OUT["errors"]["daily:" + k] = str(e)[:200]
        time.sleep(0.4)


def collect_fng():
    try:
        d = get("https://production.dataviz.cnn.io/index/fearandgreed/graphdata")
        f = d["fear_and_greed"]
        hist = []
        for p in (d.get("fear_and_greed_historical") or {}).get("data", []):
            day = datetime.fromtimestamp(p["x"] / 1000, timezone.utc).strftime("%Y-%m-%d")
            hist.append([day, round(p["y"], 1)])
        # 같은 날 여러 점이면 마지막 값만
        dd = {}
        for day, v in hist:
            dd[day] = v
        OUT["fng"] = {"score": round(f["score"], 1), "rating": f.get("rating"),
                      "prev_close": r4(f.get("previous_close")), "week": r4(f.get("previous_1_week")),
                      "month": r4(f.get("previous_1_month")), "year": r4(f.get("previous_1_year")),
                      "t": f.get("timestamp"), "hist": sorted(dd.items())[-260:]}
    except Exception as e:
        OUT["errors"]["fng"] = str(e)[:200]


def num(v):
    try:
        return float(str(v).replace(",", "").replace("%", "").replace("+", ""))
    except Exception:
        return None


def collect_vkospi():
    try:
        u = ("https://quote.cnbc.com/quote-html-webservice/restQuote/symbolType/symbol?symbols=.KSVKOSPI"
             "&requestMethod=itv&noform=1&partnerId=2&fund=1&exthrs=1&output=json&events=1")
        q = get(u)["FormattedQuoteResult"]["FormattedQuote"][0]
        OUT["vkospi"] = {"src": "cnbc", "sym": ".KSVKOSPI", "price": num(q.get("last")),
                         "pct": num(q.get("change_pct")), "chg": num(q.get("change")),
                         "prev": num(q.get("previous_day_closing")), "t": q.get("last_time")}
    except Exception as e:
        OUT["errors"]["vkospi"] = str(e)[:200]


def probe_krx():
    """야간선물용 — 접속 가능 여부만 기록(다음 단계에서 수집 방식 결정)."""
    try:
        req = urllib.request.Request("https://data.krx.co.kr/contents/MDC/MAIN/main/index.cmd", headers=UA)
        with urllib.request.urlopen(req, timeout=20) as r:
            OUT["krx_probe"] = r.status
    except Exception as e:
        OUT["errors"]["krx_probe"] = str(e)[:200]


if __name__ == "__main__":
    collect_fng()
    collect_vkospi()
    collect_yahoo()
    probe_krx()
    os.makedirs("data", exist_ok=True)
    with open("data/latest.json", "w", encoding="utf-8") as f:
        json.dump(OUT, f, ensure_ascii=False, separators=(",", ":"))
    print("asof", OUT["asof"], "errors", json.dumps(OUT["errors"], ensure_ascii=False))
