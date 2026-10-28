"""DART Audit Signals — educational audit-planning assistant (MVP)."""
from __future__ import annotations

from datetime import datetime

import pandas as pd
import altair as alt
import streamlit as st

from analysis import add_ratios, build_financials, detect_flags, flags_as_frame, numeric_summary
from dart_client import DartError, find_corporations, get_annual_statement, get_corporations, lookup_without_registry
from sample_data import make_sample_annual

@st.cache_data(ttl=24 * 3600, show_spinner=False)
def cached_corporations(api_key: str) -> pd.DataFrame:
    return get_corporations(api_key)


@st.cache_data(ttl=60 * 60, show_spinner=False)
def cached_annual_statement(api_key: str, corp_code: str, year: int, fs_div: str):
    return get_annual_statement(api_key, corp_code, year, fs_div)


st.set_page_config(page_title="DART 감사 이상징후 탐지", page_icon="📊", layout="wide")

st.markdown("""<style>
@import url('https://fonts.googleapis.com/css2?family=Pretendard:wght@400;500;600;700;800&display=swap');
html, body, [class*="css"], [data-testid="stApp"], button, input, select, textarea {
  font-family: 'Pretendard', 'Noto Sans KR', -apple-system, BlinkMacSystemFont, sans-serif !important;
}
.block-container {padding-top: 2rem; max-width: 1200px;}
h1, h2, h3 {letter-spacing: -0.035em; font-weight: 750;}
[data-testid="stMetric"] {
  background: linear-gradient(135deg, #7C3AED 0%, #A855F7 58%, #C084FC 100%);
  border: 1px solid rgba(255,255,255,.18);
  padding: 20px 19px;
  border-radius: 17px;
  box-shadow: 0 8px 22px rgba(124,58,237,.22);
  min-height: 135px;
}
[data-testid="stMetric"] [data-testid="stMetricLabel"],
[data-testid="stMetric"] [data-testid="stMetricValue"],
[data-testid="stMetric"] [data-testid="stMetricDelta"],
[data-testid="stMetric"] [data-testid="stMetricLabel"] *,
[data-testid="stMetric"] [data-testid="stMetricValue"] *,
[data-testid="stMetric"] [data-testid="stMetricDelta"] * {
  color: #ffffff !important;
}
[data-testid="stMetric"] [data-testid="stMetricLabel"] {opacity: .85; font-weight: 600;}
[data-testid="stMetric"] [data-testid="stMetricValue"] {font-weight: 800; letter-spacing: -.035em;}
[data-testid="stMetric"] [data-testid="stMetricDelta"] {opacity: .9;}
[data-testid="stMetric"] svg {fill: #ffffff !important;}
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
    if not direct_company and len(keyword.strip()) >= 2:
        st.caption("일반 회사명은 회사목록 조회 버튼을 눌러 검색합니다. 삼성전자 또는 DART 고유번호 8자리는 바로 선택됩니다.")
        if st.button("회사목록 검색", key="corp_search"):
            if not api_key.strip():
                st.warning("OpenDART 인증키를 먼저 입력하세요.")
            else:
                try:
                    with st.spinner("DART 회사목록 다운로드 중 (최대 약 2분)..."):
                        st.session_state["corp_registry"] = cached_corporations(api_key.strip())
                except DartError as exc:
                    st.error(str(exc))
        companies = st.session_state.get("corp_registry", pd.DataFrame())
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
            # Company profile is optional: a valid DART corp_code is enough to fetch statements.
            # Avoid blocking the entire analysis on an extra network request.
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
        st.caption(f"현재 표시 중인 분석: {saved['company']} / {saved['year']}년까지 {saved['period']}개년 / {saved['fs_div']}. 설정을 변경했다면 분석 실행을 다시 누르세요.")
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
    col.metric(label, f"{current/1e8:,.0f}억원" if pd.notna(current) else "자료 없음",
               f"전년 대비 {float(growth):+.1f}%" if pd.notna(growth) else None,
               delta_color="off")

st.markdown("### 2. 재무정보 및 증감률")
amount_cols = [c for c in summary.columns if c.endswith("(억원)")]
ratio_cols = [c for c in summary.columns if c not in amount_cols and c != "연도"]
st.markdown("#### 재무금액 (단위: 억원)")
st.dataframe(summary[["연도"] + amount_cols].style.format(precision=1, thousands=",", na_rep="-"), use_container_width=True, hide_index=True)

st.markdown("#### 계정과목별 시계열 추세")
if amount_cols:
    metric = st.selectbox("분석할 계정과목", amount_cols, key="trend_metric")
    chart = summary[["연도", metric]].dropna(subset=[metric]).copy()
    chart["연도"] = chart["연도"].astype(str)
    if not chart.empty:
        lo, hi = float(chart[metric].min()), float(chart[metric].max())
        span = hi - lo
        pad = span * 0.15 if span > 0 else max(abs(hi) * 0.05, 1.0)
        ymin, ymax = lo - pad, hi + pad
        plot = alt.Chart(chart).mark_line(point=True, strokeWidth=3).encode(
            x=alt.X("연도:N", sort=list(chart["연도"]), title="사업연도"),
            y=alt.Y(f"{metric}:Q", scale=alt.Scale(domain=[ymin, ymax], zero=False),
                    axis=alt.Axis(format=",.1f"), title=metric),
            tooltip=[alt.Tooltip("연도:N"), alt.Tooltip(f"{metric}:Q", format=",.1f")]
        ).properties(height=320)
        st.altair_chart(plot, use_container_width=True)
        st.caption(f"세로축은 실제 관측값 {lo:,.1f}~{hi:,.1f}억원에 맞춰 자동 조정됩니다. 축이 0에서 시작하지 않을 수 있으므로 변동폭은 수치와 함께 판단하세요.")
        st.dataframe(chart.style.format({metric: "{:,.1f}"}), use_container_width=True, hide_index=True)
    else:
        st.info("해당 계정과목에 표시할 금액이 없습니다.")

st.markdown("#### 증감률과 주요 비율")
st.caption("증감률은 직전 사업연도와 비교합니다. 비교할 자료가 없거나 기준 금액이 0 이하이면 표시하지 않습니다.")
ratio_guide = {
    "매출 증감률(%)": ("매출액의 전년 대비 변화", "급증 시 기말 매출의 발생사실과 기간귀속, 급감 시 사업환경 변화를 확인"),
    "매출채권 증감률(%)": ("외상매출금 등 매출채권의 전년 대비 변화", "매출보다 빠르게 늘면 회수가능성, 대손충당금, 매출 실재성을 확인"),
    "재고 증감률(%)": ("재고자산의 전년 대비 변화", "매출보다 빠르게 늘면 체화재고, 평가손실, 실재성을 확인"),
    "영업현금흐름 증감률(%)": ("영업활동 현금흐름의 전년 대비 변화", "급감하거나 음수 전환 시 이익의 현금전환과 운전자본 변동을 확인"),
    "채권÷매출×365(일)": ("기말 매출채권을 연매출로 나눈 회수기간 대용지표", "기간이 길어지면 회수 지연과 채권 평가 위험을 확인. 정식 DSO와 다름"),
    "재고/매출(%)": ("매출 대비 기말 재고자산의 비중", "비중이 높아지면 판매 부진, 재고 체화, 순실현가능가치 하락을 확인"),
}
with st.expander("비율의 의미와 변동 시 감사 검토사항", expanded=True):
    for label in ratio_cols:
        if label in ratio_guide:
            meaning, risk = ratio_guide[label]
            st.markdown(f"**{label}** — {meaning}  \n↳ **확인할 사항:** {risk}")
if ratio_cols:
    st.dataframe(summary[["연도"] + ratio_cols].style.format(precision=1, thousands=",", na_rep="-"), use_container_width=True, hide_index=True)
    ratio_metric = st.selectbox("추세로 볼 증감률 또는 비율", ratio_cols, key="ratio_metric")
    ratio_chart = summary[["연도", ratio_metric]].dropna(subset=[ratio_metric]).copy()
    ratio_chart["연도"] = ratio_chart["연도"].astype(str)
    if not ratio_chart.empty:
        low, high = float(ratio_chart[ratio_metric].min()), float(ratio_chart[ratio_metric].max())
        margin = (high - low) * 0.15 if high > low else max(abs(high) * 0.05, 1.0)
        ratio_plot = alt.Chart(ratio_chart).mark_line(point=True, strokeWidth=3).encode(
            x=alt.X("연도:N", sort=list(ratio_chart["연도"]), title="사업연도"),
            y=alt.Y(f"{ratio_metric}:Q", scale=alt.Scale(domain=[low-margin, high+margin], zero=False),
                    axis=alt.Axis(format=",.1f"), title=ratio_metric),
            tooltip=[alt.Tooltip("연도:N"), alt.Tooltip(f"{ratio_metric}:Q", format=",.1f")]
        ).properties(height=260)
        st.altair_chart(ratio_plot, use_container_width=True)

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

st.download_button("📥 재무지표 CSV", data=summary.to_csv(index=False).encode("utf-8-sig"),
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