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


CNN_H = dict(UA, **{"Origin": "https://edition.cnn.com", "Referer": "https://edition.cnn.com/",
                    "Accept-Language": "en-US,en;q=0.9"})


def get_h(url, headers, tries=2):
    err = None
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=20) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:
            err = e
            time.sleep(2 * (i + 1))
    raise err


def collect_fng():
    try:
        # CNN 은 Origin/Referer 머리글이 없으면 거절(418)한다
        d = get_h("https://production.dataviz.cnn.io/index/fearandgreed/graphdata", CNN_H)
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


# ---------------- KRX 정보데이터시스템 — 코스피200 선물 주간·야간 (로그인 필요)
# 아이디·비밀번호는 깃허브 저장소 Settings → Secrets 의 KRX_ID / KRX_PW 로만 받는다(파일·로그에 남기지 않음).
import http.cookiejar
KRX_UA = UA["User-Agent"]
KRX_JSON = "https://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd"
KRX_REF = "https://data.krx.co.kr/contents/MDC/MDI/mdiLoader/index.cmd?menuId=MDC0201020202"
KRX_LOGIN_PAGE = "https://data.krx.co.kr/contents/MDC/COMS/client/MDCCOMS001.cmd"
KRX_LOGIN_JSP = "https://data.krx.co.kr/contents/MDC/COMS/client/view/login.jsp?site=mdc"
KRX_LOGIN = "https://data.krx.co.kr/contents/MDC/COMS/client/MDCCOMS001D1.cmd"
_krx = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))


def krx_login():
    kid, kpw = os.environ.get("KRX_ID", ""), os.environ.get("KRX_PW", "")
    if not kid or not kpw:
        raise RuntimeError("KRX_ID/KRX_PW 시크릿 없음")
    for url, ref in ((KRX_LOGIN_PAGE, None), (KRX_LOGIN_JSP, KRX_LOGIN_PAGE)):
        h = {"User-Agent": KRX_UA}
        if ref:
            h["Referer"] = ref
        with _krx.open(urllib.request.Request(url, headers=h), timeout=20) as r:
            r.read()

    def post(skip=False):
        p = {"mbrNm": "", "telNo": "", "di": "", "certType": "", "mbrId": kid, "pw": kpw}
        if skip:
            p["skipDup"] = "Y"
        req = urllib.request.Request(KRX_LOGIN, data=urllib.parse.urlencode(p).encode(), method="POST",
                                     headers={"User-Agent": KRX_UA, "Referer": KRX_LOGIN_PAGE,
                                              "Content-Type": "application/x-www-form-urlencoded"})
        with _krx.open(req, timeout=20) as r:
            return json.loads(r.read().decode("utf-8"))
    res = post()
    if res.get("_error_code") == "CD011":  # 중복 로그인
        res = post(True)
    if res.get("_error_code") != "CD001":
        raise RuntimeError("KRX 로그인 실패 " + str(res.get("_error_code")))


def krx_post(data):
    req = urllib.request.Request(KRX_JSON, data=urllib.parse.urlencode(data).encode(), method="POST",
                                 headers={"User-Agent": KRX_UA, "Referer": KRX_REF, "X-Requested-With": "XMLHttpRequest",
                                          "Content-Type": "application/x-www-form-urlencoded"})
    with _krx.open(req, timeout=40) as r:
        raw = r.read().decode("utf-8", "replace")
    if not raw.lstrip().startswith("{"):
        raise RuntimeError("KRX 비JSON 응답 " + raw[:40])
    p = json.loads(raw)
    return p.get("OutBlock_1") or p.get("output") or []


def collect_night():
    try:
        krx_login()
        now = datetime.now(KST)
        rows = krx_post({"bld": "dbms/MDC/STAT/standard/MDCSTAT12701", "locale": "ko_KR", "prodId": "KR___FUK2I",
                         "strtDd": (now - timedelta(days=100)).strftime("%Y%m%d"), "endDd": now.strftime("%Y%m%d"),
                         "aggBasTpCd": "", "share": "1", "money": "3", "csvxls_isNo": "false"})

        def pack(r):
            cl, chg = num(r.get("TDD_CLSPRC")), num(r.get("CMPPREVDD_PRC"))
            if not cl or chg is None:
                return None
            if str(r.get("FLUC_TP_CD")) == "2" and chg > 0:
                chg = -chg
            base = cl - chg
            return {"d": str(r.get("TRD_DD", ""))[:10].replace("/", "-"), "c": cl, "chg": chg,
                    "pct": round(chg / base * 100, 2) if base else None, "o": num(r.get("TDD_OPNPRC")),
                    "h": num(r.get("TDD_HGPRC")), "l": num(r.get("TDD_LWPRC")), "vol": num(r.get("ACC_TRDVOL")),
                    "name": str(r.get("ISU_NM", "")).strip()}
        night = day = None
        hist = []
        for r in rows:
            lab = str(r.get("TRD_DD", ""))
            x = pack(r)
            if not x:
                continue
            if night is None and "(야간)" in lab:
                night = x
            if day is None and "(주간)" in lab:
                day = x
            if len(hist) < 160:
                h = {k: x[k] for k in ("d", "c", "chg", "pct", "h", "l", "vol")}
                h["s"] = "야간" if "(야간)" in lab else "주간"
                hist.append(h)
        if not night:
            raise RuntimeError(f"야간 행 없음(받은 행 {len(rows)})")
        OUT["night"] = {"night": night, "day": day, "hist": hist,
                        "src": "KRX 정보데이터시스템 [15003] 최근월물 시세 추이(선물)",
                        "fetched": now.strftime("%Y-%m-%dT%H:%M+09:00")}
    except Exception as e:
        OUT["errors"]["night"] = (type(e).__name__ + ": " + str(e))[:200]


if __name__ == "__main__":
    collect_fng()
    collect_vkospi()
    collect_yahoo()
    collect_night()
    os.makedirs("data", exist_ok=True)
    with open("data/latest.json", "w", encoding="utf-8") as f:
        json.dump(OUT, f, ensure_ascii=False, separators=(",", ":"))
    print("asof", OUT["asof"], "errors", json.dumps(OUT["errors"], ensure_ascii=False))
