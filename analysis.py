"""Deterministic red flags, not an opinion or an audit conclusion."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import math
import re
from typing import Any

import pandas as pd

# Matching by standard XBRL account ID first; exact Korean labels only as fallback.
# AR fallback 'trade and other' is explicitly identified as a proxy.
METRICS = {
    "revenue": {
        "label": "매출액",
        "sections": ("IS", "CIS"),
        "ids": ["ifrs-full_Revenue", "ifrs_Revenue", "ifrs-full_RevenueFromContractsWithCustomers",
                "ifrs_RevenueFromContractsWithCustomers", "ifrs-full_RevenueFromContractsWithCustomersExcludingAssessedTax"],
        "names": ["매출액", "수익(매출액)", "영업수익", "매출", "수익"],
    },
    "receivables": {
        "label": "매출채권",
        "sections": ("BS",),
        "ids": ["dart_ShortTermTradeReceivable", "ifrs-full_TradeReceivables", "ifrs_TradeReceivables",
                "ifrs-full_CurrentTradeReceivables", "ifrs_CurrentTradeReceivables"],
        "names": ["매출채권", "단기매출채권"],
        "proxy_ids": ["ifrs-full_TradeAndOtherCurrentReceivables", "ifrs_TradeAndOtherCurrentReceivables"],
        "proxy_names": ["매출채권 및 기타채권", "매출채권및기타채권", "매출채권 및 기타유동채권",
                        "매출채권및기타유동채권", "매출채권 및 기타수취채권"],
    },
    "inventory": {
        "label": "재고자산",
        "sections": ("BS",),
        "ids": ["ifrs-full_Inventories", "ifrs_Inventories"],
        "names": ["재고자산"],
    },
    "cfo": {
        "label": "영업활동현금흐름",
        "sections": ("CF",),
        "ids": ["ifrs-full_CashFlowsFromUsedInOperatingActivities", "ifrs_CashFlowsFromUsedInOperatingActivities"],
        "names": ["영업활동현금흐름", "영업활동으로 인한 현금흐름", "영업활동으로인한현금흐름",
                  "영업활동으로 인한 순현금흐름", "영업활동현금흐름(순액)"],
    },
    "operating_income": {
        "label": "영업이익",
        "sections": ("IS", "CIS"),
        "ids": ["dart_OperatingIncomeLoss", "ifrs-full_ProfitLossFromOperatingActivities"],
        "names": ["영업이익", "영업이익(손실)", "영업손익"],
    },
}


@dataclass
class Flag:
    year: int
    severity: str
    title: str
    evidence: str
    assertions: str
    procedures: str
    rule: str


def parse_amount(value: Any) -> float | None:
    """Parse KR DART numeric format, retaining negative figures and 0."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value) if math.isfinite(value) else None
    s = str(value).strip().replace(",", "").replace(" ", "")
    if s in ("", "-", "—", "N/A", "null", "None"):
        return None
    if s.startswith("(") and s.endswith(")"):
        s = "-" + s[1:-1]
    if not re.fullmatch(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)", s):
        return None
    return float(s)


def _clean_name(s: str) -> str:
    return re.sub(r"\s+", "", str(s or "")).strip()


def extract_metric(lines: list[dict], metric: str) -> dict:
    conf = METRICS[metric]
    candidates = []
    for item in lines:
        if item.get("sj_div") not in conf["sections"]:
            continue
        amount = parse_amount(item.get("thstrm_amount"))
        if amount is None:
            continue
        account_id = (item.get("account_id") or "").strip()
        name = (item.get("account_nm") or "").strip()
        clean = _clean_name(name)
        score = None
        proxy = False
        if account_id in conf["ids"]:
            score = conf["ids"].index(account_id)
        elif clean in [_clean_name(x) for x in conf["names"]]:
            score = 100 + [_clean_name(x) for x in conf["names"]].index(clean)
        elif metric == "receivables" and account_id in conf.get("proxy_ids", []):
            score = 200 + conf["proxy_ids"].index(account_id)
            proxy = True
        elif metric == "receivables" and clean in [_clean_name(x) for x in conf.get("proxy_names", [])]:
            score = 300 + [_clean_name(x) for x in conf["proxy_names"]].index(clean)
            proxy = True
        if score is not None:
            candidates.append((score, amount, item, proxy))
    if not candidates:
        return {"value": None, "account": "미확인", "account_id": "", "currency": "", "proxy": False, "ambiguous": False}
    best_score = min(v[0] for v in candidates)
    best = [x for x in candidates if x[0] == best_score]
    distinct = {x[1] for x in best}
    if len(distinct) > 1:
        return {"value": None, "account": "중복 계정(값 불일치)", "account_id": "", "currency": "", "proxy": False, "ambiguous": True}
    _score, value, item, proxy = best[0]
    return {"value": value, "account": item.get("account_nm", ""),
            "account_id": item.get("account_id", ""), "currency": item.get("currency", ""),
            "proxy": proxy, "ambiguous": False}


def build_financials(annual: dict[int, tuple[list[dict], str | None]]) -> tuple[pd.DataFrame, pd.DataFrame]:
    records, origins = [], []
    for year, (lines, receipt) in sorted(annual.items()):
        if not lines:
            continue
        row: dict[str, Any] = {"year": int(year), "receipt": receipt or ""}
        for metric, conf in METRICS.items():
            chosen = extract_metric(lines, metric)
            row[metric] = chosen["value"]
            origins.append({"연도": int(year), "지표": conf["label"], "원본 계정명": chosen["account"],
                            "표준계정ID": chosen["account_id"], "원본 통화": chosen["currency"],
                            "기타채권 포함 대용치": "예" if chosen["proxy"] else "아니오",
                            "중복/충돌": "예" if chosen["ambiguous"] else "아니오",
                            "금액(원본 단위)": chosen["value"], "접수번호": receipt or ""})
        records.append(row)
    return pd.DataFrame(records), pd.DataFrame(origins)


def _rate(previous: Any, current: Any) -> float | None:
    # A non-positive base is inappropriate for a conventional percentage increase.
    if pd.isna(previous) or pd.isna(current) or previous <= 0:
        return None
    return 100.0 * (current / previous - 1)


def add_ratios(fin: pd.DataFrame) -> pd.DataFrame:
    if fin.empty:
        return fin.copy()
    df = fin.copy().sort_values("year").reset_index(drop=True)
    for metric in METRICS:
        df[f"{metric}_yoy"] = pd.NA
    df["dso_days"] = df.apply(
        lambda r: 365 * r["receivables"] / r["revenue"]
        if pd.notna(r["receivables"]) and pd.notna(r["revenue"]) and r["revenue"] > 0 else None, axis=1)
    df["inventory_to_revenue"] = df.apply(
        lambda r: 100 * r["inventory"] / r["revenue"]
        if pd.notna(r["inventory"]) and pd.notna(r["revenue"]) and r["revenue"] > 0 else None, axis=1)
    for i in range(1, len(df)):
        # Gaps in annual reports are not comparable YoY.
        if int(df.loc[i, "year"]) != int(df.loc[i - 1, "year"]) + 1:
            continue
        for metric in METRICS:
            df.loc[i, f"{metric}_yoy"] = _rate(df.loc[i - 1, metric], df.loc[i, metric])
    return df


def detect_flags(df: pd.DataFrame, proxy_years: set[int] | None = None) -> list[Flag]:
    flags: list[Flag] = []
    if df.empty:
        return flags
    proxy_years = proxy_years or set()
    prev_by_year = {int(row["year"]): row for _, row in df.iterrows()}
    for _, row in df.iterrows():
        year = int(row["year"])
        prev = prev_by_year.get(year - 1)
        rev_g = row.get("revenue_yoy")
        ar_g = row.get("receivables_yoy")
        inv_g = row.get("inventory_yoy")
        cfo = row.get("cfo")
        op = row.get("operating_income")
        rev = row.get("revenue")
        suffix = " (매출채권 및 기타유동채권 대용치 포함: 정확한 매출채권 확인 필요)" if year in proxy_years else ""
        if pd.notna(rev_g) and rev_g >= 30:
            flags.append(Flag(year, "관찰", "매출 증가율 급등", f"매출 전년 대비 {rev_g:.1f}% 증가",
                              "발생사실, 기간귀속", "기말 전후 매출거래 표본, 계약서, 출고·인도증빙, 매출취소 및 반품 확인", "R01: 매출 YoY ≥ 30%"))
        if pd.notna(ar_g) and pd.notna(rev_g) and ar_g - rev_g >= 20:
            flags.append(Flag(year, "주의", "매출채권 증가가 매출 증가를 초과",
                              f"채권 증가율 {ar_g:.1f}% vs 매출 증가율 {rev_g:.1f}% (차이 {ar_g - rev_g:.1f}%p){suffix}",
                              "실재성, 평가, 기간귀속", "채권 연령분석, 후속 현금회수, 거래처 조회, 기말 매출 Cut-off 및 대손충당금 가정 확인",
                              "R02: 채권 YoY − 매출 YoY ≥ 20%p"))
        if prev is not None and pd.notna(row.get("dso_days")) and pd.notna(prev.get("dso_days")):
            delta = row["dso_days"] - prev["dso_days"]
            if delta >= 20:
                flags.append(Flag(year, "주의", "채권 회수기간 대용지표 증가",
                                  f"매출채권÷연매출×365: {prev['dso_days']:.1f}일 → {row['dso_days']:.1f}일 (+{delta:.1f}일){suffix}",
                                  "실재성, 평가", "후속 입금내역 확인, 장기미회수채권 분석, 회수가능성 및 대손충당금 검토",
                                  "R03: 채권÷매출×365 연간 증가 ≥ 20일 (정식 DSO 아님)"))
        if pd.notna(inv_g) and pd.notna(rev_g) and inv_g - rev_g >= 20:
            flags.append(Flag(year, "주의", "재고 증가가 매출 증가를 초과",
                              f"재고 증가율 {inv_g:.1f}% vs 매출 증가율 {rev_g:.1f}% (차이 {inv_g - rev_g:.1f}%p)",
                              "실재성, 평가", "재고실사 결과, 장기체화 재고, 순실현가능가치, 재고수불부 및 기말 입출고 검토",
                              "R04: 재고 YoY − 매출 YoY ≥ 20%p"))
        if pd.notna(cfo) and cfo < 0 and pd.notna(op) and op > 0:
            flags.append(Flag(year, "주의", "영업이익 흑자이나 영업현금흐름 음수",
                              f"영업이익 {op / 1e8:,.1f}억원, 영업현금흐름 {cfo / 1e8:,.1f}억원",
                              "매출 발생사실, 기간귀속 및 채권 평가 검토 단서", "영업현금흐름 조정표, 운전자본 변동, 기말 매출 실재성 및 후속 회수 검토",
                              "R05: 영업이익 > 0 & 영업현금흐름 < 0"))
        if prev is not None and pd.notna(rev_g) and rev_g > 0 and pd.notna(cfo) and pd.notna(prev.get("cfo")):
            if prev["cfo"] > 0 and cfo < 0:
                flags.append(Flag(year, "관찰", "매출 증가 속 영업현금흐름 적자 전환",
                                  f"매출 +{rev_g:.1f}%, 영업현금흐름 양(+) → 음(-) 전환",
                                  "매출 발생사실, 채권 평가 검토 단서", "현금흐름표 운전자본 조정내역, 매출채권 회수현황, 비경상 항목 확인",
                                  "R06: 매출 증가 & CFO 양수→음수"))
    return flags


def flags_as_frame(flags: list[Flag]) -> pd.DataFrame:
    return pd.DataFrame([{"연도": x.year, "등급": x.severity, "이상징후": x.title,
                          "탐지 근거": x.evidence, "연결 감사주장": x.assertions,
                          "추가 검토사항": x.procedures, "규칙": x.rule} for x in flags])


def numeric_summary(df: pd.DataFrame) -> pd.DataFrame:
    labels = {"year": "연도", "revenue": "매출액(억원)", "receivables": "매출채권(억원)",
              "inventory": "재고자산(억원)", "cfo": "영업현금흐름(억원)",
              "operating_income": "영업이익(억원)", "revenue_yoy": "매출 증감률(%)",
              "receivables_yoy": "매출채권 증감률(%)", "inventory_yoy": "재고 증감률(%)",
              "cfo_yoy": "영업현금흐름 증감률(%)", "dso_days": "채권÷매출×365(일)",
              "inventory_to_revenue": "재고/매출(%)"}
    result = df[[k for k in labels if k in df.columns]].copy()
    for k in ("revenue", "receivables", "inventory", "cfo", "operating_income"):
        if k in result:
            result[k] = result[k] / 1e8
    for k in list(result.columns):
        if k != "year":
            result[k] = pd.to_numeric(result[k], errors="coerce").round(2)
    return result.rename(columns=labels)