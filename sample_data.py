"""Entirely fictional 2021–2025 financial statement lines for offline demonstration."""
from __future__ import annotations

from analysis import METRICS

DATA = {
    2021: {"revenue": 900, "receivables": 110, "inventory": 130, "cfo": 100, "operating_income": 115},
    2022: {"revenue": 1020, "receivables": 120, "inventory": 145, "cfo": 122, "operating_income": 128},
    2023: {"revenue": 1170, "receivables": 150, "inventory": 180, "cfo": 135, "operating_income": 155},
    2024: {"revenue": 1370, "receivables": 210, "inventory": 220, "cfo": 90, "operating_income": 180},
    2025: {"revenue": 1720, "receivables": 390, "inventory": 400, "cfo": -25, "operating_income": 200},
}


ID_BY_METRIC = {"revenue": ("IS", "ifrs-full_Revenue"),
                "receivables": ("BS", "dart_ShortTermTradeReceivable"),
                "inventory": ("BS", "ifrs-full_Inventories"),
                "cfo": ("CF", "ifrs-full_CashFlowsFromUsedInOperatingActivities"),
                "operating_income": ("IS", "dart_OperatingIncomeLoss")}


def make_sample_annual(n_years: int = 5) -> dict:
    years = sorted(DATA)[-n_years:]
    result = {}
    for year in years:
        lines = []
        for metric, amount_eok in DATA[year].items():
            section, account_id = ID_BY_METRIC[metric]
            lines.append({"sj_div": section, "account_id": account_id,
                          "account_nm": METRICS[metric]["label"],
                          "thstrm_amount": str(round(amount_eok * 1e8)), "currency": "KRW",
                          "rcept_no": ""})
        result[year] = (lines, None)
    return result