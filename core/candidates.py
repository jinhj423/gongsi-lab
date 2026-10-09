"""후보 발굴 실행 + 텔레그램 후보 카드 + 직원용 데이터. PROTECTED.

  python3 core/candidates.py run kr|us     # 비교·선정(screen) -> 텔레그램 후보 카드(+차트) 발송
  python3 core/candidates.py show kr|us    # 직원이 리포트 쓸 때 읽는 데이터 (신규 후보 상세 + 섹터 비교)

카드의 숫자는 전부 시스템이 공시·주가로 계산한 값이다. 직원(Claude)은 이 숫자를 바꾸지 않고
신규 후보에 대한 리포트(사장님 양식)를 써서 뒤따라 보낸다.
"""
from __future__ import annotations

import json
import os
import sys

from common import JOURNAL, die, is_agent, out
import notify
import screen
import universe


def f_pct(x, sign=True, nd=0) -> str:
    if x is None:
        return "-"
    return f"{x * 100:+.{nd}f}%" if sign else f"{x * 100:.{nd}f}%"


def f_pp(x) -> str:
    return "-" if x is None else f"{x * 100:+.0f}%p"


def tv_symbol(market: str, ticker: str) -> str | None:
    if market == "kr":
        return f"KRX:{ticker}"
    try:
        import sources
        j = sources.sec_get("https://www.sec.gov/files/company_tickers_exchange.json").json()
        fields = j["fields"]
        ti, ei = fields.index("ticker"), fields.index("exchange")
        for row in j["data"]:
            if str(row[ti]).upper() == ticker.upper():
                ex = str(row[ei]).upper()
                return f"{'NASDAQ' if ex == 'NASDAQ' else 'NYSE' if ex == 'NYSE' else ex}:{ticker.upper()}"
    except Exception:  # noqa: BLE001
        return None
    return None


def tv_link(market: str, ticker: str) -> str:
    sym = f"KRX:{ticker}" if market == "kr" else ticker.upper()
    return f"https://www.tradingview.com/chart/?symbol={sym}"


def chart_png(symbol: str) -> bytes | None:
    key = os.environ.get("CHART_IMG_API_KEY")
    if not key or not symbol:
        return None
    import requests
    c = universe.conf().get("chart", {})
    try:
        r = requests.post("https://api.chart-img.com/v2/tradingview/advanced-chart", timeout=60,
                          headers={"x-api-key": key, "content-type": "application/json"},
                          json={"symbol": symbol, "interval": c.get("interval", "1D"), "width": c.get("width", 800),
                                "height": c.get("height", 500), "theme": c.get("theme", "dark"),
                                "studies": [{"name": "Volume"}]})
        if r.status_code == 200 and r.headers.get("content-type", "").startswith("image/"):
            return r.content
        print(f"[chart] {symbol}: HTTP {r.status_code} {r.text[:120]}", file=sys.stderr)
    except Exception as e:  # noqa: BLE001
        print(f"[chart] {symbol}: {e}", file=sys.stderr)
    return None


def card(res: dict) -> str:
    m = res["market"]
    label = "국내" if m == "kr" else "미국"
    asof = (res.get("asof") or "")[5:].replace("-", "/")
    L = [f"📈 매매 후보 · {label} 시총 상위 {res['universe']} · {asof} 종가 기준", ""]
    if not res["candidates"]:
        L.append("조건(대추세 상승 + 영업이익 증가)을 모두 통과한 종목이 없습니다.")
    for i, c in enumerate(res["candidates"], 1):
        new = "🆕 " if c.get("is_new") else ""
        code = c["ticker"]
        L.append(f"{i}. {new}{c['name']}({code}) · {c.get('sector', '')} · 점수 {c['score']:.0f}")
        L.append(f"   영업이익 {f_pct(c.get('op_yoy'))} · 매출 {f_pct(c.get('rev_yoy'))} YoY ({c.get('latest_q') or '-'})"
                 f"{' · 흑자전환' if c.get('turnaround') else ''}")
        per = f"PER {c['per']:.1f}" if c.get("per") else "PER -"
        L.append(f"   6M 지수대비 {f_pp(c.get('rs_126'))} · 정배열 {c.get('ema_align', '-')}/4 · "
                 f"52주고점 {f_pct(c.get('from_52w_high'))} · {per} · 섹터 {c.get('sector_rank', '-')}위")
    if res.get("dropped"):
        names = {r["ticker"]: r["name"] for r in res.get("all", [])}
        L += ["", "후보 제외: " + ", ".join(names.get(t, t) for t in res["dropped"])]
    if res.get("warnings"):
        L += ["", "⚠️ 대추세 이탈(SMA200 아래, 시총 30위 내): " + ", ".join(w["name"] for w in res["warnings"][:8])]
    secs = [s for s in res.get("sectors", []) if s.get("median_rs_126") is not None and s["n"] >= 3][:3]
    if secs:
        L += ["", "강한 섹터(6M 지수대비 중앙값): " + " · ".join(f"{s['sector']} {f_pp(s['median_rs_126'])}" for s in secs)]
    n_new = sum(1 for c in res["candidates"] if c.get("is_new"))
    L += ["", f"신규 {n_new}건은 직원이 리포트를 써서 이어서 보냅니다." if n_new else "신규 후보 없음 - 기존 후보 유지.",
          "※ 계산 근거: 정기보고서·10-Q XBRL 실적, 일봉 종가. 매매 판단은 사장님 몫."]
    return "\n".join(L)


def run(market: str, refresh: bool = True) -> dict:
    r = screen.run(market, refresh=refresh)
    res = screen.load(market)
    notify.send(card(res), kind="report")
    sent = 0
    for c in [c for c in res["candidates"] if c.get("is_new")][: universe.conf()["new_reports_per_run"]]:
        png = chart_png(tv_symbol(market, c["ticker"]))
        if png and notify.send_photo(png, f"{c['name']}({c['ticker']}) 일봉 · {tv_link(market, c['ticker'])}", kind="report"):
            sent += 1
    return {**r, "charts": sent}


def show(market: str) -> dict:
    """직원용: 신규 후보 상세(분기 실적 포함) + 같은 섹터 비교 + 기존 리포트 유무."""
    import fundamentals
    res = screen.load(market)
    if not res:
        die("아직 스크리닝 결과가 없습니다 (core/candidates.py run 먼저)")
    fund = fundamentals.load(market).get("companies", {})
    lim = universe.conf()["new_reports_per_run"]
    new = [c for c in res["candidates"] if c.get("is_new")][:lim]
    out_rows = []
    for c in new:
        peers = [r for r in res["all"] if r.get("sector") == c.get("sector") and r["ticker"] != c["ticker"]][:5]
        rp = JOURNAL / "reports" / f"{market}-{c['ticker']}.md"
        out_rows.append({
            "ticker": c["ticker"], "name": c["name"], "sector": c.get("sector"), "industry": c.get("industry"),
            "rank": c["rank"], "score": c["score"], "score_parts": c.get("score_parts"), "sector_rank": c.get("sector_rank"),
            "market_cap": c.get("market_cap"), "close": c.get("close"), "date": c.get("date"),
            "trend": {k: c.get(k) for k in ("sma200", "above_sma200", "sma200_slope20", "ema_align", "sma200_cross_up_10d",
                                            "from_52w_high", "ret_63", "ret_126", "rs_63", "rs_126", "traded_value20")},
            "fundamentals": {k: c.get(k) for k in ("latest_q", "rev", "op", "rev_yoy", "op_yoy", "op_margin", "op_margin_chg",
                                                   "turnaround", "ttm_ni", "equity", "per", "pbr")},
            "quarters": fund.get(c["ticker"], {}).get("quarters", {}),
            "peers": [{"name": p["name"], "ticker": p["ticker"], "score": p.get("score"), "op_yoy": p.get("op_yoy"),
                       "rs_126": p.get("rs_126"), "per": p.get("per")} for p in peers],
            "existing_report": str(rp.relative_to(JOURNAL.parent)) if rp.exists() else None,
            "tradingview": tv_link(market, c["ticker"]),
        })
    return {"market": market, "asof": res.get("asof"), "new_candidates": out_rows,
            "all_candidates": [{"rank": c["rank"], "name": c["name"], "ticker": c["ticker"], "score": c["score"],
                                "is_new": c.get("is_new")} for c in res["candidates"]],
            "sectors": res.get("sectors", [])[:8],
            "units": "KR 금액 단위: 원, US: 달러. 비율은 소수 (0.12 = 12%)."}


def main() -> None:
    a = sys.argv[1:]
    if len(a) != 2 or a[0] not in ("run", "show") or a[1] not in ("kr", "us"):
        sys.exit(__doc__)
    if a[0] == "run":
        if is_agent():
            die("에이전트는 run 을 실행할 수 없습니다 (show 만)")
        out(run(a[1]))
    else:
        print(json.dumps(show(a[1]), ensure_ascii=False, indent=1, default=float))


if __name__ == "__main__":
    main()
