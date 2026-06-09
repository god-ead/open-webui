# -*- coding: utf-8 -*-
"""批量分析企业客户价值。

用法：
  python3 run_batch.py <companies.txt> <output_dir> [label]

  companies.txt  每行一个公司名称，空行和 # 开头的行自动跳过
  output_dir     结果输出目录（自动创建）
  label          批次名称，默认取 txt 文件名（不含扩展名）

示例：
  python3 run_batch.py data/fawu0325.txt results/fawu0325 法务0325

批跑结束后会在 output_dir 下生成「细项得分」CSV：
  batch_scores_detail.csv（UTF-8 BOM，便于 Excel 打开）
"""
import csv
import os
import sys
import time
import traceback
from collections import defaultdict
from pathlib import Path

_COMPANY_PROFILE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_COMPANY_PROFILE_DIR))

from lookalike.collectors.kimi_collector import QwenCollector
from lookalike.collectors.manager import CollectorManager
from lookalike.collectors.marketing_channels import MarketingChannelsAdapter
from lookalike.collectors.web_scraper import WebScraperAdapter
from config import get_qwen_config, load_secrets
from lookalike.engine import AnalysisEngine, MultiMatchResult
from lookalike.models import AnalysisResult
from lookalike.report import (
    ReportGenerator,
    _DIMENSION_LABELS,
    _FEATURE_LABELS,
)
from lookalike.scoring.engine import _DIMENSION_ATTR_MAP
from lookalike.storage import FileStorage

SLEEP_BETWEEN = 10  # seconds between each company
LOOP_TIMES = 2      # 每家公司循环 n 次

# ============================================================
# ↓↓↓ 在这里配置每次运行的参数 ↓↓↓
# ============================================================
TXT_PATH = "./company_name.txt"  # 公司名单 txt 文件路径

# 基于 TXT_PATH 自动计算 output_dir 和 label
_txt_basename = os.path.splitext(os.path.basename(TXT_PATH))[0]

# 按照这个txt_path，最终output_dir会是：
# results/20260401待跟进客户-华东区
OUTPUT_DIR = os.path.join("results", _txt_basename)         # 结果输出目录
LABEL = _txt_basename                                               # 批次名称（用于报告标题）

# ============================================================


# ============================================================
# Load companies from txt file
# ============================================================

def load_companies(txt_path: str) -> list[str]:
    """每行一个公司名，跳过空行和 # 注释行。"""
    if not os.path.isfile(txt_path):
        print(f"✗ 文件不存在：{txt_path}")
        sys.exit(1)
    companies = []
    with open(txt_path, encoding="utf-8") as f:
        for line in f:
            name = line.strip()
            if name and not name.startswith("#"):
                companies.append(name)
    if not companies:
        print(f"✗ 文件中未找到任何公司名称：{txt_path}")
        sys.exit(1)
    return companies


# ============================================================
# Engine builder
# ============================================================

def build_engine(output_dir: str, kimi_cfg: dict) -> AnalysisEngine:
    kimi_collector = QwenCollector(
        api_key=kimi_cfg["api_key"],
        base_url=kimi_cfg["base_url"],
        model=kimi_cfg["model"],
    )
    collectors = []
    if kimi_collector.is_configured():
        collectors.append(kimi_collector)
        print("✓ Qwen 已配置，将作为主力数据源")

    collectors += [
        WebScraperAdapter(),
        MarketingChannelsAdapter(),
    ]

    return AnalysisEngine(
        collector_manager=CollectorManager(collectors),
        storage=FileStorage(base_dir=output_dir),
        config_path=str(_COMPANY_PROFILE_DIR / "scoring_config.yaml"),
    )


# ============================================================
# Single company analysis (run 3 times, take max score per dimension)
# ============================================================

def analyze_one(
    engine: AnalysisEngine, company_name: str, output_dir: str
) -> AnalysisResult | None:
    """分析单家企业并保存报告；成功返回 AnalysisResult，否则返回 None。
    
    逻辑：
    1. 针对每个企业，调用 LOOP_TIMES 次收集的方法，按照维度评分
    2. 针对每个评分维度，取用 LOOP_TIMES 次收集评分的值，并统计每个维度的最高分
    3. 将所有维度的最高分加和，形成企业的最终报告
    """
    print(f"\n  → 分析：{company_name}")
    try:
        # 运行三次分析
        results: list[AnalysisResult] = []
        for run_idx in range(LOOP_TIMES):
            print(f"    第 {run_idx + 1}/{LOOP_TIMES} 次分析...")
            result = engine.analyze(company_name)

            if isinstance(result, MultiMatchResult):
                if not result.matches:
                    print(f"    ✗ 第{run_idx + 1}次：未找到匹配企业")
                    continue
                result = engine.analyze_by_id(result.matches[0])

            if isinstance(result, AnalysisResult):
                results.append(result)
                print(f"    ✓ 第{run_idx + 1}次：得分 {result.score_result.total_score:.1f}")
            else:
                print(f"    ✗ 第{run_idx + 1}次：分析失败")

        if not results:
            print(f"  ✗ 三次分析均失败：{company_name}")
            return None

        # 取每个维度的最高分，合成最终结果
        final_result = _merge_max_scores(results, company_name)
        
        # 生成新的 report_id
        import uuid
        from datetime import datetime
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        short_uuid = uuid.uuid4().hex[:8]
        import re
        safe_name = re.sub(r'[\\/:*?"<>|（）\(\)\s]+', '_', company_name)
        safe_name = safe_name.strip('_')
        if len(safe_name) > 50:
            safe_name = safe_name[:50]
        final_result.report_id = f"{safe_name}_{ts}_{short_uuid}"

        sr = final_result.score_result
        print(f"\n{'='*50}")
        print(f"  企业名称：{final_result.raw_data.company_name}")
        print(f"  报告编号：{final_result.report_id}")
        print(f"  综合得分：{sr.total_score:.1f} / 100")
        print(f"  成单可能性：{sr.probability_level}")
        if sr.top_factors:
            print(f"  关键因素：{'、'.join(sr.top_factors)}")
        if sr.follow_up_suggestion:
            print(f"  跟进建议：{sr.follow_up_suggestion}")
        print(f"{'='*50}\n")

        reporter = ReportGenerator()
        md = reporter.generate_markdown(final_result)
        os.makedirs(output_dir, exist_ok=True)
        md_path = os.path.join(output_dir, f"{final_result.report_id}_report.md")
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(md)
        print(f"  ✓ 报告已保存：{md_path}")
        
        # 保存 score.json
        score_json_path = os.path.join(output_dir, f"{final_result.report_id}_score.json")
        import json
        score_data = {
            "company_name": final_result.raw_data.company_name,
            "report_id": final_result.report_id,
            "total_score": sr.total_score,
            "probability_level": sr.probability_level,
            "dimension_scores": [
                {
                    "dimension_name": ds.dimension_name,
                    "raw_score": ds.raw_score,
                    "max_score": ds.max_score,
                    "weight": ds.weight,
                    "weighted_score": ds.weighted_score,
                    "feature_scores": [
                        {
                            "feature_name": fs.feature_name,
                            "score": fs.score,
                            "max_score": fs.max_score,
                        }
                        for fs in ds.feature_scores
                    ]
                }
                for ds in sr.dimension_scores
            ]
        }
        with open(score_json_path, "w", encoding="utf-8") as f:
            json.dump(score_data, f, ensure_ascii=False, indent=2)
        print(f"  ✓ 分数已保存：{score_json_path}")
        
        return final_result

    except Exception:
        print(f"  ✗ 异常：{company_name}")
        traceback.print_exc()
        return None


def _merge_max_scores(results: list[AnalysisResult], company_name: str) -> AnalysisResult:
    """将多次分析的结果合并，取每个维度的最高分。"""
    from lookalike.models import ScoreResult, DimensionScore, FeatureScore, SalesStrategy
    from datetime import datetime
    
    if len(results) == 1:
        return results[0]
    
    # 收集所有维度的分数
    dimension_scores_map: dict[str, list[DimensionScore]] = {}
    for result in results:
        for ds in result.score_result.dimension_scores:
            dim_name = ds.dimension_name
            if dim_name not in dimension_scores_map:
                dimension_scores_map[dim_name] = []
            dimension_scores_map[dim_name].append(ds)
    
    # 取每个维度的最高 raw_score
    merged_dimension_scores: list[DimensionScore] = []
    for dim_name, dim_scores in dimension_scores_map.items():
        # 找到最高分的那次
        best_ds = max(dim_scores, key=lambda x: x.raw_score)
        
        # 收集该维度所有特征分数
        feature_scores_map: dict[str, list[FeatureScore]] = {}
        for ds in dim_scores:
            for fs in ds.feature_scores:
                feat_name = fs.feature_name
                if feat_name not in feature_scores_map:
                    feature_scores_map[feat_name] = []
                feature_scores_map[feat_name].append(fs)
        
        # 取每个特征的最高分
        merged_feature_scores: list[FeatureScore] = []
        for feat_name, feat_scores in feature_scores_map.items():
            best_fs = max(feat_scores, key=lambda x: x.score)
            merged_feature_scores.append(FeatureScore(
                feature_name=best_fs.feature_name,
                score=best_fs.score,
                max_score=best_fs.max_score,
                scoring_reason=best_fs.scoring_reason,
            ))
        
        merged_dimension_scores.append(DimensionScore(
            dimension_name=best_ds.dimension_name,
            raw_score=best_ds.raw_score,
            max_score=best_ds.max_score,
            weight=best_ds.weight,
            weighted_score=best_ds.weighted_score,
            feature_scores=merged_feature_scores,
            data_insufficient=best_ds.data_insufficient,
        ))
    
    # 计算总分（所有维度最高分之和）
    total_score = sum(ds.raw_score for ds in merged_dimension_scores)
    
    # 计算加权分数
    for ds in merged_dimension_scores:
        ds.weighted_score = ds.raw_score * ds.weight
    
    # 确定成单可能性等级
    probability_level = _get_probability_level(total_score)
    
    # 获取关键因素（从最高分的那次结果中获取）
    top_factors = results[0].score_result.top_factors
    follow_up_suggestion = results[0].score_result.follow_up_suggestion
    
    # 使用第一次分析的 raw_data 和 features
    final_score_result = ScoreResult(
        dimension_scores=merged_dimension_scores,
        total_score=total_score,
        probability_level=probability_level,
        top_factors=top_factors,
        follow_up_suggestion=follow_up_suggestion,
    )
    
    return AnalysisResult(
        report_id="",
        raw_data=results[0].raw_data,
        features=results[0].features,
        score_result=final_score_result,
        strategy=results[0].strategy,
        analyzed_at=datetime.now(),
    )


def _get_probability_level(total_score: float) -> str:
    """根据总分确定成单可能性等级。"""
    if total_score >= 80:
        return "高（S级客户）"
    elif total_score >= 65:
        return "中高（A级客户）"
    elif total_score >= 50:
        return "中（B级客户）"
    else:
        return "低（C级客户）"


# ============================================================
# CSV：综合分 + 各细项（特征）得分
# ============================================================

def _column_label(dim_key: str, feat_key: str) -> str:
    d = _DIMENSION_LABELS.get(dim_key, dim_key)
    f = _FEATURE_LABELS.get(feat_key, feat_key)
    return f"{d}/{f}"


def _collect_feature_columns(
    rows: list[tuple[str, AnalysisResult | None]],
) -> list[tuple[str, str]]:
    """按评分引擎维度顺序，每维内特征名排序，得到稳定 CSV 列 (dim, feat)。"""
    present: dict[str, set[str]] = defaultdict(set)
    for _, r in rows:
        if r is None:
            continue
        for ds in r.score_result.dimension_scores:
            for fs in ds.feature_scores:
                present[ds.dimension_name].add(fs.feature_name)
    out: list[tuple[str, str]] = []
    for dim in _DIMENSION_ATTR_MAP:
        if dim not in present:
            continue
        for feat in sorted(present[dim]):
            out.append((dim, feat))
    return out


def write_batch_scores_csv(
    rows: list[tuple[str, AnalysisResult | None]],
    output_dir: str,
) -> str:
    """一行一家企业：公司名称、综合得分、成单等级、各特征得分。失败行细项留空。"""
    os.makedirs(output_dir, exist_ok=True)
    feat_cols = _collect_feature_columns(rows)
    path = os.path.join(output_dir, "batch_scores_detail.csv")
    if not feat_cols:
        print("  ⚠ 无成功样本，CSV 仅含公司名与空得分列")

    headers = (
        ["公司名称", "综合得分", "成单可能性"]
        + [_column_label(d, f) for d, f in feat_cols]
    )

    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(headers)
        for requested_name, r in rows:
            if r is None:
                w.writerow([requested_name, "", ""] + [""] * len(feat_cols))
                continue
            sr = r.score_result
            feat_map: dict[tuple[str, str], float] = {}
            for ds in sr.dimension_scores:
                for fs in ds.feature_scores:
                    feat_map[(ds.dimension_name, fs.feature_name)] = round(
                        float(fs.score), 4
                    )
            body = [feat_map.get((d, f), "") for d, f in feat_cols]
            w.writerow(
                [
                    r.raw_data.company_name,
                    round(float(sr.total_score), 4),
                    sr.probability_level,
                ]
                + body
            )

    print(f"✓ 细项得分 CSV：{path}")
    return path


# ============================================================
# Batch runner
# ============================================================

def run_batch(companies: list[str], output_dir: str, kimi_cfg: dict, label: str):
    total = len(companies)
    success = 0
    failed = []
    recorded: list[tuple[str, AnalysisResult | None]] = []

    print(f"\n{'='*60}")
    print(f"  开始批量分析【{label}】共 {total} 家企业")
    print(f"  输出目录：{output_dir}")
    print(f"  每家间隔：{SLEEP_BETWEEN}s")
    print(f"{'='*60}")

    engine = build_engine(output_dir, kimi_cfg)

    for i, company in enumerate(companies, 1):
        print(f"\n[{i}/{total}]", end="")
        res = analyze_one(engine, company, output_dir)
        recorded.append((company, res))
        if res is not None:
            success += 1
        else:
            failed.append(company)

        if i < total:
            print(f"  ⏳ 等待 {SLEEP_BETWEEN}s ...")
            time.sleep(SLEEP_BETWEEN)

    write_batch_scores_csv(recorded, output_dir)

    print(f"\n{'='*60}")
    print(f"  【{label}】完成：{success}/{total} 成功")
    if failed:
        print("  失败列表：")
        for name in failed:
            print(f"    - {name}")
    print(f"{'='*60}\n")


# ============================================================
# Main
# ============================================================

def main():
    txt_path   = TXT_PATH
    output_dir = OUTPUT_DIR
    label      = LABEL

    companies = load_companies(txt_path)
    print(f"✓ 读取到 {len(companies)} 家企业：{txt_path}")

    secrets = load_secrets()
    kimi_cfg = get_qwen_config(secrets)
    if not kimi_cfg.get("api_key"):
        print("✗ 未配置 Qwen API Key，请检查 config/secrets.yaml")
        sys.exit(1)
    print(f"✓ Qwen 已配置，模型：{kimi_cfg.get('model') or 'qwen3.5-plus'}")

    run_batch(companies, output_dir, kimi_cfg, label)


if __name__ == "__main__":
    main()
