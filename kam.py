"""Fetch DART audit report excerpts; never fabricate KAM."""
from io import BytesIO
from zipfile import ZipFile
import re
import requests
from bs4 import BeautifulSoup

def fetch_kam(key, corp, year):
    try:
        params = {"crtfc_key":key,"corp_code":corp,"bgn_de":str(year+1)+"0101","end_de":str(year+1)+"1231","page_count":100}
        data=requests.get("https://opendart.fss.or.kr/api/list.json",params=params,timeout=15).json()
        reports=[r for r in data.get("list",[]) if "감사보고서" in r.get("report_nm","")]
        if not reports:
            return {"year":year,"status":"별도 감사보고서 공시를 찾지 못했습니다."}
        reports.sort(key=lambda r:("연결" in r.get("report_nm",""),r.get("rcept_dt","")),reverse=True)
        receipt=reports[0]["rcept_no"]
        url="https://dart.fss.or.kr/dsaf001/main.do?rcpNo="+receipt
        response=requests.get("https://opendart.fss.or.kr/api/document.xml",params={"crtfc_key":key,"rcept_no":receipt},timeout=22)
        response.raise_for_status()
        if len(response.content)>24000000:
            return {"year":year,"url":url,"status":"원문이 커서 자동 추출하지 못했습니다."}
        with ZipFile(BytesIO(response.content)) as archive:
            text=""
            for info in archive.infolist():
                if info.filename.lower().endswith((".xml",".html",".htm")) and info.file_size<16000000:
                    content=BeautifulSoup(archive.read(info),"html.parser").get_text(" ",strip=True)
                    if "핵심감사사항" in content or "핵심 감사사항" in content:
                        text=content
                        break
        match=re.search(r"핵심\s*감사\s*사항",text)
        if not match:
            return {"year":year,"url":url,"status":"핵심감사사항 표제를 확인하지 못했습니다."}
        excerpt=re.sub(r"\s+"," ",text[match.end():match.end()+1500]).strip()
        return {"year":year,"url":url,"excerpt":excerpt,"status":"원문 자동 발췌 · 정확한 범위는 원문 확인 필요"}
    except Exception:
        return {"year":year,"status":"DART 감사보고서 원문 조회 실패"}
