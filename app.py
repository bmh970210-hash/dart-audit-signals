"""DART Audit Signals — educational audit-planning assistant (MVP)."""
from __future__ import annotations

from datetime import datetime

import pandas as pd
import streamlit as st

from analysis import add_ratios, build_financials, detect_flags, flags_as_frame, numeric_summary
from dart_client import DartError, find_corporations, get_annual_statement, get_corporations, lookup_without_registry, get_company_profile
from sample_data import make_sample_annual

@st.cache_data(ttl=24 * 3600, show_spinner=False)
def cached_corporations(api_key: str) -> pd.DataFrame:
    return get_corporations(api_key)


@st.cache_data(ttl=60 * 60, show_spinner=False)
def cached_annual_statement(api_key: str, corp_code: str, year: int, fs_div: str):
    return get_annual_statement(api_key, corp_code, year, fs_div)


st.set_page_config(page_title="DART 감사 이상징후 탐지", page_icon="📊", layout="wide")

st.markdown("""<style>
.block-container {padding-top: 2rem; max-width: 1200px;}
[data-testid="stMetric"] {background-color: rgba(128,128,128,.065); padding: 14px; border-radius: 10px;}
</style>""", unsafe_allow_html=True)

st.title("📊 DART 회계 이상징후 탐지")
st.caption("재무제표 → 전년 대비 분석 → 사전 정의된 규칙 → 감사주장·추가 검토사항 | MVP v1.0")

with st.sidebar:
    st.header("분석 설정")
    mode = st.radio("데이터", ["가상 데이터 체험", "실제 DART 조회"], help="실제 조회에는 OpenDART 무료 인증키가 필요합니다.")
    n_years = st.select_slider("분석 기간", options=[3, 4, 5], value=5, format_func=lambda v: f"{v}개년")
    st.divider()
    st.info("징후는 감사절차 설계를 돕는 신호일 뿐, 분식회계나 오류가 확정됐다는 뜻이 아닙니다.")
    st.caption("데이터: 금융감독원 OpenDART / 가상 예시 별도 표기")

annual = {}
company = "가상 샘플제조(주)"
selection = "CFS"
warnings = []
real_mode = mode == "실제 DART 조회"

if not real_mode:
    st.warning("**가상 데이터 체험 모드**입니다. 실제 회사 자료나 DART 공시 수치가 아닙니다.")
    annual = make_sample_annual(n_years)
else:
    st.markdown("#### 1. 회사 검색 및 사업보고서 가져오기")
    api_key = st.text_input("OpenDART API 인증키", type="password", help="입력값은 화면에 표시되지 않고 코드·보고서에 저장되지 않습니다. GitHub에는 절대 올리지 마세요.")
    year = st.selectbox("마지막 사업연도", list(range(min(datetime.now().year - 1, 2025), 2014, -1)),
                        help="예: 2026년 중에는 보통 2025년 사업보고서가 가장 최근입니다.")
    selection_label = st.radio("재무제표", ["연결재무제표(CFS)", "별도재무제표(OFS)"], horizontal=True,
                               help="연도별 연결/별도 혼용을 금지합니다. 연결 자료가 없으면 별도로 재조회하세요.")
    selection = "CFS" if selection_label.startswith("연결") else "OFS"
    st.caption("삼성전자, 005930 또는 DART 고유번호 8자리를 입력하면 대용량 회사목록 다운로드를 생략합니다. 다른 회사명은 기존 검색을 사용합니다.")
    keyword = st.text_input("회사명 / 종목코드", placeholder="예: 삼성전자 또는 005930")
    companies = pd.DataFrame()
    direct_company = lookup_without_registry(keyword)
    if api_key and len(keyword.strip()) >= 2 and not direct_company:
        try:
            with st.spinner("금융감독원 회사목록 조회 중..."):
                companies = cached_corporations(api_key)
        except DartError as exc:
            st.error(str(exc))
    selected_code = None
    if direct_company:
        selected_code = direct_company["corp_code"]
        company = direct_company["corp_name"]
        st.success(f"회사 선택: {company} (DART 고유번호 {selected_code})")
    if not companies.empty:
        matches = find_corporations(companies, keyword)
        if matches.empty:
            st.warning("검색 결과가 없습니다. 정확한 회사명 또는 종목코드를 입력하세요.")
        else:
            options = list(matches.itertuples(index=False))
            selected = st.selectbox("회사 선택", options,
                format_func=lambda c: f"{c.corp_name} | 종목코드 {c.stock_code or '-'} | 고유번호 {c.corp_code}")
            selected_code = selected.corp_code
            company = selected.corp_name
    if st.button("🔎 재무정보 분석 실행", type="primary", disabled=not (bool(selected_code) and bool(api_key.strip()))):
        result = {}
        try:
            with st.spinner("회사정보 연결 확인 중..."):
                profile = get_company_profile(api_key, selected_code)
                company = profile.get("corp_name") or company
            with st.spinner("연도별 사업보고서에서 재무제표 수집 중..."):
                for y in range(year - n_years + 1, year + 1):
                    lines, receipt = cached_annual_statement(api_key, selected_code, y, selection)
                    if lines:
                        result[y] = (lines, receipt)
                    else:
                        warnings.append(f"{y}년 {selection} 사업보고서 재무정보 없음 (결측 처리)")
            st.session_state["analysis_result"] = {
                "company": company, "fs_div": selection, "year": year,
                "period": n_years, "annual": result, "warnings": warnings,
            }
        except DartError as exc:
            st.error(str(exc))
    saved = st.session_state.get("analysis_result", {})
    if saved:
        annual = saved["annual"]
        warnings = saved["warnings"]
        company = saved["company"]
        selection = saved["fs_div"]
    if not annual:
        st.info("인증키와 회사명을 입력하고, 검색 결과에서 회사를 고른 뒤 **재무정보 분석 실행**을 누르세요.")
        st.stop()

fin, origins = build_financials(annual)
if fin.empty:
    st.error("분석 가능한 재무정보가 없습니다. 연결/별도 구분, 연도 또는 회사를 바꾸어 보세요.")
    st.stop()

if warnings:
    for warning in warnings:
        st.warning(warning)

# Validate raw-currency consistency before comparing amounts.
if not origins.empty:
    units = set(origins["원본 통화"].dropna()) - {""}
    if len(units) > 1:
        st.error(f"원본 재무정보의 통화가 일치하지 않습니다: {', '.join(sorted(units))}. 비교를 중단합니다.")
        st.stop()

by_year = add_ratios(fin)
proxy_years = set(origins.loc[origins["기타채권 포함 대용치"] == "예", "연도"].tolist())
flags = detect_flags(by_year, proxy_years)
summary = numeric_summary(by_year)
flag_table = flags_as_frame(flags)

if real_mode:
    st.success(f"{company} | {selection} | 확인된 {len(fin)}개 연도 | DART 사업보고서 (11011)")
else:
    st.caption("사례는 재무이상징후를 학습하기 위해 임의로 설계한 가상 수치입니다.")

latest = by_year.iloc[-1]
cols = st.columns(4)
for col, key, label in zip(cols, ("revenue", "receivables", "inventory", "cfo"),
                           ("매출액", "매출채권", "재고자산", "영업현금흐름")):
    current = latest[key]
    growth = latest.get(f"{key}_yoy")
    col.metric(label, f"{current/1e8:,.1f}억원" if pd.notna(current) else "자료 없음",
               f"전년 대비 {float(growth):+.1f}%" if pd.notna(growth) else None,
               delta_color="off")

st.markdown("### 2. 재무정보 및 증감률")
st.dataframe(summary.style.format(precision=1, thousands=",", na_rep="-"), use_container_width=True, hide_index=True)
with st.expander("계정과목별 시계열 그래프", expanded=True):
    metric_columns = [c for c in ["매출액(억원)", "매출채권(억원)", "재고자산(억원)", "영업이익(억원)", "영업현금흐름(억원)"] if c in summary.columns]
    if metric_columns:
        metric = st.selectbox("그래프로 볼 계정과목", metric_columns)
        chart = summary[["연도", metric]].dropna(subset=[metric]).copy()
        chart["연도"] = chart["연도"].astype(str)
        if not chart.empty:
            st.line_chart(chart.set_index("연도")[[metric]], use_container_width=True)
            st.dataframe(chart.style.format({metric: "{:,.1f}"}), use_container_width=True, hide_index=True)
        else:
            st.info("선택한 계정과목의 금액이 없습니다.")

st.markdown("### 3. 이상징후와 감사 대응")
if flag_table.empty:
    st.info("사전 설정한 규칙에 해당하는 이상징후가 발견되지 않았습니다. 이는 재무정보가 정상임을 보증하지 않습니다.")
else:
    st.warning(f"총 {len(flag_table)}건의 규칙 기반 이상징후가 발견됐습니다. (감사의견이나 부정 판정 아님)")
    st.dataframe(flag_table[["연도", "등급", "이상징후", "탐지 근거", "연결 감사주장", "추가 검토사항"]],
                 use_container_width=True, hide_index=True)
    with st.expander("탐지 규칙 상세"):
        st.dataframe(flag_table[["연도", "이상징후", "규칙"]], hide_index=True, use_container_width=True)

st.markdown("### 4. 근거 자료와 결과 내려받기")
with st.expander("DART 원본 계정 매핑 확인", expanded=False):
    st.dataframe(origins, hide_index=True, use_container_width=True)
    if real_mode:
        seen = fin[["year", "receipt"]].drop_duplicates()
        for _, item in seen.iterrows():
            if str(item["receipt"]).isdigit() and len(str(item["receipt"])) == 14:
                st.markdown(f"[{int(item['year'])}년 사업보고서 원문](https://dart.fss.or.kr/dsaf001/main.do?rcpNo={item['receipt']})")

st.download_button("📥 5개년 재무지표 CSV", data=summary.to_csv(index=False).encode("utf-8-sig"),
                   file_name="financial_summary.csv", mime="text/csv")
if not flag_table.empty:
    st.download_button("📥 감사 이상징후 CSV", data=flag_table.to_csv(index=False).encode("utf-8-sig"),
                       file_name="audit_flags.csv", mime="text/csv")
st.download_button("📥 계정 출처 및 매핑 CSV", data=origins.to_csv(index=False).encode("utf-8-sig"),
                   file_name="source_mapping.csv", mime="text/csv")

with st.expander("해석 시 주의사항 (중요)"):
    st.markdown("""
- **규칙 기반 사전 검토**로 감사위험을 확정하거나 회계부정을 판정하지 않습니다.
- 연도별 **동일한 연결 또는 별도** 재무제표만 사용하며, 누락 연도는 0으로 채우지 않습니다.
- 기말 **매출채권 ÷ 연간 매출 × 365**는 단순 대용지표이며, 기초·기말 평균채권으로 계산하는 정식 DSO가 아닙니다.
- 매출채권 계정을 찾지 못하고 **매출채권 및 기타유동채권**만 찾았을 때는 이를 대용치로 표시합니다.
- 재무제표의 표시, 사업구조, 회계정책, 결산기 변동에 따라 단순 비교가 왜곡될 수 있습니다.
- 산업별 기준, 연도별 보고서 정정, XBRL 주석 및 연결 범위 변경은 후속 검증 대상입니다.
- DART 조회는 정기 사업보고서의 현재 연도 재무제표 데이터 사용. 다른 보고서의 비교표시 금액으로 소급 수정하지 않습니다.
- 확인되지 않은 계정은 **누락** 처리하고 계산하지 않습니다. 모든 금액은 억원 단위로 표시합니다(원본 통화는 자료 근거에서 확인).
""")

st.caption(f"생성: {datetime.now():%Y-%m-%d} | 출처: {'금융감독원 OpenDART API' if real_mode else '프로젝트용 가상 데이터'} | Audit Signals MVP")