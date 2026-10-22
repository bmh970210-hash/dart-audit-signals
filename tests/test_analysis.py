import pandas as pd

from analysis import add_ratios, build_financials, detect_flags, extract_metric, parse_amount
from dart_client import find_corporations
from sample_data import make_sample_annual


def test_negative_and_missing():
    assert parse_amount("(1,234)") == -1234
    assert parse_amount("-23,000") == -23000
    assert parse_amount("-") is None
    assert parse_amount("0") == 0


def test_sample_has_expected_flags():
    fin, mapping = build_financials(make_sample_annual())
    assert fin.shape[0] == 5
    assert fin.loc[fin.year == 2025, "cfo"].iloc[0] == -2.5e9
    flags = detect_flags(add_ratios(fin))
    names = {f.title for f in flags if f.year == 2025}
    assert "매출채권 증가가 매출 증가를 초과" in names
    assert "재고 증가가 매출 증가를 초과" in names
    assert "영업이익 흑자이나 영업현금흐름 음수" in names
    assert len(mapping) == 25


def test_no_fill_missing_accounts():
    lines = [{"sj_div": "IS", "account_id": "ifrs-full_Revenue", "account_nm": "매출액", "thstrm_amount": "1000"}]
    assert extract_metric(lines, "inventory")["value"] is None
    assert extract_metric(lines, "revenue")["value"] == 1000


def test_proxy_receivables_labeled():
    lines = [{"sj_div": "BS", "account_id": "ifrs-full_TradeAndOtherCurrentReceivables",
              "account_nm": "매출채권 및 기타유동채권", "thstrm_amount": "1,200"}]
    result = extract_metric(lines, "receivables")
    assert result["proxy"] is True
    assert result["value"] == 1200


def test_gap_years_have_no_yoy():
    annual = make_sample_annual()
    annual.pop(2024)
    fin, _ = build_financials(annual)
    ratios = add_ratios(fin)
    assert pd.isna(ratios.loc[ratios.year == 2025, "revenue_yoy"].iloc[0])


def test_ambiguous_duplicate_refused():
    lines = [
        {"sj_div": "IS", "account_id": "ifrs-full_Revenue", "account_nm": "매출액", "thstrm_amount": "200"},
        {"sj_div": "CIS", "account_id": "ifrs-full_Revenue", "account_nm": "매출액", "thstrm_amount": "300"},
    ]
    assert extract_metric(lines, "revenue")["ambiguous"] is True
    assert extract_metric(lines, "revenue")["value"] is None


def test_stock_search():
    companies = pd.DataFrame([{"corp_name": "삼성전자", "stock_code": "005930", "corp_code": "00126380"},
                              {"corp_name": "다른회사", "stock_code": "", "corp_code": "00123456"}])
    assert len(find_corporations(companies, "005930")) == 1


def test_corp_code_zip_parsing(monkeypatch):
    from io import BytesIO
    import zipfile
    from dart_client import get_corporations

    content = b'''<?xml version="1.0" encoding="utf-8"?><result>
        <list><corp_code>00126380</corp_code><corp_name>Sample Electronics</corp_name>
        <stock_code>005930</stock_code></list>
        <list><corp_code>00998877</corp_code><corp_name>Small Test</corp_name>
        <stock_code></stock_code></list></result>'''
    out = BytesIO()
    with zipfile.ZipFile(out, 'w') as file:
        file.writestr('CORPCODE.xml', content)

    class FakeResponse:
        content = out.getvalue()
        def raise_for_status(self):
            pass

    monkeypatch.setattr('dart_client.requests.get', lambda *args, **kwargs: FakeResponse())
    found = get_corporations('test-key')
    assert found.shape[0] == 2
    assert found.loc[0, 'corp_code'] == '00126380'
    assert found.loc[0, 'stock_code'] == '005930'


def test_annual_endpoint_params_and_receipt(monkeypatch):
    from dart_client import get_annual_statement
    called = {}
    class FakeResponse:
        def raise_for_status(self):
            pass
        def json(self):
            return {'status': '000', 'list': [
                {'sj_div': 'IS', 'account_id': 'ifrs-full_Revenue', 'thstrm_amount': '150000000',
                 'rcept_no': '20260301000001'}]}

    def request(url, params=None, **kwargs):
        called['url'] = url
        called['params'] = params
        return FakeResponse()

    monkeypatch.setattr('dart_client.requests.get', request)
    lines, receipt = get_annual_statement('secret', '00126380', 2025, 'CFS')
    assert called['url'].endswith('fnlttSinglAcntAll.json')
    assert called['params']['reprt_code'] == '11011'
    assert called['params']['bsns_year'] == '2025'
    assert called['params']['fs_div'] == 'CFS'
    assert receipt == '20260301000001'
    assert len(lines) == 1

def test_cloud_connection_error_is_sanitized(monkeypatch):
    import requests
    from dart_client import DartError, get_corporations
    from unittest.mock import patch
    import pytest

    def broken_request(url, params=None, **kwargs):
        # requests may embed the key in its raw exception: never show it.
        raise requests.ConnectionError(f"https://opendart.fss.or.kr/api/corpCode.xml?crtfc_key={params['crtfc_key']}")

    monkeypatch.setattr('dart_client.requests.get', broken_request)
    with patch('dart_client.time.sleep') as sleep:
        with pytest.raises(DartError, match="연결 오류") as err:
            get_corporations('very-private-secret')
    assert 'very-private-secret' not in str(err.value)
    assert sleep.call_count == 1


def test_cloud_access_forbidden_is_specific_and_no_retry(monkeypatch):
    import requests
    from dart_client import DartError, get_corporations
    import pytest

    calls = []
    def forbidden_request(url, params=None, **kwargs):
        calls.append(url)
        response = requests.Response()
        response.status_code = 403
        response.url = url + '?crtfc_key=very-private-secret'
        return response

    monkeypatch.setattr('dart_client.requests.get', forbidden_request)
    with pytest.raises(DartError, match="HTTP 403") as err:
        get_corporations('very-private-secret')
    assert 'very-private-secret' not in str(err.value)
    assert len(calls) == 1


def test_cloud_timeout_retries_once(monkeypatch):
    import requests
    from dart_client import DartError, get_corporations
    from unittest.mock import patch
    import pytest

    calls = []
    def timeouting_request(*args, **kwargs):
        calls.append(1)
        raise requests.Timeout("timeout after key=very-private-secret")

    monkeypatch.setattr('dart_client.requests.get', timeouting_request)
    with patch('dart_client.time.sleep'):
        with pytest.raises(DartError, match="시간초과") as err:
            get_corporations('very-private-secret')
    assert 'very-private-secret' not in str(err.value)
    assert len(calls) == 2
