"""Free, source-grounded article extraction and sentence ranking. No paid API."""
from __future__ import annotations
from urllib.parse import urlparse, urljoin
from collections import Counter
import ipaddress
import re
import socket
import requests
from bs4 import BeautifulSoup

HEADERS={"User-Agent":"Mozilla/5.0 (compatible; AuditSignals/1.0)"}
def _safe_url(url):
    parsed=urlparse(url)
    if parsed.scheme not in ("http","https") or not parsed.hostname:
        raise ValueError("유효한 기사 URL이 아닙니다.")
    host=parsed.hostname.lower()
    if host=="localhost" or host.endswith((".local",".internal")):
        raise ValueError("내부 주소 접근 불가")
    for info in socket.getaddrinfo(host,443 if parsed.scheme=="https" else 80,type=socket.SOCK_STREAM):
        if not ipaddress.ip_address(info[4][0]).is_global:
            raise ValueError("내부 IP 접근 불가")
def _download(url):
    for _ in range(3):
        _safe_url(url)
        response=requests.get(url,timeout=(5,9),headers=HEADERS,allow_redirects=False)
        if response.status_code in (301,302,303,307,308):
            target=response.headers.get("Location")
            if not target: raise ValueError("리디렉션 주소 없음")
            url=urljoin(url,target)
            continue
        response.raise_for_status()
        if "html" not in response.headers.get("Content-Type","").lower():
            raise ValueError("HTML 기사 아님")
        if len(response.content)>2500000:
            raise ValueError("기사 크기 제한 초과")
        return response.text
    raise ValueError("리디렉션이 너무 많습니다.")
def extract_article(url):
    soup=BeautifulSoup(_download(url),"html.parser")
    for tag in soup(["script","style","nav","footer","header","aside","form"]):
        tag.decompose()
    selectors=["article","[itemprop='articleBody']","#articleBody","#article-view-content-div",
               "#dic_area","#newsct_article",".article_body",".article-body","main"]
    blocks=[soup.select_one(sel) for sel in selectors]
    blocks=[b for b in blocks if b]
    if not blocks: raise ValueError("기사 본문을 확인할 수 없습니다.")
    text=max((" ".join(b.stripped_strings) for b in blocks),key=len)
    text=re.sub(r"\s+"," ",text).strip()
    if len(text)<300: raise ValueError("기사 본문이 충분하지 않습니다.")
    return text[:15000]
def summarize_article(title, body, max_sentences=3):
    """Extract existing sentences; do not invent claims or call an LLM."""
    sentences=[s.strip() for s in re.split(r"(?<=[.!?。])\s+",body) if len(s.strip())>=28]
    if not sentences: raise ValueError("요약 가능한 문장이 없습니다.")
    title_words=set(re.findall(r"[가-힣A-Za-z0-9]{2,}",title.lower()))
    words=re.findall(r"[가-힣A-Za-z0-9]{2,}",body.lower())
    common=Counter(w for w in words if len(w)>1)
    ranked=[]
    for idx,sentence in enumerate(sentences[:70]):
        tokens=set(re.findall(r"[가-힣A-Za-z0-9]{2,}",sentence.lower()))
        score=sum(min(common[w],12) for w in tokens)/max(len(tokens),1)
        score+=2.5*len(tokens & title_words)
        score+=1.5 if idx<4 else 0
        ranked.append((score,idx,sentence))
    selected=sorted(sorted(ranked,reverse=True)[:max_sentences],key=lambda x:x[1])
    return " ".join(x[2] for x in selected)[:650]
