# -*- coding: utf-8 -*-
"""Web scraper — uses Bing search to gather public company information.

Replaces the old qcc.com scraper (which was blocked by anti-crawl).
Uses Bing's public search results to extract company snippets, then
parses structured fields from the text.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime

import requests
from bs4 import BeautifulSoup

from .base import BaseCollector
from ..models import CompanyMatch, DataSource, RawCompanyData

logger = logging.getLogger(__name__)

BING_SEARCH_URL = "https://www.bing.com/search"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "zh-CN,zh;q=0.9",
}


class WebScraperAdapter(BaseCollector):
    """Fallback collector that uses Bing search to gather public company info.

    No API key required. Searches Bing for company information and extracts
    structured fields from search result snippets.
    """

    def __init__(self) -> None:
        self._session = requests.Session()
        self._session.headers.update(_HEADERS)

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def search(self, company_name: str) -> list[CompanyMatch]:
        """Search Bing for the company and return a best-guess match."""
        try:
            snippets = self._bing_search(f"{company_name} 企业 公司简介")
            if snippets:
                return [
                    CompanyMatch(
                        company_id=company_name,
                        company_name=company_name,
                        legal_representative="",
                        source="bing_search",
                        confidence=0.5,
                    )
                ]
        except Exception:
            logger.debug("Bing search failed for %s", company_name, exc_info=True)
        return []

    def collect(self, company_id: str) -> RawCompanyData:
        """Collect company info by searching Bing for multiple query angles."""
        raw = RawCompanyData(company_id=company_id, company_name=company_id)
        now = datetime.now()

        queries = [
            (f"{company_id} 注册资本 成立时间 法定代表人 员工人数", "business_info"),
            (f"{company_id} 年营收 营业收入 营业额", "financial_info"),
            (f"{company_id} 海外业务 出海 国际化 外资", "overseas_info"),
            (f"{company_id} 知识产权诉讼 字体侵权 版权纠纷", "litigation_info"),
            (f"{company_id} APP 小程序 游戏产品 官网", "tech_products"),
            (f"{company_id} 招聘 设计岗位 品牌部门 市场部", "recruitment_info"),
        ]

        all_snippets: list[str] = []
        for query, field_hint in queries:
            try:
                snippets = self._bing_search(query)
                if snippets:
                    all_snippets.extend(snippets)
                    raw.sources.append(
                        DataSource("bing_search", f"bing://{query[:60]}", now, field_hint)
                    )
            except Exception:
                logger.debug("Bing query failed: %s", query, exc_info=True)

        if all_snippets:
            self._parse_snippets(company_id, all_snippets, raw)

        return raw

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _bing_search(self, query: str, count: int = 5) -> list[str]:
        """Search Bing and return text snippets from results."""
        resp = self._session.get(
            BING_SEARCH_URL,
            params={"q": query, "count": count, "setlang": "zh-Hans"},
            timeout=15,
        )
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        snippets: list[str] = []
        # Bing result containers
        for item in soup.select("#b_results .b_algo"):
            caption = item.select_one(".b_caption p, .b_snippet")
            if caption:
                text = caption.get_text(strip=True)
                if text:
                    snippets.append(text)
            if len(snippets) >= count:
                break
        return snippets

    def _parse_snippets(
        self, company_name: str, snippets: str | list[str], raw: RawCompanyData
    ) -> None:
        """Parse structured fields from Bing search snippets."""
        text = " ".join(snippets) if isinstance(snippets, list) else snippets

        # --- business_info ---
        info = raw.business_info

        # Company name (use search term as fallback)
        if not info.get("name"):
            info["name"] = company_name

        # Registered capital
        if not info.get("registered_capital"):
            m = re.search(r"注册资本[：:]\s*([\d,.]+)\s*(亿|万)?\s*元?", text)
            if m:
                val = float(m.group(1).replace(",", ""))
                unit = m.group(2) or ""
                if unit == "亿":
                    val *= 1e8
                elif unit == "万":
                    val *= 1e4
                info["registered_capital"] = val

        # Establishment date
        if not info.get("establishment_date"):
            m = re.search(r"成立(?:于|时间)?[：:\s]*(20\d{2}|19\d{2})年(\d{1,2})月?(\d{1,2})?日?", text)
            if m:
                y, mo, d = m.group(1), m.group(2) or "1", m.group(3) or "1"
                info["establishment_date"] = f"{y}-{int(mo):02d}-{int(d):02d}"

        # Region
        if not info.get("region"):
            m = re.search(r"(?:总部|注册地|位于|坐落于)[：:\s]*(北京|上海|广州|深圳|杭州|成都|南京|武汉|重庆|苏州|西安|长沙|天津)", text)
            if m:
                info["region"] = m.group(1)

        # Employee scale
        if not info.get("employee_scale"):
            m = re.search(r"员工[人数]?[约达超过]*\s*([\d,.]+)\s*(?:余|多)?人", text)
            if m:
                info["employee_scale"] = int(m.group(1).replace(",", "").replace(".", ""))

        # Industry
        if not info.get("industry"):
            for kw in ["互联网", "科技", "传媒", "广告", "游戏", "电商", "教育", "金融", "制造"]:
                if kw in text:
                    info["industry"] = kw
                    break

        # --- financial_info ---
        if not raw.financial_info.get("estimated_revenue"):
            m = re.search(r"(?:营收|营业收入|年收入)[约达超过]*\s*([\d,.]+)\s*(亿|万)?\s*元?", text)
            if m:
                val = float(m.group(1).replace(",", ""))
                unit = m.group(2) or ""
                if unit == "亿":
                    val *= 1e8
                elif unit == "万":
                    val *= 1e4
                raw.financial_info["estimated_revenue"] = val
                raw.financial_info["revenue_source"] = "Bing搜索提取"

        # --- overseas_info ---
        if "has_overseas_business" not in raw.overseas_info:
            overseas_keywords = ["海外", "出海", "国际化", "全球", "境外", "海外市场"]
            if any(kw in text for kw in overseas_keywords):
                raw.overseas_info["has_overseas_business"] = True

        # --- litigation_info ---
        if "ip_litigation_count" not in raw.litigation_info:
            m = re.search(r"知识产权.*?(\d+)\s*起", text)
            if m:
                raw.litigation_info["ip_litigation_count"] = int(m.group(1))
            if "字体侵权" in text or "字体版权" in text:
                raw.litigation_info["font_infringement"] = True

        # --- tech_products ---
        tech = raw.tech_products
        if "has_app" not in tech:
            if re.search(r"APP|应用|客户端", text, re.IGNORECASE):
                tech["has_app"] = True
        if "has_mini_program" not in tech:
            if "小程序" in text:
                tech["has_mini_program"] = True
        if "has_game" not in tech:
            if "游戏" in text:
                tech["has_game"] = True
        if "has_website" not in tech and re.search(r"官网|官方网站", text):
            tech["has_website"] = True

        # --- recruitment_info ---
        if "design_position_count" not in raw.recruitment_info:
            design_kws = ["设计师", "UI设计", "视觉设计", "平面设计", "品牌设计"]
            count = sum(1 for kw in design_kws if kw in text)
            if count > 0:
                raw.recruitment_info["design_position_count"] = count
