"""Qwen (Bailian Aliyun) collector — uses Qwen's web_search tool
to search the web and extract structured company information in one call.

Qwen API is OpenAI-compatible at https://dashscope.aliyuncs.com/compatible-mode/v1.
"""

from __future__ import annotations

import json
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime
from threading import local
from typing import Any

import requests

from .base import BaseCollector
from ..models import CompanyMatch, DataSource, RawCompanyData

logger = logging.getLogger(__name__)

MAX_PARALLEL_ROUNDS = 5
MAX_QWEN_ATTEMPTS = 3
RETRY_DELAY_SECONDS = 5


def _make_session() -> requests.Session:
    """创建不包含隐式重试策略的 HTTP 会话。"""
    return requests.Session()


@dataclass(frozen=True)
class _QwenCallResult:
    payload: dict[str, Any] | None
    attempts: int
    error: str | None


@dataclass(frozen=True)
class _RoundResult:
    label: str
    payload: dict[str, Any] | None
    attempts: int
    elapsed_seconds: float
    error: str | None


_SYSTEM_PROMPT = """你是一个企业信息搜索与提取助手。请使用联网搜索功能，搜索指定企业的公开信息，
然后从搜索结果中提取结构化数据。请尽量搜索多个维度的信息。

重要原则：
1. 时效性：优先采用执行时最近一年的数据，如果有多个来源，选择最新的。
2. 合理估算：当精确数据不可得时，可以根据行业地位、员工规模、融资轮次、公开报道等进行合理估算，并在相关字段注明"估算"。不要轻易填 null。
3. 对于未上市但知名度高的企业（如字节跳动、华为、蚂蚁集团等），营收、员工规模等信息虽未官方披露，但媒体报道和行业分析中有大量可参考数据，请积极搜索并给出估算值。"""

_SEARCH_MATCHES_PROMPT = """请搜索用户输入可能对应的企业实体，并返回候选企业列表。
用户输入可能是简称、旧称、模糊称呼或不完整的公司名。

要求：
1. 优先返回最可能的中国企业主体，最多 5 个。
2. 若用户输入足够明确，只返回 1 个最可能企业。
3. 候选之间必须是不同企业，避免重复。
4. company_name 必须尽量写成完整企业名称。
5. confidence 使用 0 到 1 之间的小数。
6. 只返回 JSON，不要输出解释。

用户输入：{company_name}

返回格式：
{{
  "matches": [
    {{
      "company_name": "企业全称",
      "legal_representative": "法定代表人或空字符串",
      "confidence": 0.95
    }}
  ]
}}"""

# ── Round 1a：工商基础信息 ──
_PROMPT_ROUND1A = """请搜索企业「{company_name}」的基本工商注册信息。
请优先搜索执行时最近一年的最新数据。
优先使用以下数据源（按优先级排列）：
1. 国家企业信用信息公示系统（gsxt.gov.cn）
2. 天眼查（tianyancha.com）
3. 企查查（qcc.com）
4. 爱企查（aiqicha.baidu.com）
5. 百度百科、企业官网
避免使用启信宝等数据更新较慢的平台。
如果某项信息搜索不到或不确定，对应字段填 null。
请在 _source_url 中填写你实际获取数据的网页地址（必须是可访问的稳定链接）。

请以 JSON 格式返回：

{{
  "business_info": {{
    "name": "企业全称",
    "foreign_name": "企业外文名（如有）",
    "founder": "创始人姓名（如有）",
    "legal_representative": "法定代表人姓名",
    "registered_capital": 注册资本（单位：元，如5000万填50000000）,
    "establishment_date": "成立日期，格式YYYY-MM-DD",
    "business_status": "经营状态，如存续、注销等",
    "industry": "所属行业",
    "company_type": "企业类型，如有限责任公司(外商投资企业法人独资)",
    "employee_scale": "员工规模，如1000人以上、500-999人、50-99人",
    "region": "所在城市，如北京市、深圳市",
    "main_business": "主营业务/经营范围简述",
    "website_url": "官网地址",
    "summary": "企业简介（100字以内）",
    "business_model": "业务模式：b2c/b2b2c/b2b_brand/b2b/g2b",
    "listing_status": "listed/soe/foreign/group/private/sme",
    "_source_url": "本段信息的来源网页地址"
  }}
}}

只返回 JSON，不要其他文字。"""

# ── Round 1b：财务 + 海外 + 治理 ──
_PROMPT_ROUND1B = """请搜索企业「{company_name}」的财务状况、海外业务和公司治理结构。
请优先搜索执行时最近一年的数据。
优先使用以下数据源：
1. 企业年报、财报（巨潮资讯 cninfo.com.cn、东方财富 eastmoney.com）
2. 天眼查、企查查的企业详情页
3. 企业官网的"关于我们"/"投资者关系"页面
4. 百度百科
避免使用启信宝等数据更新较慢的平台。
如果某项信息搜索不到或不确定，对应字段填 null。
请在 _source_url 中填写你实际获取数据的网页地址（必须是可访问的稳定链接）。

请以 JSON 格式返回：

{{
  "financial_info": {{
    "estimated_revenue": 估算年营收（单位：元人民币）。即使没有官方披露，也请根据行业报道、融资估值、员工规模、同行对比等给出合理估算值，标注来源。尽量不要填null,
    "revenue_source": "营收数据来源描述（如：2025年媒体报道估算/年报披露/行业分析）",
    "_source_url": "本段信息的来源网页地址"
  }},
  "overseas_info": {{
    "has_overseas_business": true/false/null,
    "overseas_regions": ["海外业务地区列表"],
    "overseas_products": ["海外产品/品牌名称列表"],
    "is_foreign_invested": true/false/null,
    "foreign_investor_countries": ["外资来源国家列表"],
    "_source_url": "本段信息的来源网页地址"
  }},
  "governance_info": {{
    "governance_type": "foreign/group_subsidiary/joint_stock/limited/private_small",
    "executives": [{{"name": "高管姓名", "title": "职务"}}],
    "executive_count": 高管人数（整数）,
    "has_procurement_system": true/false/null,
    "compliance_awareness": "high/medium/low",
    "_source_url": "本段信息的来源网页地址"
  }}
}}

只返回 JSON，不要其他文字。"""

# ── Round 2：招聘 + 市场营销活动 ──
_PROMPT_ROUND2 = """请搜索企业「{company_name}」的招聘信息和市场营销活动。
请优先搜索执行时最近一年的最新数据。

招聘信息搜索建议（按优先级）：
1. 该企业官网的"加入我们"/"招聘"页面（链接最稳定）
2. 搜索"[企业名] 招聘"获取概况信息
3. 如果是大企业，可搜索其在脉脉、LinkedIn上的信息
注意：不要返回BOSS直聘、猎聘、智联招聘等平台的具体岗位链接（这些链接会很快失效），
只需要总结该企业在招的岗位类型和数量即可。

市场营销搜索建议：
1. 直接搜索该企业的官方微信公众号、抖音号、微博账号名称
2. 在天猫/京东搜索该企业的官方旗舰店
3. 搜索"[企业名] 广告投放"或"[企业名] 品牌活动"获取营销信息
4. 搜索该企业官网了解品牌动态

如果某项信息搜索不到或不确定，对应字段填 null。
请在 _source_url 中填写你实际获取数据的网页地址（必须是可访问的稳定链接，避免招聘平台的动态链接）。

请以 JSON 格式返回：

{{
  "recruitment_info": {{
    "positions": [{{"title": "岗位名称", "department": "所属部门"}}],
    "design_position_count": 设计类岗位数量（整数，包含UI/UX/视觉/平面/美工/品牌设计等）,
    "departments": ["部门列表"],
    "_source_url": "本段信息的来源网页地址"
  }},
  "market_activity": {{
    "ad_platforms": ["广告投放平台列表，如抖音、微信朋友圈、百度、今日头条等"],
    "ad_activity_level": "high/medium/low（high=多平台持续投放，medium=有投放但不频繁，low=几乎无投放）",
    "social_media_accounts": ["社交媒体账号平台列表，如微信公众号、抖音号、微博、小红书、B站等"],
    "social_media_activity": "high/medium/low（high=日更或高频更新，medium=周更，low=月更或更低）",
    "marketing_events": ["近期营销事件列表，如品牌升级、新品发布、促销活动、赞助活动等"],
    "ecommerce_platforms": ["电商平台列表，如天猫、京东、拼多多、抖音商城等"],
    "ecommerce_presence_count": 电商平台数量（整数）,
    "_source_url": "本段信息的来源网页地址"
  }}
}}

只返回 JSON，不要其他文字。"""

# ── Round 3：诉讼记录 + 技术产品 ──
_PROMPT_ROUND3 = """请搜索企业「{company_name}」的知识产权诉讼记录和技术产品情况。
请优先搜索执行时最近一年的最新数据。

诉讼信息搜索建议（按优先级）：
1. 天眼查的"司法风险"页面（tianyancha.com）
2. 企查查的"法律诉讼"页面（qcc.com）
3. 中国裁判文书网（wenshu.court.gov.cn）
4. 搜索"[企业名] 字体侵权"或"[企业名] 知识产权诉讼"
避免使用启信宝。

技术产品搜索建议：
1. 搜索该企业官网，确认是否有独立官网
2. 在苹果App Store或华为应用市场搜索该企业的APP
3. 在微信中搜索该企业的小程序
4. 搜索"[企业名] APP"或"[企业名] 小程序"

如果某项信息搜索不到或不确定，对应字段填 null。
请在 _source_url 中填写你实际获取数据的网页地址（必须是可访问的稳定链接）。

请以 JSON 格式返回：

{{
  "litigation_info": {{
    "ip_litigation_count": 知识产权诉讼数量（整数）,
    "font_infringement": true/false（是否有字体侵权记录，包括方正、汉仪、造字工房等字体公司起诉）,
    "font_infringement_cases": ["字体侵权案件描述列表"],
    "related_companies": [{{"name": "关联公司名", "litigation_count": 诉讼数量}}],
    "_source_url": "本段信息的来源网页地址"
  }},
  "tech_products": {{
    "has_app": true/false,
    "app_names": ["APP名称列表"],
    "has_mini_program": true/false,
    "has_game": true/false,
    "game_names": ["游戏名称列表"],
    "has_website": true/false,
    "website_url": "官网地址",
    "tech_complexity": "high/medium/low（high=有自研APP/游戏/复杂系统，medium=有小程序/官网，low=几乎无技术产品）",
    "_source_url": "本段信息的来源网页地址"
  }}
}}

只返回 JSON，不要其他文字。"""

# ── Round 4：企业联系方式 ──
_PROMPT_ROUND4_CONTACT = """请搜索企业「{company_name}」的公开联系方式，尽量多找电话号码。
请优先搜索执行时最近一年的最新联系方式。

重点搜索以下渠道的联系方式（按优先级）：
1. 企业官网上的"联系我们"页面（最优先）
2. 天眼查/企查查上的企业联系电话
3. 企业的品牌部/市场部/法务部/知识产权部的联系方式
4. 企业的招聘页面上的HR联系电话
5. 企业的客服电话、400电话、投诉电话
6. 企业在招标网站、行业协会上留的联系方式
7. 企业高管的公开联系方式（如LinkedIn、脉脉等）

特别关注：
- 法务部/知识产权部/合规部的电话（维权相关）
- 品牌部/市场部/广告部的电话（营销相关）
- 采购部/行政部的电话

如果某项信息搜索不到，对应字段填 null 或空列表。
请在 _source_url 中填写你实际获取数据的网页地址。

请以 JSON 格式返回：

{{
  "contact_info": {{
    "official_phone": "企业总机/前台电话",
    "customer_service": "客服电话/400电话",
    "legal_dept_phone": "法务部/知识产权部电话",
    "marketing_dept_phone": "品牌部/市场部电话",
    "procurement_dept_phone": "采购部电话",
    "hr_phone": "人事/招聘电话",
    "other_phones": ["其他公开电话号码列表"],
    "email": "企业公开邮箱",
    "legal_email": "法务相关邮箱",
    "address": "企业办公地址",
    "contact_persons": [
      {{"name": "联系人姓名", "title": "职务", "phone": "电话", "source": "信息来源"}}
    ],
    "_source_url": "本段信息的来源网页地址"
  }}
}}

只返回 JSON，不要其他文字。"""

_ENRICH_PROMPT = """请搜索企业「{company_name}」的以下补充信息，并提取结构化数据。
请优先搜索执行时最近一年的数据。
如果信息不确定或未提及，对应字段填 null。
涉及到金钱的，请转换成人民币。

请以 JSON 格式返回：
{{
  "estimated_revenue": 估算年营收（单位：元人民币）。即使没有官方数据，也请根据媒体报道、行业分析等给出合理估算,
  "revenue_source": "营收数据来源描述",
  "has_overseas_business": true/false/null,
  "overseas_regions": ["海外业务地区列表"],
  "overseas_products": ["海外产品/品牌名称列表"],
  "is_foreign_invested": true/false/null,
  "foreign_investor_countries": ["外资来源国家列表"]
}}

只返回 JSON，不要其他文字。"""


class QwenCollector(BaseCollector):
    """Collector that uses Qwen web search."""

    def __init__(
        self,
        api_key: str = "",
        base_url: str = "",
        model: str = "",
        timeout_seconds: int = 300,
    ) -> None:
        """
        初始化 Qwen 企业信息采集器

        参数：
            api_key:
                - Qwen OpenAI 兼容接口的 API Key
            base_url:
                - API 基础地址；为空时使用阿里云 DashScope 兼容地址
            model:
                - 调用的模型名称；为空时使用默认模型
            timeout_seconds:
                - 单次网络请求的读取超时时间，单位为秒

        说明：
            - OpenAI 客户端和 requests 会话均在线程首次使用时创建
            - 每个工作线程维护独立的传输客户端，避免跨线程共享连接状态
        """
        self._api_key = api_key
        self._base_url = (base_url or "https://dashscope.aliyuncs.com/compatible-mode/v1").rstrip("/")
        self._model = model or "qwen3.5-plus"
        self._timeout_seconds = max(1, int(timeout_seconds))
        self._transport_local = local()
        self._openai_client_factory = None
        if api_key:
            try:
                from openai import OpenAI
            except ImportError:
                pass
            else:
                self._openai_client_factory = OpenAI

    def is_configured(self) -> bool:
        return bool(self._api_key)

    def search(self, company_name: str) -> list[CompanyMatch]:
        """Search for a company using Qwen web search and return matches."""
        if not self._api_key:
            return []
        # 企业实体识别/消歧：对存在多个公司的输入名称，返回 "候选列表"
        call_result = self._call_qwen(
            _SEARCH_MATCHES_PROMPT.format(company_name=company_name),
            company_id=company_name,
            round_label="search",
        )
        result = call_result.payload
        matches = self._parse_search_matches(result)
        if matches:
            return matches

        return [
            CompanyMatch(
                company_id=company_name,
                company_name=company_name,
                legal_representative="",
                source="qwen",
                confidence=0.8,
            )
        ]

    def collect(self, company_id: str) -> RawCompanyData:
        """
        并行执行五轮企业公开信息采集，并按固定轮次顺序合并结果

        参数：
            company_id:
                - 待采集企业的名称或唯一标识

        执行流程：
            1. 将基础工商、财务治理、招聘营销、诉讼技术和联系方式五轮任务
               提交到有界线程池
            2. 每轮独立调用 Qwen，并在轮次内部完成重试和耗时统计
            3. 等待所有轮次结束后，按照原始轮次定义顺序合并有效数据
            4. 根据成功轮次数记录 full_collection、partial_collection
               或 collection_failed 状态

        返回：
            RawCompanyData:
                - 合并后的企业原始数据
                - 单轮失败不会丢弃其他成功轮次的数据
                - 所有轮次失败时返回仅包含企业标识和失败状态的数据
        """
        raw = RawCompanyData(company_id=company_id, company_name=company_id)
        if not self._api_key:
            return raw

        now = datetime.now()
        rounds = [
            ("1a-基础工商", _PROMPT_ROUND1A),
            ("1b-财务治理", _PROMPT_ROUND1B),
            ("2-招聘营销", _PROMPT_ROUND2),
            ("3-诉讼技术", _PROMPT_ROUND3),
            ("4-联系方式", _PROMPT_ROUND4_CONTACT),
        ]

        started_at = time.perf_counter()
        max_workers = min(MAX_PARALLEL_ROUNDS, len(rounds))
        logger.info(
            "Qwen collection started: company=%s rounds=%d max_workers=%d",
            company_id,
            len(rounds),
            max_workers,
        )

        with ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="company-profile-qwen",
        ) as executor:
            futures = [
                executor.submit(
                    self._collect_round,
                    label,
                    prompt_tpl.format(company_name=company_id),
                    company_id,
                )
                for label, prompt_tpl in rounds
            ]

            round_results: list[_RoundResult] = []
            for (label, _), future in zip(rounds, futures):
                try:
                    round_results.append(future.result())
                except Exception as exc:
                    logger.error(
                        "Qwen round crashed: company=%s round=%s",
                        company_id,
                        label,
                        exc_info=True,
                    )
                    round_results.append(
                        _RoundResult(
                            label=label,
                            payload=None,
                            attempts=0,
                            elapsed_seconds=0.0,
                            error=f"{type(exc).__name__}: {exc}",
                        )
                    )

        successful_rounds = 0
        for round_result in round_results:
            result = round_result.payload
            if not result:
                continue
            successful_rounds += 1
            # Merge only on the caller thread and in declared round order.
            for section_name, section_data in result.items():
                if isinstance(section_data, dict):
                    url = section_data.get("_source_url")
                    if isinstance(url, str) and url.startswith("http"):
                        raw.sources.append(
                            DataSource("qwen_web_search", url, now, section_name)
                        )
            self._merge_extracted(raw, result)

        failed_rounds = len(rounds) - successful_rounds
        elapsed_seconds = time.perf_counter() - started_at
        if successful_rounds == 0:
            logger.warning(
                "Qwen collection completed: company=%s all %d rounds failed elapsed_seconds=%.2f",
                company_id,
                len(rounds),
                elapsed_seconds,
            )
        else:
            logger.info(
                "Qwen collection completed: company=%s successful_rounds=%d failed_rounds=%d "
                "elapsed_seconds=%.2f",
                company_id,
                successful_rounds,
                failed_rounds,
                elapsed_seconds,
            )

        # Keep original input name for report; store Qwen's resolved name in business_info
        resolved_name = raw.business_info.get("name") or raw.business_info.get("Name")
        if resolved_name and resolved_name != company_id:
            raw.business_info["resolved_name"] = resolved_name
        # company_name stays as the original input (company_id)

        # 记录 web_search 状态
        status = (
            "full_collection"
            if successful_rounds == len(rounds)
            else "partial_collection"
            if successful_rounds > 0
            else "collection_failed"
        )

        raw.sources.append(
            DataSource("qwen_web_search", "qwen://web_search", now, status)
        )
        return raw

    def _collect_round(
        self,
        label: str,
        prompt: str,
        company_id: str,
    ) -> _RoundResult:
        """
        执行单轮 Qwen 采集并记录运行状态

        参数：
            label:
                - 当前采集轮次的可读名称，用于日志定位
            prompt:
                - 已填充企业名称的完整提示词
            company_id:
                - 当前采集企业的名称或唯一标识

        返回：
            _RoundResult:
                - payload: 成功解析的结构化结果，失败时为 None
                - attempts: 实际调用次数
                - elapsed_seconds: 当前轮次总耗时
                - error: 最后一次失败原因，成功时为 None
        """
        started_at = time.perf_counter()
        call_result = self._call_qwen(
            prompt,
            company_id=company_id,
            round_label=label,
        )
        elapsed_seconds = time.perf_counter() - started_at

        if call_result.payload:
            logger.debug(
                "Qwen round completed: company=%s round=%s attempts=%d elapsed_seconds=%.2f",
                company_id,
                label,
                call_result.attempts,
                elapsed_seconds,
            )
        else:
            logger.warning(
                "Qwen round failed: company=%s round=%s attempts=%d elapsed_seconds=%.2f error=%s",
                company_id,
                label,
                call_result.attempts,
                elapsed_seconds,
                call_result.error or "empty response",
            )

        return _RoundResult(
            label=label,
            payload=call_result.payload,
            attempts=call_result.attempts,
            elapsed_seconds=elapsed_seconds,
            error=call_result.error,
        )

    @staticmethod
    def _merge_extracted(raw: RawCompanyData, extracted: dict[str, Any]) -> None:
        """Merge extracted dict sections into raw data, without overwriting existing keys.

        Each section may contain a _source_url field — it is stored as-is
        so that downstream extractors can reference it.
        """
        for field in (
            "business_info", "financial_info", "overseas_info",
            "recruitment_info", "market_activity", "litigation_info",
            "tech_products", "governance_info", "contact_info",
        ):
            section = extracted.get(field)
            if isinstance(section, dict) and section:
                target = getattr(raw, field)
                for k, v in section.items():
                    key = k.lower()
                    if v is not None and key not in target:
                        target[key] = v

    def enrich(self, raw_data: RawCompanyData) -> RawCompanyData:
        """Enrich existing raw data using Qwen web search.

        Only fills in fields that are not already populated.
        """
        if not self._api_key:
            return raw_data

        try:
            call_result = self._call_qwen(
                _ENRICH_PROMPT.format(company_name=raw_data.company_name),
                company_id=raw_data.company_name,
                round_label="enrich",
            )
            extracted = call_result.payload
            if extracted:
                now = datetime.now()
                rev = extracted.get("estimated_revenue")
                if rev is not None and "estimated_revenue" not in raw_data.financial_info:
                    raw_data.financial_info["estimated_revenue"] = rev
                    raw_data.financial_info["revenue_source"] = extracted.get(
                        "revenue_source", "Qwen搜索提取"
                    )
                has_overseas = extracted.get("has_overseas_business")
                if has_overseas is not None and "has_overseas_business" not in raw_data.overseas_info:
                    raw_data.overseas_info["has_overseas_business"] = has_overseas
                    raw_data.overseas_info["overseas_regions"] = extracted.get(
                        "overseas_regions", []
                    )
                    raw_data.overseas_info["overseas_products"] = extracted.get(
                        "overseas_products", []
                    )
                is_foreign = extracted.get("is_foreign_invested")
                if is_foreign is not None and "is_foreign_invested" not in raw_data.overseas_info:
                    raw_data.overseas_info["is_foreign_invested"] = is_foreign
                    raw_data.overseas_info["foreign_investor_countries"] = extracted.get(
                        "foreign_investor_countries", []
                    )
                raw_data.sources.append(
                    DataSource(
                        "qwen_web_search",
                        "qwen://web_search",
                        now,
                        "qwen_enrichment",
                    )
                )
        except Exception:
            logger.debug("Qwen enrichment failed", exc_info=True)

        return raw_data

    @staticmethod
    def _parse_search_matches(payload: dict[str, Any] | None) -> list[CompanyMatch]:
        if not isinstance(payload, dict):
            return []

        matches_payload = payload.get("matches")
        if not isinstance(matches_payload, list):
            return []

        seen: set[str] = set()
        matches: list[CompanyMatch] = []
        for item in matches_payload:
            if not isinstance(item, dict):
                continue

            company_name = str(item.get("company_name") or "").strip()
            if not company_name or company_name in seen:
                continue

            seen.add(company_name)
            try:
                confidence = float(item.get("confidence", 0.8))
            except (TypeError, ValueError):
                confidence = 0.8

            matches.append(
                CompanyMatch(
                    company_id=company_name,
                    company_name=company_name,
                    legal_representative=str(item.get("legal_representative") or "").strip(),
                    source="qwen_web_search",
                    confidence=max(0.0, min(1.0, confidence)),
                )
            )

        return matches

    def _call_qwen(
        self,
        prompt: str,
        company_id: str = "-",
        round_label: str = "-",
    ) -> _QwenCallResult:
        """
        使用统一的应用层重试策略调用 Qwen

        参数：
            prompt:
                - 发送给 Qwen 的用户提示词
            company_id:
                - 当前企业名称或标识，用于关联并发请求日志
            round_label:
                - 当前请求所属轮次，用于区分并发执行的提示词

        重试策略：
            - 最多调用 MAX_QWEN_ATTEMPTS 次
            - 异常、空响应或无法解析的响应均视为本次失败
            - 每次重试前按照 RETRY_DELAY_SECONDS * 当前次数递增等待

        返回：
            _QwenCallResult:
                - payload: 成功解析的字典，最终失败时为 None
                - attempts: 实际尝试次数
                - error: 最后一次失败原因，成功时为 None
        """
        last_error: str | None = None
        for attempt in range(1, MAX_QWEN_ATTEMPTS + 1):
            try:
                result = self._call_qwen_once(prompt)
                if result:
                    return _QwenCallResult(
                        payload=result,
                        attempts=attempt,
                        error=None,
                    )
                last_error = "empty or invalid response"
            except Exception as exc:
                last_error = f"{type(exc).__name__}: {exc}"

            if attempt < MAX_QWEN_ATTEMPTS:
                logger.debug(
                    "Qwen retry scheduled: company=%s round=%s attempt=%d/%d error=%s",
                    company_id,
                    round_label,
                    attempt,
                    MAX_QWEN_ATTEMPTS,
                    last_error,
                )
                time.sleep(RETRY_DELAY_SECONDS * attempt)

        return _QwenCallResult(
            payload=None,
            attempts=MAX_QWEN_ATTEMPTS,
            error=last_error,
        )

    def _get_openai_client(self):
        """获取当前线程专用的 OpenAI 客户端；未启用 SDK 时返回 None。"""
        if self._openai_client_factory is None:
            return None

        client = getattr(self._transport_local, "openai_client", None)
        if client is None:
            client = self._openai_client_factory(
                api_key=self._api_key,
                base_url=self._base_url,
                timeout=self._timeout_seconds,
                max_retries=0,
            )
            self._transport_local.openai_client = client
        return client

    def _get_session(self) -> requests.Session:
        """获取当前线程专用的 requests 会话，不在线程之间共享连接状态。"""
        session = getattr(self._transport_local, "session", None)
        if session is None:
            session = _make_session()
            self._transport_local.session = session
        return session

    def _call_qwen_once(self, prompt: str) -> dict[str, Any] | None:
        """为提示词补充当前日期，并通过可用传输方式执行一次 Qwen 请求。"""
        prompt = f"当前日期：{date.today().isoformat()}。\n{prompt}"
        if self._openai_client_factory is not None:
            return self._call_via_openai(prompt)
        return self._call_via_requests(prompt)

    def _call_via_openai(self, prompt: str) -> dict[str, Any] | None:
        """
        使用当前线程专用的 OpenAI 客户端调用 Qwen

        参数：
            prompt:
                - 已补充当前日期的用户提示词

        返回：
            dict[str, Any] | None:
                - 模型响应中解析出的 JSON 字典
                - 响应内容为空或无法解析时返回 None

        异常：
            - 网络错误、超时和服务端错误向上抛出，由应用层统一重试
        """
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ]
        client = self._get_openai_client()
        if client is None:
            return self._call_via_requests(prompt)

        response = client.chat.completions.create(
            model=self._model,
            messages=messages,
            extra_body={
                "enable_search": True,
                "search_options": {
                    "search_strategy": "turbo",
                    "enable_search_extension": True,
                },
            },
        )
        choice = response.choices[0]
        content = (choice.message.content or "").strip()
        if not content:
            return None
        return self._extract_json(content)

    def _call_via_requests(self, prompt: str) -> dict[str, Any] | None:
        """
        使用当前线程专用的 requests 会话调用 Qwen

        参数：
            prompt:
                - 已补充当前日期的用户提示词

        返回：
            dict[str, Any] | None:
                - 模型响应中解析出的 JSON 字典
                - 响应内容为空或无法解析时返回 None

        异常：
            - 网络错误、超时、非成功 HTTP 状态和响应解析错误向上抛出，
              由应用层统一重试
        """
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ]
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "enable_search": True,
            "stream": False,
        }

        resp = self._get_session().post(
            f"{self._base_url}/chat/completions",
            headers=headers,
            json=payload,
            timeout=(30, self._timeout_seconds),
            stream=False,
        )
        resp.raise_for_status()
        data = resp.json()

        choice = data.get("choices", [{}])[0]
        message = choice.get("message", {})
        content = (message.get("content") or "").strip()

        if not content:
            return None
        return self._extract_json(content)

    @staticmethod
    def _extract_json(content: str) -> dict[str, Any] | None:
        """Extract and parse JSON from model response content."""
        json_match = re.search(r"```(?:json)?\s*\n(.*?)\n```", content, re.DOTALL)
        if json_match:
            content = json_match.group(1).strip()
        else:
            brace_start = content.find("{")
            if brace_start >= 0:
                depth = 0
                for i in range(brace_start, len(content)):
                    if content[i] == "{":
                        depth += 1
                    elif content[i] == "}":
                        depth -= 1
                        if depth == 0:
                            content = content[brace_start:i + 1]
                            break
        try:
            return json.loads(content)
        except json.JSONDecodeError as exc:
            logger.debug("Failed to parse Qwen JSON response: %s", exc)
            return None
