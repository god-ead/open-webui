"""报告生成器 — 将分析结果整合为 Markdown / PDF 格式的完整报告。

报告章节：
1. 企业基本信息摘要
2. 六维特征分析明细
3. 评分卡结果
4. 成单可能性评估
5. 销售沟通策略建议
6. 结论
7. 数据来源说明
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from .models import (
    AnalysisResult,
    DimensionFeatures,
    DimensionScore,
    FeatureStatus,
    FeatureValue,
)

logger = logging.getLogger(__name__)

# Dimension key → Chinese display name
_DIMENSION_LABELS: dict[str, str] = {
    "basic_attributes": "企业基础属性",
    "business_relevance": "业务关联特征",
    "public_behavior": "公开行为特征",
    "copyright_risk": "版权风险敏感度",
    "tech_environment": "技术环境",
    "decision_chain": "决策链特征",
    "marketing_intent": "营销意愿",
}

# Feature key → Chinese display name (from scoring_config.yaml labels)
_FEATURE_LABELS: dict[str, str] = {
    # basic_attributes
    "listing_status": "上市/国企/外资背景",
    "industry_type": "行业类型",
    "annual_revenue": "年营收",
    "region": "总部地域",
    "employee_scale": "员工规模",
    # kept for backward compat with old reports
    "registered_capital": "注册资本",
    "establishment_years": "成立年限",
    "industry_match": "行业匹配度",
    "overseas_business": "海外业务",
    "foreign_investment": "外资背景",
    # business_relevance
    "business_model": "业务模式",
    "design_positions": "设计类在招岗位数",
    "brand_marketing_dept": "品牌/市场部门",
    "content_output_density": "内容产出密度",
    # kept for backward compat
    "high_risk_industry": "侵权高风险行业",
    "brand_product_count": "品牌/产品线数量",
    # public_behavior
    "ad_activity": "广告投放活跃度",
    "social_media_activity": "社交媒体活跃平台数",
    "recent_marketing_events": "近期营销事件",
    "ecommerce_presence": "电商平台入驻数",
    # copyright_risk
    "font_infringement_litigation": "字体侵权诉讼记录",
    "other_ip_litigation": "其他知识产权诉讼数",
    "affiliated_infringement": "关联企业侵权记录",
    "infringement_peak_period": "行业侵权高发期状态",
    # tech_environment
    "has_app": "已发布 APP",
    "has_mini_program": "微信小程序",
    "has_game": "游戏产品",
    "website_complexity": "官网复杂度",
    # decision_chain
    "governance_structure": "治理结构",
    "decision_chain_length": "决策链长度",
    "procurement_system": "集中采购制度",
    "compliance_awareness": "合规意识",
    # marketing_intent（仅参与评分的特征需在 scoring_config 中配置；其余仅展示）
    "marketing_willingness_score": "营销意愿综合分",
    "channel_count": "宣传渠道数量",
    "update_frequency": "渠道更新频次",
    "channel_details": "渠道明细",
    "key_factors": "营销关键因素",
}

# Suggested channels for supplementing missing data per dimension
_SUPPLEMENT_CHANNELS: dict[str, str] = {
    "basic_attributes": "企查查、天眼查等工商数据平台",
    "business_relevance": "招聘平台（Boss直聘、猎聘）、企业官网",
    "public_behavior": "社交媒体平台、广告监测工具、电商平台",
    "copyright_risk": "裁判文书网、知识产权局公开数据",
    "tech_environment": "应用商店、微信小程序搜索、官网检测工具",
    "decision_chain": "工商数据平台、企业年报、公开治理信息",
    "marketing_intent": "企业官网、社交媒体平台、百度搜索",
}


class PdfGenerationError(RuntimeError):
    """Raised when a PDF report cannot be generated."""


class ReportGenerator:
    """Generate Markdown and PDF analysis reports."""

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def generate_markdown(self, data: AnalysisResult) -> str:
        """Generate a full Markdown report from *data*."""
        sections = [
            self._header(data),
            self._section_basic_info(data),
            self._section_operating_status(data),
            self._section_company_overview(data),
            self._section_probability(data),
            self._section_scorecard(data),
            self._section_contact_info(data),
            self._section_strategy(data),
            self._section_six_dimensions(data),
            self._section_conclusion(data),
            self._section_data_sources(data),
        ]
        return "\n\n".join(sections) + "\n"

    def generate_pdf(self, data: AnalysisResult, output_path: str) -> None:
        """Generate a PDF report and raise when PDF rendering is unavailable."""
        md_content = self.generate_markdown(data)
        out = Path(output_path)

        try:
            import markdown
            from weasyprint import HTML  # type: ignore[import-untyped]
        except ImportError as exc:
            raise PdfGenerationError(
                "PDF 依赖缺失，需要安装 markdown 和 weasyprint"
            ) from exc

        html_body = markdown.markdown(md_content, extensions=["tables", "fenced_code"])
        html_full = (
            "<html><head><meta charset='utf-8'>"
            "<style>body{font-family:sans-serif;padding:2em;}"
            "table{border-collapse:collapse;width:100%;}"
            "th,td{border:1px solid #ccc;padding:6px 10px;text-align:left;}"
            "</style></head><body>" + html_body + "</body></html>"
        )
        try:
            out.parent.mkdir(parents=True, exist_ok=True)
            HTML(string=html_full).write_pdf(str(out))
        except Exception as exc:
            if out.exists():
                out.unlink()
            raise PdfGenerationError(f"PDF 报告生成失败: {exc}") from exc

        if not out.is_file():
            raise PdfGenerationError(f"PDF 报告未生成: {out}")
        logger.info("PDF 报告已生成: %s", out)

    # ------------------------------------------------------------------
    # Header
    # ------------------------------------------------------------------

    @staticmethod
    def _header(data: AnalysisResult) -> str:
        ts = _fmt_datetime(data.analyzed_at)
        sr = data.score_result
        score_bar = "🟢" if sr.total_score >= 65 else ("🟡" if sr.total_score >= 35 else "🔴")
        return (
            f"# {score_bar} {data.raw_data.company_name} — 综合得分 {sr.total_score:.0f}/100　成单可能性：{sr.probability_level}\n\n"
            f"报告编号：{data.report_id}　　生成时间：{ts}"
        )

    # ------------------------------------------------------------------
    # 1. 基础工商信息
    # ------------------------------------------------------------------

    @staticmethod
    def _section_basic_info(data: AnalysisResult) -> str:
        info = data.raw_data.business_info

        fields = [
            ("法定代表人", info.get("legal_representative")),
            ("经营状态", info.get("business_status")),
            ("成立日期", info.get("establishment_date")),
            ("注册资本", info.get("registered_capital")),
            ("所属行业",info.get( "industry")),
            ("企业类型", info.get("company_type")),
            ("注册地址", info.get("registered_address") or info.get("address")),
            ("营业期限", info.get("business_term") or info.get("operating_period")),
            ("员工规模", info.get("employee_scale")),
            ("所在地域", info.get("region")),
        ]
        lines = ["## 一、基础工商信息", ""]
        lines.extend(
            f"- {label}：{_format_raw_value(value)}"
            for label, value in fields
            if value not in (None, "", [], {})
        )
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # 2. 企业经营范围
    # ------------------------------------------------------------------

    @staticmethod
    def _section_operating_status(data: AnalysisResult) -> str:
        info = data.raw_data.business_info
        licensed_items = info.get("licensed_items")
        general_items = info.get("general_items")
        business_scope = str(info.get("business_scope") or "").strip()

        # 缺少结构化经营范围时，按许可项目和一般项目标记拆分原始文本
        if not licensed_items and not general_items and business_scope:
            licensed_marker = "许可项目"
            general_marker = "一般项目"
            if licensed_marker in business_scope and general_marker in business_scope:
                licensed_text, general_text = business_scope.split(general_marker, 1)
                licensed_items = licensed_text.split(licensed_marker, 1)[1].strip(
                    "：:；;，, "
                )
                general_items = general_text.strip("：:；;，, ")

        lines = ["## 二、经营范围", ""]
        for label, value in (("许可项目", licensed_items), ("一般项目", general_items)):
            if value not in (None, "", [], {}):
                display = (
                    "；".join(str(item) for item in value)
                    if isinstance(value, list)
                    else str(value)
                )
                lines.append(f"{label}：{display}")

        if len(lines) == 2 and business_scope:
            lines.append(business_scope)
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # 3. 企业整体情况
    # ------------------------------------------------------------------

    @staticmethod
    def _section_company_overview(data: AnalysisResult) -> str:
        info = data.raw_data.business_info
        litigation = data.raw_data.litigation_info

        brand_background = info.get("summary") or info.get("main_business")

        # 由 litigation_info 中现有字段 ip_litigation_count / font_infringement 生成“知产纠纷/字体侵权”记录
        judicial_risk = litigation.get("judicial_risk") or litigation.get("summary")
        if not judicial_risk:
            risk_parts = []
            case_count = litigation.get("ip_litigation_count")
            if case_count is not None:
                risk_parts.append(f"公开可查知识产权诉讼 {case_count} 起")
            font_infringement = litigation.get("font_infringement")
            if font_infringement is not None:
                risk_parts.append("有字体侵权记录" if font_infringement else "无字体侵权记录")
            judicial_risk = "；".join(risk_parts)

        fields = [
            ("品牌背景", brand_background),
            ("司法风险", judicial_risk),
            ("业务模式", info.get("business_model")),
        ]
        lines = ["## 三、企业整体情况", ""]
        lines.extend(
            f"- {label}：{_format_raw_value(value)}"
            for label, value in fields
            if value not in (None, "", [], {})
        )
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # 4. 成单可能性评估
    # ------------------------------------------------------------------

    @staticmethod
    def _section_probability(data: AnalysisResult) -> str:
        sr = data.score_result
        lines = ["## 四、成单可能性评估\n"]
        lines.append(f"- **成单可能性等级**：{sr.probability_level}")
        lines.append(f"- **综合得分**：{sr.total_score:.1f} / 100")

        if sr.top_factors:
            factors = "、".join(_FEATURE_LABELS.get(f, f) for f in sr.top_factors)
            lines.append(f"- **关键影响因素（Top-3）**：{factors}")

        if sr.follow_up_suggestion:
            lines.append(f"- **跟进建议**：{sr.follow_up_suggestion}")

        if sr.low_confidence:
            lines.append(
                "\n> ⚠️ **数据置信度低**：超过两个维度数据不足，"
                "建议补充信息后重新评估。"
            )

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # 5. 评分卡结果
    # ------------------------------------------------------------------

    @staticmethod
    def _section_scorecard(data: AnalysisResult) -> str:
        sr = data.score_result
        lines = ["## 五、评分卡结果\n"]
        lines.append("| 维度 | 维度得分 | 满分 | 权重 | 加权得分 | 数据充分性 |")
        lines.append("| --- | --- | --- | --- | --- | --- |")

        for ds in sr.dimension_scores:
            label = _DIMENSION_LABELS.get(ds.dimension_name, ds.dimension_name)
            sufficiency = "数据不足" if ds.data_insufficient else "充分"
            lines.append(
                f"| {label} | {ds.raw_score:.1f} | {ds.max_score:.1f} "
                f"| {ds.weight:.0%} | {ds.weighted_score:.1f} | {sufficiency} |"
            )

        lines.append(f"| **综合得分** | | | | **{sr.total_score:.1f}** | |")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # 6. 联系方式
    # ------------------------------------------------------------------

    @staticmethod
    def _section_contact_info(data: AnalysisResult) -> str:
        contact = data.raw_data.contact_info
        if not contact:
            return ""

        lines = ["## 六、联系方式", ""]

        # 主要电话
        phone_fields = [
            ("official_phone", "总机/前台"),
            ("customer_service", "客服/400"),
            ("legal_dept_phone", "法务/知识产权部"),
            ("marketing_dept_phone", "品牌/市场部"),
            ("procurement_dept_phone", "采购部"),
            ("hr_phone", "人事/招聘"),
        ]
        has_contact = False
        for key, label in phone_fields:
            val = contact.get(key)
            if val and str(val).lower() not in ("null", "none", ""):
                lines.append(f"- {label}：{val}")
                has_contact = True

        # 其他电话
        others = contact.get("other_phones") or []
        if isinstance(others, list):
            for p in others:
                if p and str(p).lower() not in ("null", "none", ""):
                    lines.append(f"- 其他：{p}")
                    has_contact = True

        # 邮箱
        for key, label in [("email", "企业邮箱"), ("legal_email", "法务邮箱")]:
            val = contact.get(key)
            if val and str(val).lower() not in ("null", "none", ""):
                lines.append(f"- {label}：{val}")
                has_contact = True

        # 地址
        addr = contact.get("address")
        if addr and str(addr).lower() not in ("null", "none", ""):
            lines.append(f"- 办公地址：{addr}")
            has_contact = True

        # 联系人
        persons = contact.get("contact_persons") or []
        if isinstance(persons, list) and persons:
            has_contact = True
            lines.append("")
            lines.append("### 关键联系人")
            lines.append("")
            lines.append("| 姓名 | 职务 | 电话 | 来源 |")
            lines.append("| ---- | ---- | ---- | ---- |")
            for p in persons:
                if isinstance(p, dict):
                    name = p.get("name", "—")
                    title = p.get("title", "—")
                    phone = p.get("phone", "—")
                    src = p.get("source", "—")
                    lines.append(f"| {name} | {title} | {phone} | {src} |")

        if not has_contact:
            return ""

        # 来源
        src_url = contact.get("_source_url")
        if src_url and str(src_url).startswith("http"):
            lines.append(f"\n> 数据来源：{src_url}")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # 7. 销售沟通策略
    # ------------------------------------------------------------------

    @staticmethod
    def _section_strategy(data: AnalysisResult) -> str:
        s = data.strategy
        lines = ["## 七、销售沟通策略\n"]
        lines.append(f"- **沟通切入点**：{s.entry_point}")
        lines.append(f"- **风险提示话术方向**：{s.risk_talk_direction}")
        lines.append(f"- **目标接触角色**：{s.target_role}")
        lines.append(f"- **产品方案方向**：{s.product_direction}")

        if s.suggestions:
            lines.append("\n### 具体沟通建议\n")
            for i, sg in enumerate(s.suggestions, 1):
                lines.append(f"**建议 {i}：{sg.angle}**\n")
                lines.append(f"- 话术方向：{sg.talk_direction}")
                lines.append(f"- 预期效果：{sg.expected_effect}\n")

        if s.obstacle_note:
            lines.append(f"### 障碍因素与替代策略\n")
            lines.append(s.obstacle_note)

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # 8. 六维特征分析明细
    # ------------------------------------------------------------------

    def _section_six_dimensions(self, data: AnalysisResult) -> str:
        lines = ["## 八、六维特征分析明细\n"]

        # Map dimension key → DimensionFeatures
        dim_map: dict[str, DimensionFeatures] = {
            "basic_attributes": data.features.basic_attributes,
            "business_relevance": data.features.business_relevance,
            "public_behavior": data.features.public_behavior,
            "copyright_risk": data.features.copyright_risk,
            "tech_environment": data.features.tech_environment,
            "decision_chain": data.features.decision_chain,
            "marketing_intent": data.features.marketing_intent,
        }

        # Build a lookup: dimension_name → DimensionScore
        ds_map: dict[str, DimensionScore] = {
            ds.dimension_name: ds for ds in data.score_result.dimension_scores
        }

        for dim_key, dim_feat in dim_map.items():
            label = _DIMENSION_LABELS.get(dim_key, dim_key)
            ds = ds_map.get(dim_key)
            lines.append(f"### {label}\n")

            # Check data_insufficient
            if ds and ds.data_insufficient:
                channel = _SUPPLEMENT_CHANNELS.get(dim_key, "相关公开数据平台")
                lines.append(
                    f"> ⚠️ **数据不足**：该维度数据不足，评分结果仅供参考。"
                    f"建议通过以下渠道补充数据：{channel}\n"
                )

            if ds:
                lines.append(
                    f"维度得分：**{ds.raw_score:.1f}** / {ds.max_score:.1f}"
                    f"（权重 {ds.weight:.0%}，加权得分 {ds.weighted_score:.1f}）\n"
                )

            # Feature detail table
            lines.append("| 特征 | 原始值 | 状态 | 得分 | 满分 | 评分依据 |")
            lines.append("| --- | --- | --- | --- | --- | --- |")

            # Build feature score lookup
            fs_map = {}
            if ds:
                fs_map = {fs.feature_name: fs for fs in ds.feature_scores}

            for fv in dim_feat.features:
                status_label = _status_label(fv.status)
                raw_display = _format_raw_value(fv.raw_value)
                fs = fs_map.get(fv.name)
                score_str = f"{fs.score:.1f}" if fs else "—"
                max_str = f"{fs.max_score:.1f}" if fs else "—"
                reason = fs.scoring_reason if fs else "—"
                source_note = f"（来源: {fv.source}）" if fv.source else ""
                feat_label = _FEATURE_LABELS.get(fv.name, fv.name)
                lines.append(
                    f"| {feat_label} | {raw_display}{source_note} "
                    f"| {status_label} | {score_str} | {max_str} | {reason} |"
                )

            lines.append("")  # blank line after table

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # 9. 综合结论
    # ------------------------------------------------------------------

    @staticmethod
    def _section_conclusion(data: AnalysisResult) -> str:
        sr = data.score_result
        s = data.strategy
        lines = ["## 九、综合结论\n"]

        # Core value points
        value_points: list[str] = []
        for ds in sr.dimension_scores:
            if ds.max_score > 0 and (ds.raw_score / ds.max_score) >= 0.6:
                label = _DIMENSION_LABELS.get(ds.dimension_name, ds.dimension_name)
                value_points.append(f"{label}表现良好（{ds.raw_score:.1f}/{ds.max_score:.1f}）")

        if sr.top_factors:
            value_points.append(f"关键优势因素：{'、'.join(_FEATURE_LABELS.get(f, f) for f in sr.top_factors)}")

        # Risk points
        risk_points: list[str] = []
        for ds in sr.dimension_scores:
            if ds.data_insufficient:
                label = _DIMENSION_LABELS.get(ds.dimension_name, ds.dimension_name)
                risk_points.append(f"{label}数据不足")
            elif ds.max_score > 0 and (ds.raw_score / ds.max_score) < 0.3:
                label = _DIMENSION_LABELS.get(ds.dimension_name, ds.dimension_name)
                risk_points.append(f"{label}得分偏低（{ds.raw_score:.1f}/{ds.max_score:.1f}）")

        if sr.low_confidence:
            risk_points.append("整体数据置信度低，评估结果仅供参考")

        lines.append(f"**综合评价**：{data.raw_data.company_name}的成单可能性等级为"
                     f"**{sr.probability_level}**（综合得分 {sr.total_score:.1f}）。\n")

        if value_points:
            lines.append("**核心价值点**：\n")
            for vp in value_points:
                lines.append(f"- {vp}")
            lines.append("")

        if risk_points:
            lines.append("**主要风险点**：\n")
            for rp in risk_points:
                lines.append(f"- {rp}")
            lines.append("")

        if s.entry_point:
            lines.append(f"**建议切入方向**：{s.entry_point}")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # 10. 数据来源说明
    # ------------------------------------------------------------------

    @staticmethod
    def _section_data_sources(data: AnalysisResult) -> str:
        from urllib.parse import quote

        company = data.raw_data.company_name or data.raw_data.company_id
        encoded = quote(company)

        lines = ["## 十、数据来源说明\n"]

        # 构造可靠的标准化搜索链接（点击即可查看该企业信息）
        lines.append("### 快速查询链接\n")
        lines.append("| 平台 | 用途 | 链接 |")
        lines.append("| ---- | ---- | ---- |")

        reliable_links = [
            ("天眼查", "工商/财务/诉讼", f"https://www.tianyancha.com/search?key={encoded}"),
            ("企查查", "工商/财务/诉讼", f"https://www.qcc.com/web/search?key={encoded}"),
            ("爱企查", "工商信息", f"https://aiqicha.baidu.com/s?q={encoded}"),
            ("国家企信系统", "工商登记", f"https://www.gsxt.gov.cn/index.html"),
            ("百度百科", "企业概况", f"https://baike.baidu.com/search/word?word={encoded}"),
            ("裁判文书网", "诉讼记录", f"https://wenshu.court.gov.cn/website/wenshu/181029CR4M5A62CH/index.html?s8={encoded}"),
            ("百度搜索-招聘", "招聘信息", f"https://www.baidu.com/s?wd={encoded}+招聘"),
            ("百度搜索-字体侵权", "字体侵权", f"https://www.baidu.com/s?wd={encoded}+字体侵权"),
        ]

        for platform, usage, url in reliable_links:
            lines.append(f"| {platform} | {usage} | [点击查询]({url}) |")

        # Kimi 搜索过程中实际访问的页面（仅展示有效 URL）
        valid_sources = [
            s for s in data.raw_data.sources
            if s.url.startswith("http") and s.url != "kimi://web_search"
        ]
        if valid_sources:
            lines.append("\n### Kimi 搜索访问的页面\n")
            lines.append("| 数据段 | URL |")
            lines.append("| ------ | --- |")
            seen = set()
            for src in valid_sources:
                if src.url not in seen:
                    seen.add(src.url)
                    lines.append(f"| {src.field_name} | {src.url} |")
            lines.append("\n> 注：以上 URL 来自 Kimi 搜索过程，部分链接可能已失效，建议优先使用上方的快速查询链接。")

        collected_ts = _fmt_datetime(data.raw_data.collected_at)
        lines.append(f"\n数据采集时间：{collected_ts}")

        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fmt_datetime(dt: datetime) -> str:
    """Format a datetime for display."""
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _status_label(status: FeatureStatus) -> str:
    """Return a Chinese label for a FeatureStatus."""
    return {
        FeatureStatus.AVAILABLE: "已获取",
        FeatureStatus.UNAVAILABLE: "未获取",
        FeatureStatus.INFERRED: "推断值",
    }.get(status, str(status))


def _format_raw_value(value: object) -> str:
    """Format a raw feature value for display."""
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, float):
        if value == int(value):
            return str(int(value))
        return f"{value:.2f}"
    return str(value)
