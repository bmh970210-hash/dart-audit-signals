"""OpenDART REST API integration. Never log or persist the API key."""
from __future__ import annotations

from io import BytesIO
from zipfile import ZipFile, BadZipFile
import xml.etree.ElementTree as ET
import time

import pandas as pd
import requests

BASE = "https://opendart.fss.or.kr/api"
CONNECT_TIMEOUT = 10
READ_TIMEOUT = 60  # corpCode.xml is a large ZIP and may take longer to download


class DartError(Exception):
    """An actionable DART API or network error."""


def _request(url: str, params: dict) -> requests.Response:
    """Request OpenDART and report safe diagnostics without exposing API keys.

    Timeout/transient server failures get one retry. Never include the underlying
    requests exception or full URL in UI messages: both may contain crtfc_key.
    """
    endpoint = "회사목록" if url.endswith("/corpCode.xml") else "재무제표"
    timeout = (CONNECT_TIMEOUT, READ_TIMEOUT if endpoint == "회사목록" else 30)
    for attempt in range(2):
        try:
            response = requests.get(url, params=params, timeout=timeout)
            response.raise_for_status()
            return response
        except requests.exceptions.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else None
            if status in (500, 502, 503, 504) and attempt == 0:
                time.sleep(0.5)
                continue
            if status == 403:
                detail = "접근 거부(HTTP 403). 클라우드 서버 IP의 접근 제한 가능성이 있습니다."
            elif status == 429:
                detail = "요청 제한(HTTP 429). 잠시 후 다시 시도하세요."
            else:
                detail = f"서버 응답 오류(HTTP {status or '알 수 없음'})."
            raise DartError(f"DART {endpoint} 요청 실패: {detail}") from None
        except requests.exceptions.SSLError:
            raise DartError(
                f"DART {endpoint} 연결 실패(SSL 인증서 오류). 서버 인증서 검증에 문제가 있습니다."
            ) from None
        except requests.exceptions.Timeout:
            if attempt == 0:
                time.sleep(0.5)
                continue
            raise DartError(
                f"DART {endpoint} 연결 시간초과(2회 시도). 클라우드 서버에서 DART에 접속하지 못하거나 응답이 지연됩니다."
            ) from None
        except requests.exceptions.ConnectionError:
            if attempt == 0:
                time.sleep(0.5)
                continue
            raise DartError(
                f"DART {endpoint} 연결 오류(2회 시도). 클라우드 호스팅 IP 또는 DART의 네트워크 연결이 제한되었을 수 있습니다."
            ) from None
        except requests.exceptions.RequestException:
            raise DartError(f"DART {endpoint} 네트워크 오류. 연결 상태를 확인하세요.") from None
    raise DartError(f"DART {endpoint} 요청에 실패했습니다.")


def _check_api_json(response: requests.Response) -> dict:
    try:
        body = response.json()
    except ValueError as exc:
        raise DartError("DART가 재무정보 JSON 대신 다른 형식의 응답을 보냈습니다. DART 접속 제한이나 시스템 점검 가능성이 있습니다.") from exc
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
        if response.content.lstrip().lower().startswith((b"<!doctype html", b"<html")):
            raise DartError("DART가 회사목록 ZIP 대신 HTML 오류 페이지를 보냈습니다. DART 서버에서 클라우드 접속을 제한했을 가능성이 있습니다.") from None
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