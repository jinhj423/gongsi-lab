"""Rule-based disclosure classification. PROTECTED.

KR: DART report_nm (보고서명) -> event type.
US: 8-K item numbers -> event type.
Order matters: first match wins.
"""
from __future__ import annotations

import re

KR_RULES: list[tuple[str, str]] = [
    ("자기주식취득신탁계약체결", "buyback_trust"),
    ("자기주식취득신탁계약해지", "buyback_trust_end"),
    ("자기주식취득결정", "buyback"),
    ("자기주식처분결정", "treasury_sale"),
    ("주식소각결정", "share_cancel"),
    ("유무상증자결정", "rights_offering"),
    ("유상증자결정", "rights_offering"),
    ("무상증자결정", "bonus_issue"),
    ("전환사채권발행결정", "cb"),
    ("신주인수권부사채권발행결정", "bw"),
    ("교환사채권발행결정", "eb"),
    ("단일판매공급계약해지", "contract_cancel"),
    ("단일판매공급계약체결", "supply_contract"),
    ("영업잠정실적", "prelim_earnings"),
    ("매출액또는손익구조", "earnings_change"),
    ("현금현물배당결정", "dividend"),
    ("현금배당결정", "dividend"),
    ("최대주주변경", "control_change"),
    ("타법인주식및출자증권취득결정", "acquisition"),
    ("타법인주식및출자증권처분결정", "divestiture"),
    ("회사분할합병결정", "split"),
    ("회사분할결정", "split"),
    ("회사합병결정", "merger"),
    ("감자결정", "capital_reduction"),
    ("유형자산취득결정", "capex"),
    ("유형자산처분결정", "asset_sale"),
    ("횡령배임", "embezzlement"),
    ("소송등의제기", "lawsuit"),
    ("주권매매거래정지", "trading_halt"),
    ("기술도입", "tech_deal"),
    ("기술이전", "tech_deal"),
    ("투자판단관련주요경영사항", "material_info"),
    ("주식등의대량보유상황보고서", "block_holding"),
    ("임원주요주주특정증권등소유상황보고서", "insider_holding"),
]

US_ITEM_PRIORITY: list[tuple[str, str]] = [
    ("1.03", "bankruptcy"),
    ("4.02", "restatement"),
    ("3.01", "delisting_notice"),
    ("2.01", "acquisition_completed"),
    ("1.01", "material_agreement"),
    ("1.02", "agreement_terminated"),
    ("2.02", "earnings"),
    ("2.05", "restructuring"),
    ("2.06", "impairment"),
    ("3.02", "unregistered_equity"),
    ("4.01", "auditor_change"),
    ("5.01", "control_change"),
    ("5.02", "exec_change"),
    ("5.07", "shareholder_vote"),
    ("2.03", "debt_obligation"),
    ("7.01", "reg_fd"),
    ("8.01", "other_event"),
]


def _norm(s: str) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣]", "", s)


def kr_type(report_nm: str) -> tuple[str, bool]:
    """Returns (type, is_correction)."""
    tags = re.findall(r"\[([^\]]*)\]", report_nm)
    corr = any("정정" in t for t in tags)
    body = _norm(re.sub(r"\[[^\]]*\]", "", report_nm))
    for key, typ in KR_RULES:
        if key in body:
            return typ, corr
    return "other", corr


def us_type(items: str) -> str:
    parts = {p.strip() for p in (items or "").split(",") if p.strip()}
    for item, typ in US_ITEM_PRIORITY:
        if item in parts:
            return typ
    return "other"


def all_types(market: str) -> list[str]:
    rules = KR_RULES if market == "kr" else US_ITEM_PRIORITY
    seen: list[str] = []
    for _, t in rules:
        if t not in seen:
            seen.append(t)
    return seen + ["other"]


LABELS = {
    # KR
    "buyback_trust": "자사주 신탁계약", "buyback_trust_end": "자사주 신탁해지", "buyback": "자사주 취득",
    "treasury_sale": "자사주 처분", "share_cancel": "주식 소각", "rights_offering": "유상증자", "bonus_issue": "무상증자",
    "cb": "전환사채", "bw": "신주인수권부사채", "eb": "교환사채", "contract_cancel": "공급계약 해지",
    "supply_contract": "단일판매·공급계약", "prelim_earnings": "잠정실적", "earnings_change": "손익구조 변동",
    "dividend": "배당", "control_change": "최대주주 변경", "acquisition": "타법인 주식 취득",
    "divestiture": "타법인 주식 처분", "split": "회사 분할", "merger": "회사 합병", "capital_reduction": "감자",
    "capex": "유형자산 취득", "asset_sale": "유형자산 처분", "embezzlement": "횡령·배임", "lawsuit": "소송",
    "trading_halt": "거래정지", "tech_deal": "기술 도입·이전", "material_info": "주요 경영사항",
    "block_holding": "대량보유 보고", "insider_holding": "임원·주요주주 소유보고",
    # US
    "bankruptcy": "파산(1.03)", "restatement": "재무제표 신뢰불가(4.02)", "delisting_notice": "상장폐지 통보(3.01)",
    "acquisition_completed": "인수 완료(2.01)", "material_agreement": "중요 계약(1.01)",
    "agreement_terminated": "계약 종료(1.02)", "earnings": "실적 발표(2.02)", "restructuring": "구조조정(2.05)",
    "impairment": "손상차손(2.06)", "unregistered_equity": "사모 지분발행(3.02)", "auditor_change": "감사인 변경(4.01)",
    "exec_change": "임원 변동(5.02)", "shareholder_vote": "주총 결과(5.07)", "debt_obligation": "채무 발생(2.03)",
    "reg_fd": "공정공시(7.01)", "other_event": "기타 사건(8.01)", "other": "기타",
}


def label(typ: str) -> str:
    return LABELS.get(typ, typ)
