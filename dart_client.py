"""OpenDART REST API integration. Never log or persist the API key."""
from __future__ import annotations

from io import BytesIO
from zipfile import ZipFile, BadZipFile
import xml.etree.ElementTree as ET

import pandas as pd
import requests

BASE = "https://opendart.fss.or.kr/api"
TIMEOUT = 35


class DartError(Exception):
    """An actionable DART API or network error."""


def _request(url: str, params: dict) -> requests.Response:
    try:
        result = requests.get(url, params=params, timeout=TIMEOUT)
        result.raise_for_status()
        return result
    except requests.RequestException as exc:
        raise DartError("DART 연결에 실패했습니다. 인터넷 연결 또는 DART 서버 상태를 확인하세요.") from exc


def _check_api_json(response: requests.Response) -> dict:
    try:
        body = response.json()
    except ValueError as exc:
        raise DartError("DART가 JSON이 아닌 응답을 보냈습니다.") from exc
    status = body.get("status")
    if status not in ("000", "013"):
        message = body.get("message") or "알 수 없는 오류"
        raise DartError(f"DART 오류 {status}: {message}")
    return body


def get_corporations(api_key: str) -> pd.DataFrame:
    """Fetch the full corporation-code ZIP and parse the bundled CORPCODE.xml."""
    response = _request(f"{BASE}/corpCode.xml", {"crtfc_key": api_key})
    try:
        with ZipFile(BytesIO(response.content)) as archive:
            xml_name = next((n for n in archive.namelist() if n.lower().endswith(".xml")), None)
            if not xml_name:
                raise DartError("회사목록 ZIP에 XML 파일이 없습니다.")
            # Parse while streaming to avoid retaining tens of thousands of XML nodes.
            records = []
            with archive.open(xml_name) as xml_file:
                for _event, elem in ET.iterparse(xml_file, events=("end",)):
                    if elem.tag == "list":
                        item = {c.tag: (c.text or "").strip() for c in elem}
                        if item.get("corp_code") and item.get("corp_name"):
                            records.append({
                                "corp_code": item["corp_code"],
                                "corp_name": item["corp_name"],
                                "stock_code": item.get("stock_code", ""),
                            })
                        elem.clear()
        return pd.DataFrame(records)
    except (BadZipFile, ET.ParseError) as exc:
        # Invalid keys often return an error XML in place of a ZIP file.
        try:
            error_root = ET.fromstring(response.content)
            status = error_root.findtext("status")
            message = error_root.findtext("message")
        except ET.ParseError:
            status = None
            message = None
        if status:
            raise DartError(f"DART 오류 {status}: {message or '회사목록을 받지 못했습니다.'}") from exc
        raise DartError("DART 회사목록 ZIP을 해석하지 못했습니다.") from exc


def find_corporations(corporations: pd.DataFrame, query: str, limit: int = 40) -> pd.DataFrame:
    query = query.strip()
    if len(query) < 2:
        return corporations.iloc[0:0].copy()
    match = (corporations["corp_name"].str.contains(query, case=False, regex=False, na=False)
             | corporations["stock_code"].eq(query))
    result = corporations.loc[match].copy()
    result["_exact"] = result["corp_name"].str.lower().eq(query.lower()) | result["stock_code"].eq(query)
    result["_listed"] = result["stock_code"].str.len().eq(6)
    return result.sort_values(["_exact", "_listed", "corp_name"], ascending=[False, False, True]).head(limit).drop(columns=["_exact", "_listed"])


def get_annual_statement(api_key: str, corp_code: str, year: int, fs_div: str = "CFS") -> tuple[list[dict], str | None]:
    """Current-term values only, from the annual report of the requested year."""
    data = _check_api_json(_request(f"{BASE}/fnlttSinglAcntAll.json", {
        "crtfc_key": api_key,
        "corp_code": corp_code,
        "bsns_year": str(year),
        "reprt_code": "11011",
        "fs_div": fs_div,
    }))
    if data["status"] == "013":
        return [], None
    lines = data.get("list") or []
    rcept_no = next((item.get("rcept_no") for item in lines if item.get("rcept_no")), None)
    return lines, rcept_no