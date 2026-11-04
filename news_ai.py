"""Article-body extraction and grounded Korean AI summaries."""
from urllib.parse import urlparse
import ipaddress
import socket
import requests
from bs4 import BeautifulSoup

def extract_article(url):
    parsed=urlparse(url)
    if parsed.scheme!="https" or not parsed.hostname:
        raise ValueError("HTTPS 기사 링크가 아닙니다.")
    host=parsed.hostname.lower()
    if host=="localhost" or host.endswith((".local",".internal")):
        raise ValueError("허용되지 않는 주소")
    # Do not permit server-side requests to private IP ranges.
    for info in socket.getaddrinfo(host,443,type=socket.SOCK_STREAM):
        ip=ipaddress.ip_address(info[4][0])
        if not ip.is_global:
            raise ValueError("내부 네트워크 주소는 접근하지 않습니다.")
    response=requests.get(url,timeout=(5,10),headers={"User-Agent":"Mozilla/5.0"},allow_redirects=False)
    if response.status_code in (301,302,303,307,308):
        raise ValueError("기사 링크가 다른 주소로 연결되어 원문을 직접 읽을 수 없습니다.")
    response.raise_for_status()
    if "html" not in response.headers.get("Content-Type","").lower():
        raise ValueError("HTML 기사 본문이 아닙니다.")
    if len(response.content)>2500000:
        raise ValueError("기사 파일이 너무 큽니다.")
    soup=BeautifulSoup(response.content,"html.parser")
    for element in soup(["script","style","nav","footer","header","aside","form"]):
        element.decompose()
    candidate=soup.select_one("article") or soup.select_one("[itemprop='articleBody']") or soup.select_one("main")
    if candidate is None:
        raise ValueError("기사 본문 영역을 식별하지 못했습니다.")
    text=" ".join(candidate.stripped_strings)
    if len(text)<300:
        raise ValueError("확인 가능한 기사 본문이 충분하지 않습니다.")
    return text[:12000]

def summarize_article(api_key, title, body):
    prompt=("다음 기사 원문만 근거로 한국어로 요약하세요. 추측하거나 기사에 없는 내용을 추가하지 마세요. "
            "회계·감사와 관련된 영향이 명시되어 있을 때만 언급하세요. "
            "출력은 2~3문장, 최대 250자. 기사 제목: "+title+"\n기사 원문:\n"+body)
    response=requests.post("https://api.openai.com/v1/chat/completions",
        headers={"Authorization":"Bearer "+api_key,"Content-Type":"application/json"},
        json={"model":"gpt-4o-mini","messages":[{"role":"user","content":prompt}],"temperature":0.1,"max_tokens":350},
        timeout=(6,25))
    response.raise_for_status()
    return response.json()["choices"][0]["message"]["content"].strip()
