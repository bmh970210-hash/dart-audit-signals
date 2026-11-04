"""Download OpenDART corpCode.xml and write a small UTF-8 CSV for instant app search."""
import csv
import io
import os
from pathlib import Path
import xml.etree.ElementTree as ET
from zipfile import ZipFile

import requests

key = os.environ["DART_API_KEY"]
response = requests.get("https://opendart.fss.or.kr/api/corpCode.xml", params={"crtfc_key": key}, timeout=(15, 120))
response.raise_for_status()
with ZipFile(io.BytesIO(response.content)) as archive:
    filename = next(n for n in archive.namelist() if n.lower().endswith(".xml"))
    with archive.open(filename) as source:
        root = ET.parse(source).getroot()
rows = []
for item in root.findall("list"):
    code = (item.findtext("corp_code") or "").strip()
    name = (item.findtext("corp_name") or "").strip()
    stock = (item.findtext("stock_code") or "").strip()
    if code and name:
        rows.append((code, name, stock))
if len(rows) < 10000:
    raise RuntimeError("Company registry looks incomplete; refusing to overwrite.")
path = Path("data/corporations.csv")
path.parent.mkdir(parents=True, exist_ok=True)
with path.open("w", encoding="utf-8-sig", newline="") as output:
    writer = csv.writer(output)
    writer.writerow(["corp_code", "corp_name", "stock_code"])
    writer.writerows(sorted(rows, key=lambda x: x[1]))
print(f"Wrote {len(rows)} company codes")
