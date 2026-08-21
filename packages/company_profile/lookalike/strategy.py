"""Sales strategy generator based on six-dimension features and scoring results."""

from __future__ import annotations

from .models import (
    CommunicationSuggestion,
    DimensionFeatures,
    FeatureStatus,
    FeatureValue,
    SalesStrategy,
    ScoreResult,
    SixDimensionFeatures,
)


def _get_feature(dim: DimensionFeatures, name: str) -> FeatureValue | None:
    """Return the FeatureValue with the given name, or None."""
    for fv in dim.features:
        if fv.name == name:
            return fv
    return None


def _raw(fv: FeatureValue | None, default=None):
    """Return raw_value if the feature is available/inferred, else *default*."""
    if fv is None or fv.status == FeatureStatus.UNAVAILABLE:
        return default
    return fv.raw_value


class StrategyGenerator:
    """Rule-based generator that produces a SalesStrategy from features and scores."""

    def generate(
        self, features: SixDimensionFeatures, score_result: ScoreResult
    ) -> SalesStrategy:
        entry_point = self._generate_entry_point(features)
        risk_talk_direction = self._generate_risk_talk_direction(features)
        target_role = self._generate_target_role(features)
        product_direction = self._generate_product_direction(features)
        suggestions = self._generate_suggestions(features, score_result)
        obstacle_note = self._generate_obstacle_note(features, score_result)

        return SalesStrategy(
            entry_point=entry_point,
            risk_talk_direction=risk_talk_direction,
            target_role=target_role,
            product_direction=product_direction,
            suggestions=suggestions,
            obstacle_note=obstacle_note,
        )

    # ------------------------------------------------------------------
    # 1. 沟通切入点
    # ------------------------------------------------------------------
    def _generate_entry_point(self, features: SixDimensionFeatures) -> str:
        biz = features.business_relevance
        basic = features.basic_attributes

        high_risk = _get_feature(biz, "high_risk_industry")
        design_pos = _get_feature(biz, "design_positions")
        industry_fv = _get_feature(basic, "industry_match")

        # Priority 1: high-risk industry
        if _raw(high_risk) == "true":
            industry_name = _raw(industry_fv, "相关")
            return (
                f"贵公司所在的{industry_name}行业是字体版权侵权高发领域，"
                "建议提前做好版权合规"
            )

        # Priority 2: design positions
        design_count = _raw(design_pos, 0)
        if isinstance(design_count, (int, float)) and design_count > 0:
            return (
                f"贵公司有{int(design_count)}个设计类岗位，"
                "字体是日常设计工作的核心工具"
            )

        # Default
        return "正版字体授权可以提升品牌专业形象，避免潜在法律风险"

    # ------------------------------------------------------------------
    # 2. 风险提示话术方向
    # ------------------------------------------------------------------
    def _generate_risk_talk_direction(self, features: SixDimensionFeatures) -> str:
        cr = features.copyright_risk

        font_lit = _get_feature(cr, "font_infringement_litigation")
        ip_lit = _get_feature(cr, "other_ip_litigation")

        # Priority 1: font infringement record
        if _raw(font_lit) == "true":
            return (
                "合规风险切入：贵公司有字体相关诉讼记录，"
                "建议尽快完成正版化以避免持续法律风险"
            )

        # Priority 2: other IP litigation
        ip_count = _raw(ip_lit, 0)
        if isinstance(ip_count, (int, float)) and ip_count > 0:
            return (
                "合规风险切入：贵公司有知识产权诉讼记录，"
                "字体版权是容易被忽视的合规盲区"
            )

        # Default: preventive
        return "预防性合规切入：提前购买正版字体授权，避免未来可能的侵权风险和赔偿"

    # ------------------------------------------------------------------
    # 3. 目标接触角色
    # ------------------------------------------------------------------
    def _generate_target_role(self, features: SixDimensionFeatures) -> str:
        dc = features.decision_chain

        gov = _get_feature(dc, "governance_structure")
        chain = _get_feature(dc, "decision_chain_length")

        gov_val = _raw(gov)
        chain_val = _raw(chain)

        if gov_val in ("foreign", "group_subsidiary"):
            return "法务部门或合规部门"

        if chain_val == "short":
            return "公司负责人或总经理"

        if chain_val == "medium":
            return "市场部门负责人或设计部门主管"

        # Default (long chain or unknown)
        return "市场部门或采购部门"

    # ------------------------------------------------------------------
    # 4. 产品方案方向
    # ------------------------------------------------------------------
    def _generate_product_direction(self, features: SixDimensionFeatures) -> str:
        tech = features.tech_environment
        pub = features.public_behavior

        has_app = _get_feature(tech, "has_app")
        has_game = _get_feature(tech, "has_game")
        ecommerce = _get_feature(pub, "ecommerce_presence")
        ad = _get_feature(pub, "ad_activity")

        if _raw(has_app) is True or _raw(has_game) is True:
            return "嵌入式字体授权方案（APP/游戏内嵌字体）"

        ecom_count = _raw(ecommerce, 0)
        if isinstance(ecom_count, (int, float)) and ecom_count > 0:
            return "电商商用字体授权方案"

        ad_val = _raw(ad, "none")
        if ad_val != "none":
            return "广告宣传字体授权方案"

        return "企业商用字体授权基础方案"

    # ------------------------------------------------------------------
    # 5. 沟通建议（至少 3 条）
    # ------------------------------------------------------------------
    def _generate_suggestions(
        self, features: SixDimensionFeatures, score_result: ScoreResult
    ) -> list[CommunicationSuggestion]:
        suggestions: list[CommunicationSuggestion] = []

        biz = features.business_relevance
        cr = features.copyright_risk
        tech = features.tech_environment
        pub = features.public_behavior
        dc = features.decision_chain
        basic = features.basic_attributes

        # --- Suggestion pool (ordered by signal strength) ---

        # Font infringement record → strongest signal
        font_lit = _get_feature(cr, "font_infringement_litigation")
        if _raw(font_lit) == "true":
            suggestions.append(
                CommunicationSuggestion(
                    angle="版权合规紧迫性",
                    talk_direction="以已有字体侵权诉讼记录为切入，强调正版化的紧迫性和法律风险",
                    expected_effect="引起客户对版权合规的重视，推动快速决策",
                )
            )

        # Other IP litigation
        ip_lit = _get_feature(cr, "other_ip_litigation")
        ip_count = _raw(ip_lit, 0)
        if isinstance(ip_count, (int, float)) and ip_count > 0:
            suggestions.append(
                CommunicationSuggestion(
                    angle="知识产权合规延伸",
                    talk_direction="从已有知识产权诉讼经验出发，提醒字体版权是常被忽视的合规盲区",
                    expected_effect="利用客户已有的合规意识，降低沟通门槛",
                )
            )

        # High-risk industry
        high_risk = _get_feature(biz, "high_risk_industry")
        if _raw(high_risk) == "true":
            industry_fv = _get_feature(basic, "industry_match")
            industry_name = _raw(industry_fv, "相关")
            suggestions.append(
                CommunicationSuggestion(
                    angle="行业风险提示",
                    talk_direction=f"{industry_name}行业是字体侵权高发领域，同行业已有大量侵权案例",
                    expected_effect="通过行业案例引发客户警觉，建立专业信任",
                )
            )

        # Design positions
        design_pos = _get_feature(biz, "design_positions")
        design_count = _raw(design_pos, 0)
        if isinstance(design_count, (int, float)) and design_count > 0:
            suggestions.append(
                CommunicationSuggestion(
                    angle="设计团队赋能",
                    talk_direction=f"贵公司有{int(design_count)}个设计岗位，正版字体库可以提升设计效率和作品质量",
                    expected_effect="从提升工作效率角度切入，获得设计团队支持",
                )
            )

        # APP / Game
        has_app = _get_feature(tech, "has_app")
        has_game = _get_feature(tech, "has_game")
        if _raw(has_app) is True or _raw(has_game) is True:
            suggestions.append(
                CommunicationSuggestion(
                    angle="嵌入式字体需求",
                    talk_direction="APP或游戏产品中使用的字体需要嵌入式授权，未授权使用存在法律风险",
                    expected_effect="明确产品中的字体授权需求，推动技术团队参与决策",
                )
            )

        # E-commerce presence
        ecommerce = _get_feature(pub, "ecommerce_presence")
        ecom_count = _raw(ecommerce, 0)
        if isinstance(ecom_count, (int, float)) and ecom_count > 0:
            suggestions.append(
                CommunicationSuggestion(
                    angle="电商视觉合规",
                    talk_direction="电商店铺的商品图片和详情页大量使用字体，是侵权投诉的高发区域",
                    expected_effect="结合电商运营痛点，推动电商团队关注字体版权",
                )
            )

        # Ad activity
        ad = _get_feature(pub, "ad_activity")
        ad_val = _raw(ad, "none")
        if ad_val != "none":
            suggestions.append(
                CommunicationSuggestion(
                    angle="广告投放合规",
                    talk_direction="广告素材中的字体使用需要商用授权，投放平台可能因版权问题下架广告",
                    expected_effect="从广告投放效率和风险角度切入，引起市场部门重视",
                )
            )

        # Governance / compliance awareness
        gov = _get_feature(dc, "governance_structure")
        gov_val = _raw(gov)
        if gov_val in ("foreign", "group_subsidiary"):
            suggestions.append(
                CommunicationSuggestion(
                    angle="企业合规体系",
                    talk_direction="外资或集团企业通常有严格的合规要求，字体版权是合规体系的重要组成部分",
                    expected_effect="对接法务或合规部门，利用企业内部合规驱动力推动采购",
                )
            )

        # Ensure at least 3 suggestions with defaults
        default_suggestions = [
            CommunicationSuggestion(
                angle="品牌形象提升",
                talk_direction="正版字体授权可以提升品牌专业形象，展现企业对知识产权的尊重",
                expected_effect="从品牌价值角度建立沟通基础，适用于品牌意识较强的企业",
            ),
            CommunicationSuggestion(
                angle="风险预防",
                talk_direction="字体版权侵权赔偿金额逐年上升，提前购买授权是最经济的风险防范方式",
                expected_effect="通过成本对比分析，帮助客户理解正版授权的性价比",
            ),
            CommunicationSuggestion(
                angle="行业趋势",
                talk_direction="越来越多的企业开始重视字体版权合规，正版化已成为行业趋势",
                expected_effect="利用从众心理和行业趋势，降低客户决策阻力",
            ),
        ]

        for ds in default_suggestions:
            if len(suggestions) >= 3:
                break
            # Avoid duplicate angles
            if not any(s.angle == ds.angle for s in suggestions):
                suggestions.append(ds)

        return suggestions

    # ------------------------------------------------------------------
    # 6. 障碍说明（仅低成单可能性时）
    # ------------------------------------------------------------------
    def _generate_obstacle_note(
        self, features: SixDimensionFeatures, score_result: ScoreResult
    ) -> str:
        if score_result.probability_level != "低":
            return ""

        obstacles: list[str] = []

        # Identify main obstacles from weak dimensions
        for ds in score_result.dimension_scores:
            if ds.data_insufficient:
                obstacles.append(f"{ds.dimension_name}维度数据不足，无法准确评估")
            elif ds.max_score > 0 and (ds.raw_score / ds.max_score) < 0.3:
                obstacles.append(f"{ds.dimension_name}维度得分偏低")

        if not obstacles:
            obstacles.append("综合评分较低，当前需求信号不明显")

        obstacle_text = "、".join(obstacles)

        return (
            f"主要障碍：{obstacle_text}。"
            "替代跟进策略：建议将该企业纳入长期培育名单，"
            "定期关注其业务变化和行业动态，"
            "在出现新的需求信号（如新产品发布、品牌升级、行业政策变化）时重新评估跟进时机。"
        )
