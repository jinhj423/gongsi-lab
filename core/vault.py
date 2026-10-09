"""Obsidian export: 섹터 > 기업 순서로 정리된 노트를 보관함에 쓴다. PROTECTED.

  python3 core/vault.py export [--no-sync]   # (ob sync 로 내려받기) -> 노트 재생성 -> (ob sync 로 올리기)
  python3 core/vault.py status

보관함 구조 (<보관함>/<root_folder>/):
  대시보드.md                              전체 성적·섹터 표·최근 보고·보유 페이퍼 포지션
  섹터/<섹터>/섹터 - <섹터>.md              섹터 안 기업 목록, 섹터별 예측 성적, 최근 예측
  섹터/<섹터>/<회사명> (<코드>).md          기업 노트: 예측 기록, 직원 리서치 메모, 공시 타임라인
  보고/<YYYY-MM>/<날짜 시각> 예측 보고.md     텔레그램으로 보낸 보고 원문 (회사명은 기업 노트 링크)

기업 노트 맨 아래 "## 내 메모" 부분은 사장님 영역 - 자동 갱신 때 그대로 보존된다.
어떤 기업이 노트를 갖나: 관심종목 + 직원이 예측했거나 리서치 노트를 쓴 기업.
설정: config/vault.json. 서버에서는 env 파일의 VAULT_DIR 로 켠다 (값이 있으면 enabled + dir 대체).
주의: config/ 를 서버에서 직접 고치면 loop.sh 가 사이클 후 되돌린다. PC 에서 고쳐 push -> /update.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import shutil
import statistics
import subprocess
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

from common import JOURNAL, ROOT, STATE, die, is_agent, out, read_jsonl
import ledger
import sectors
from classify import label

KST = ZoneInfo("Asia/Seoul")
MEMO_HEAD = "## 내 메모"
MEMO_MARK = "<!-- 이 아래는 자동 갱신 때 보존됩니다. 자유롭게 쓰세요. -->"
INDEX = STATE / "vault-index.json"


def conf() -> dict:
    c = json.loads((ROOT / "config" / "vault.json").read_text(encoding="utf-8"))
    if os.environ.get("VAULT_DIR"):
        c["dir"] = os.environ["VAULT_DIR"]
        c["enabled"] = True
    return c


def safe(s: str) -> str:
    s = re.sub(r'[\\/:*?"<>|#^\[\]]', " ", s)
    return re.sub(r"\s+", " ", s).strip() or "이름없음"


def cell(s) -> str:
    return str(s if s is not None else "").replace("|", "/").replace("\n", " ")


def money(market: str, v: float, sign: bool = False) -> str:
    n = f"{abs(v):,.0f}"
    sg = ("+" if v >= 0 else "-") if sign else ("-" if v < 0 else "")
    return f"{sg}{n}원" if market == "kr" else f"{sg}${n}"


def pct(x) -> str:
    return "" if x is None else f"{x * 100:+.1f}%"


def ob_sync(vault: Path) -> str:
    if not shutil.which("ob"):
        return "ob 없음 (Obsidian Headless 미설치) - 로컬에만 기록"
    try:
        p = subprocess.run(["ob", "sync", "--path", str(vault)], capture_output=True, text=True, timeout=600)
        return "ok" if p.returncode == 0 else f"실패: {(p.stdout + p.stderr)[-300:]}"
    except subprocess.TimeoutExpired:
        return "실패: 10분 초과"


def preserved_memo(path: Path | None) -> str:
    if path and path.exists():
        text = path.read_text(encoding="utf-8")
        i = text.find(MEMO_HEAD)
        if i >= 0:
            return text[i:].rstrip() + "\n"
    return f"{MEMO_HEAD}\n{MEMO_MARK}\n\n"


def watchlist() -> dict[tuple[str, str], str]:
    wl = {}
    p = ROOT / "config" / "my_watchlist.txt"
    if p.exists():
        for ln in p.read_text(encoding="utf-8").splitlines():
            parts = ln.split("#")[0].split()
            if len(parts) >= 2 and parts[0] in ("kr", "us"):
                wl[(parts[0], parts[1].upper() if parts[0] == "us" else parts[1].zfill(6))] = " ".join(parts[2:])
    return wl


def agent_notes() -> dict[tuple[str, str], str]:
    d = JOURNAL / "notes"
    res = {}
    if d.exists():
        for p in d.glob("*.md"):
            m = re.match(r"(kr|us)-(.+)\.md$", p.name)
            if m:
                body = p.read_text(encoding="utf-8")
                body = re.sub(r"\A#\s+[^\n]*\n", "", body).strip()  # 노트의 H1 은 기업 노트 제목과 겹치므로 제거
                body = re.sub(r"(?m)^## ", "### ", body)
                res[(m.group(1), m.group(2).upper() if m.group(1) == "us" else m.group(2).zfill(6))] = body
    return res


def agent_reports() -> dict[tuple[str, str], str]:
    d = JOURNAL / "reports"
    res = {}
    if d.exists():
        for p in d.glob("*.md"):
            m = re.match(r"(kr|us)-(.+)\.md$", p.name)
            if m:
                body = p.read_text(encoding="utf-8")
                body = re.sub(r"\A#\s+[^\n]*\n", "", body).strip()
                body = re.sub(r"(?m)^## ", "### ", body)
                res[(m.group(1), m.group(2).upper() if m.group(1) == "us" else m.group(2).zfill(6))] = body
    return res


def screen_rows() -> dict[tuple[str, str], dict]:
    """최신 스크리닝 결과 (후보 + 전체 순위)."""
    res = {}
    for m in ("kr", "us"):
        p = STATE / f"screen-{m}.json"
        if p.exists():
            s = json.loads(p.read_text(encoding="utf-8"))
            cands = {c["ticker"] for c in s.get("candidates", [])}
            for r in s.get("all", []):
                res[(m, r["ticker"])] = {**r, "is_candidate": r["ticker"] in cands, "asof": s.get("asof")}
    return res


def candidate_tickers() -> set[tuple[str, str]]:
    keep = set()
    for c in read_jsonl(JOURNAL / "candidates.jsonl"):
        keep.add((c["market"], c["ticker"].upper() if c["market"] == "us" else c["ticker"]))
    return keep


def score(rows: list[tuple[dict, dict | None]]) -> dict:
    done = [(f, r) for f, r in rows if r and r.get("status") == "ok"]
    d = {"n": len(rows), "resolved": len(done)}
    if done:
        d["hit"] = sum((r["up"] == 1) == (f["est_prob"] >= 0.5) for f, r in done) / len(done)
        d["brier_delta"] = statistics.fmean([r["brier"] for _, r in done]) - statistics.fmean([r["base_brier"] for _, r in done])
    return d


def fmt_score(d: dict) -> str:
    if not d.get("resolved"):
        return f"예측 {d['n']}건 · 정산 0건"
    return (f"예측 {d['n']}건 · 정산 {d['resolved']}건 · 방향 적중 {d['hit'] * 100:.0f}% · "
            f"brier_delta {d['brier_delta']:+.4f}" + (" (표본 작음)" if d["resolved"] < 30 else ""))


def build(vault: Path, c: dict) -> dict:
    root = vault / c["root_folder"]
    feed = ledger.feed_events()
    fc = ledger.forecasts()
    res = ledger.resolutions()
    wl = watchlist()
    notes = agent_notes()
    reports = agent_reports()
    scr = screen_rows()

    comps: set[tuple[str, str]] = set(wl) | set(notes) | set(reports) | candidate_tickers()
    for f in fc:
        comps.add((f["market"], f["ticker"].upper() if f["market"] == "us" else f["ticker"]))

    by_comp_ev: dict = {}
    for e in feed.values():
        k = (e["market"], e["ticker"].upper() if e["market"] == "us" else e["ticker"])
        if k in comps:
            by_comp_ev.setdefault(k, []).append(e)
    by_comp_fc: dict = {}
    for f in fc:
        k = (f["market"], f["ticker"].upper() if f["market"] == "us" else f["ticker"])
        by_comp_fc.setdefault(k, []).append((f, res.get(f["id"])))

    index = json.loads(INDEX.read_text(encoding="utf-8")) if INDEX.exists() else {}
    info: dict = {}
    for k in comps:
        m, t = k
        sec = sectors.lookup(m, t, network=True)
        if (m, t) in scr and scr[(m, t)].get("sector") and (m == "us" or sec["sector"] == sectors.UNKNOWN):
            sec = {**sec, "sector": scr[(m, t)]["sector"]}  # 미국은 S&P500 의 GICS 섹터를 우선
        name = ((scr.get((m, t)) or {}).get("name") or sec.get("name") or next((e.get("name") for e in by_comp_ev.get(k, []) if e.get("name")), "")
                or next((f.get("name") for f, _ in by_comp_fc.get(k, []) if f.get("name")), "") or wl.get(k, "") or t)
        info[k] = {"name": name, "sector": safe(sec["sector"]), "raw_sector": sec.get("raw_sector", ""),
                   "industry": sec.get("industry", ""), "note": safe(f"{name} ({t})")}

    now = dt.datetime.now(KST).strftime("%Y-%m-%d %H:%M")
    written = 0
    new_index = {}
    for k, inf in info.items():
        m, t = k
        rel = Path("섹터") / inf["sector"] / f"{inf['note']}.md"
        path = root / rel
        old = root / index[f"{m}:{t}"] if f"{m}:{t}" in index else None
        memo = preserved_memo(old if old and old.exists() else path)
        if old and old.exists() and old != path:
            old.unlink()  # 섹터가 바뀐 경우 이동 (메모는 보존)
        evs = sorted(by_comp_ev.get(k, []), key=lambda e: (e["filed_date"], e.get("filed_at") or ""), reverse=True)
        rows = sorted(by_comp_fc.get(k, []), key=lambda x: x[0]["recorded_at"], reverse=True)
        tag_sector = inf["sector"].replace(" ", "_")
        L = [
            "---", f"market: {m}", f'ticker: "{t}"', f"name: {json.dumps(inf['name'], ensure_ascii=False)}",
            f"sector: {json.dumps(inf['sector'], ensure_ascii=False)}",
            f"raw_sector: {json.dumps(inf['raw_sector'], ensure_ascii=False)}",
            f"industry: {json.dumps(inf['industry'], ensure_ascii=False)}",
            f"watchlist: {'true' if k in wl else 'false'}",
            f"tags: [공시랩, 섹터/{tag_sector}]", f"updated: {now}", "---",
            f"# {inf['name']} ({t})", "",
            f"섹터: [[섹터 - {inf['sector']}]] · {'관심종목 · ' if k in wl else ''}{inf['industry']}",
            "",
        ]
        sr = scr.get(k)
        if sr:
            per_s = f"{sr['per']:.1f}" if sr.get("per") else "-"
            L += [f"## 시총 상위 비교 ({sr.get('asof')} 기준)" + (" · 🟢 현재 매매 후보" if sr.get("is_candidate") else ""), "",
                  "| 점수 | 시장 순위 | 섹터 순위 | 영업이익 YoY | 매출 YoY | 6M 지수대비 | 정배열 | SMA200 | PER |",
                  "|---|---|---|---|---|---|---|---|---|",
                  f"| {sr.get('score', '')} | {sr.get('rank', '')} | {sr.get('sector_rank', '')} | {pct(sr.get('op_yoy'))} | "
                  f"{pct(sr.get('rev_yoy'))} | {pct(sr.get('rs_126'))} | {sr.get('ema_align', '-')}/4 | "
                  f"{'위' if sr.get('above_sma200') else '아래'} | {per_s} |", ""]
            if sr.get("gate_fails"):
                L += ["후보 제외 사유: " + ", ".join(sr["gate_fails"]), ""]
        if k in reports:
            L += ["## 후보 리포트", "", reports[k], ""]
        L += ["## 예측 기록", "", fmt_score(score(rows)), ""]
        if rows:
            L += ["| 기록일 | 공시 | 지평 | 예측 | 기저율 | 결과(초과수익) | 판정 | 페이퍼 |",
                  "|---|---|---|---|---|---|---|---|"]
            for f, r in rows:
                if r and r.get("status") == "ok":
                    ok = "✅" if (r["up"] == 1) == (f["est_prob"] >= 0.5) else "❌"
                    resv, pnl = pct(r["ar"]), (f"{r['pnl']:+,.0f}" if f["decision"] == "bet" else "")
                elif r:
                    ok, resv, pnl = "무효", r.get("reason", ""), ""
                else:
                    ok, resv, pnl = "대기", "", ("보유" if f["decision"] == "bet" else "")
                L.append(f"| {f['recorded_at'][:10]} | {cell(label(f['type']))} | {f['h']}일 | {f['est_prob']:.2f} | "
                         f"{f['base_p']:.2f} | {resv} | {ok} | {pnl} |")
            L.append("")
            L += ["### 판단 근거 (기록 당시)", ""]
            for f, _ in rows[:10]:
                L.append(f"- {f['recorded_at'][:10]} {label(f['type'])} ({f['decision']}): {cell(f['rationale'])}")
            L.append("")
        L += ["## 직원 리서치 메모", ""]
        L += [notes[k], ""] if k in notes else ["(아직 없음)", ""]
        L += [f"## 공시 타임라인 (최근 {c['timeline_max']}건)", ""]
        if evs:
            for e in evs[: c["timeline_max"]]:
                corr = " (정정)" if e.get("corr") else ""
                link = f"[{cell(e['title'])}]({e['url']})" if e.get("url") else cell(e["title"])
                L.append(f"- {e['filed_date']} {link}{corr} · {label(e['type'])}")
        else:
            L.append("(수집된 공시 없음)")
        L += ["", memo]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(L), encoding="utf-8")
        new_index[f"{m}:{t}"] = str(rel)
        written += 1

    # ---- 섹터 노트
    by_sector: dict[str, list] = {}
    for k, inf in info.items():
        by_sector.setdefault(inf["sector"], []).append(k)
    sector_scores = {}
    for sec_name, ks in by_sector.items():
        rows = [x for k in ks for x in by_comp_fc.get(k, [])]
        sc = score(rows)
        sector_scores[sec_name] = (len(ks), sc)
        L = ["---", f"sector: {json.dumps(sec_name, ensure_ascii=False)}", f"tags: [공시랩, 섹터/{sec_name.replace(' ', '_')}]",
             f"updated: {now}", "---", f"# 섹터 - {sec_name}", "", "[[대시보드]]", "", "## 성적", "", fmt_score(sc), "",
             "## 기업", ""]
        for k in sorted(ks, key=lambda k: info[k]["name"]):
            n_fc = len(by_comp_fc.get(k, []))
            last = max((e["filed_date"] for e in by_comp_ev.get(k, [])), default="-")
            L.append(f"- [[{info[k]['note']}]] · 예측 {n_fc}건 · 최근 공시 {last}" + (" · 관심" if k in wl else ""))
        recent = sorted(rows, key=lambda x: x[0]["recorded_at"], reverse=True)[: c["sector_recent_max"]]
        if recent:
            L += ["", "## 최근 예측", "", "| 기록일 | 기업 | 공시 | 예측 | 결과 |", "|---|---|---|---|---|"]
            for f, r in recent:
                k = (f["market"], f["ticker"].upper() if f["market"] == "us" else f["ticker"])
                resv = pct(r["ar"]) if r and r.get("status") == "ok" else ("무효" if r else "대기")
                L.append(f"| {f['recorded_at'][:10]} | [[{info[k]['note']}]] | {cell(label(f['type']))} | {f['est_prob']:.2f} | {resv} |")
        p = root / "섹터" / sec_name / f"섹터 - {sec_name}.md"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("\n".join(L) + "\n", encoding="utf-8")

    # ---- 보고 원문 (회사명 -> 기업 노트 링크)
    def linkify(text: str) -> str:
        for k, inf in info.items():
            pat = re.escape(inf["name"]) + r"\s*\(\s*" + re.escape(k[1]) + r"\s*\)"
            text = re.sub(pat, f"[[{inf['note']}|{inf['name']}({k[1]})]]", text)
        return text

    report_titles = []
    sent = JOURNAL / "outbox" / "sent"
    for p in sorted(sent.glob("*.md")) if sent.exists() else []:
        mt = re.match(r"(\d{8})-(\d{4})-?([\w-]*)\.md$", p.name)
        if not mt:
            continue
        t_utc = dt.datetime.strptime(mt.group(1) + mt.group(2), "%Y%m%d%H%M").replace(tzinfo=dt.timezone.utc)
        t = t_utc.astimezone(KST)
        kind = {"forecast": "예측 보고", "research": "주간 리서치 보고"}.get(mt.group(3), "후보 리포트" if "candidates" in p.name else "보고")
        if "candidates" in p.name:
            kind += " (국내)" if p.name.endswith("-kr.md") else " (미국)" if p.name.endswith("-us.md") else ""
        title = f"{t:%Y-%m-%d %H%M} {kind}"
        dest = root / "보고" / f"{t:%Y-%m}" / f"{title}.md"
        report_titles.append(title)
        if not dest.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(f"---\ntags: [공시랩, 보고]\nkind: {kind}\nsent: {t:%Y-%m-%d %H:%M}\n---\n"
                            f"[[대시보드]]\n\n{linkify(p.read_text(encoding='utf-8'))}\n", encoding="utf-8")

    # ---- 대시보드
    all_rows = [x for v in by_comp_fc.values() for x in v]
    L = ["---", "tags: [공시랩]", f"updated: {now}", "---", "# 공시랩 대시보드", "", f"갱신: {now} (KST)", "",
         "## 전체 성적", "", fmt_score(score(all_rows)), ""]
    for m in ("kr", "us"):
        b = ledger.book(m)
        L.append(f"- {m.upper()} 페이퍼: 현금 {money(m, b['cash'])} · 실현 {money(m, b['realized_pnl'], True)} · "
                 f"보유 {b['open_bets']}건 · 승 {b['wins']}/{b['settled_bets']}")
    L += ["", "## 섹터", "", "| 섹터 | 기업 수 | 예측 | 정산 | brier_delta |", "|---|---|---|---|---|"]
    for sec_name, (nc, sc) in sorted(sector_scores.items(), key=lambda x: -x[1][1]["n"]):
        bd = f"{sc['brier_delta']:+.4f}" if sc.get("resolved") else ""
        L.append(f"| [[섹터 - {sec_name}\\|{sec_name}]] | {nc} | {sc['n']} | {sc['resolved']} | {bd} |")
    open_bets = [(f, k) for k, v in by_comp_fc.items() for f, r in v if f["decision"] == "bet" and r is None]
    if open_bets:
        L += ["", "## 보유 중인 페이퍼 포지션", ""]
        for f, k in sorted(open_bets, key=lambda x: x[0]["recorded_at"], reverse=True):
            L.append(f"- [[{info[k]['note']}]] {label(f['type'])} · 예측 {f['est_prob']:.2f} · {f['stake']:,.0f} · 기록 {f['recorded_at'][:10]}")
    for m, lab in (("kr", "국내"), ("us", "미국")):
        sp = STATE / f"screen-{m}.json"
        if not sp.exists():
            continue
        s = json.loads(sp.read_text(encoding="utf-8"))
        L += ["", f"## 매매 후보 · {lab} ({s.get('asof')} 기준)", "",
              "| 순위 | 기업 | 섹터 | 점수 | 영업이익 YoY | 6M 지수대비 | 신규 |", "|---|---|---|---|---|---|---|"]
        for c2 in s.get("candidates", []):
            kk = (m, c2["ticker"])
            nm = f"[[{info[kk]['note']}]]" if kk in info else f"{c2['name']} ({c2['ticker']})"
            L.append(f"| {c2['rank']} | {nm} | {cell(c2.get('sector'))} | {c2['score']} | {pct(c2.get('op_yoy'))} | "
                     f"{pct(c2.get('rs_126'))} | {'🆕' if c2.get('is_new') else ''} |")
    L += ["", "## 최근 보고", ""] + [f"- [[{t}]]" for t in reversed(report_titles[-10:])]
    (root / "대시보드.md").parent.mkdir(parents=True, exist_ok=True)
    (root / "대시보드.md").write_text("\n".join(L) + "\n", encoding="utf-8")

    INDEX.parent.mkdir(parents=True, exist_ok=True)
    INDEX.write_text(json.dumps(new_index, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"companies": written, "sectors": len(by_sector), "reports": len(report_titles), "root": str(root)}


def main() -> None:
    a = sys.argv[1:]
    if is_agent():
        die("에이전트는 보관함 내보내기를 실행할 수 없습니다 (journal/notes 에 쓰면 자동 반영)")
    if a[:1] == ["export"] and os.environ.get("OBSIDIAN_VAULT") and not os.environ.get("VAULT_DIR"):
        import obsync  # GitHub Actions: 매번 새 환경이라 로그인 + 임시 폴더 연결부터
        try:
            obsync.prepare()
        except Exception as e:  # noqa: BLE001
            die(f"옵시디언 준비 실패: {e}")
    c = conf()
    vault = Path(os.path.expanduser(c["dir"]))
    if a[:1] == ["status"]:
        out({"enabled": c["enabled"], "vault": str(vault), "exists": vault.exists(), "ob": bool(shutil.which("ob"))})
        return
    if a[:1] != ["export"]:
        sys.exit(__doc__)
    if not c["enabled"]:
        out({"skipped": "config/vault.json enabled=false"})
        return
    if not vault.exists():
        die(f"보관함 경로 없음: {vault} (ob sync-setup 먼저)")
    do_sync = c.get("sync") == "ob" and "--no-sync" not in a
    pull = ob_sync(vault) if do_sync else "skip"   # 폰에서 쓴 '내 메모' 를 먼저 받아온다
    r = build(vault, c)
    push = ob_sync(vault) if do_sync else "skip"
    out({**r, "pull": pull, "push": push})
    if do_sync and (pull.startswith("실패") or push.startswith("실패")):
        sys.exit(1)


if __name__ == "__main__":
    main()
