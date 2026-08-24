# -*- coding: utf-8 -*-
"""批量分析企业客户价值。

批跑结束后会在 output_dir 下生成「细项得分」CSV：
  batch_scores_detail.csv（UTF-8 BOM，便于 Excel 打开）
"""
import csv
import json
import os
import re
import sys
import time
import traceback
import uuid
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from dataclasses import asdict, dataclass, is_dataclass
from datetime import date, datetime
from pathlib import Path
from threading import Lock
from typing import Any

_PACKAGES_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_PACKAGES_DIR))

from company_profile.lookalike.collectors.kimi_collector import QwenCollector
from company_profile.lookalike.collectors.manager import CollectorManager
from company_profile.lookalike.collectors.marketing_channels import MarketingChannelsAdapter
from company_profile.lookalike.collectors.web_scraper import WebScraperAdapter
from config import get_qwen_config, load_secrets
from company_profile.lookalike.engine import AnalysisEngine, MultiMatchResult
from company_profile.lookalike.models import AnalysisResult
from company_profile.lookalike.report import (
    ReportGenerator,
    _DIMENSION_LABELS,
    _FEATURE_LABELS,
)
from company_profile.lookalike.scoring.engine import _DIMENSION_ATTR_MAP
from company_profile.lookalike.storage import FileStorage

_CONSOLE_LOCK = Lock()

# ============================================================
# ↓↓↓ 在这里配置每次运行的参数 ↓↓↓
# ============================================================

LOOP_TIMES = 3
MAX_PARALLEL_COMPANIES = 4
TIMING_CSV_NAME = "测试对比.csv"
TXT_PATH = "./测试对比.txt"  # 公司名单 txt 文件路径

# 基于 TXT_PATH 自动计算 output_dir 和 label
_txt_basename = os.path.splitext(os.path.basename(TXT_PATH))[0]

# 按照这个txt_path，最终output_dir会是：
# results/20260401待跟进客户-华东区
OUTPUT_DIR = os.path.join("results", _txt_basename)         # 结果输出目录
LABEL = _txt_basename                                               # 批次名称（用于报告标题）

# ============================================================

ROUND_LABEL_TO_COLUMN = {
    "1a-基础工商": "Collector-1a",
    "1b-财务治理": "Collector-1b",
    "2-招聘营销": "Collector-2",
    "3-诉讼技术": "Collector-3",
    "4-联系方式": "Collector-4",
}


def _console_print(*lines: str) -> None:
    """一次性输出一组日志，避免并发企业的多行信息相互穿插。"""
    with _CONSOLE_LOCK:
        print("\n".join(lines), flush=True)


@dataclass(frozen=True)
class RunTiming:
    report_id: str | None
    completed_at: str
    elapsed_seconds: float
    search_elapsed_seconds: float | None
    collect_elapsed_seconds: float | None
    other_non_collect_elapsed_seconds: float | None
    round_elapsed_seconds: dict[str, float]
    merge_elapsed_seconds: float | None
    diagnostics_path: str | None


@dataclass(frozen=True)
class CompanyAnalysisRecord:
    requested_name: str
    result: AnalysisResult | None
    runs: list[RunTiming]
    completed_at: str


class QwenCollectionProbe:
    """在批量脚本内记录 QwenCollector 的 search 和 collect 诊断信息。"""

    def __init__(self, collector: QwenCollector) -> None:
        self._collector = collector
        self._lock = Lock()
        self._active = False
        self._search_diagnostics: dict[str, Any] | None = None
        self._search_call_diagnostics: dict[str, Any] | None = None
        self._rounds: list[dict[str, Any]] = []
        self._merge_elapsed_seconds = 0.0
        self._last_diagnostics: dict[str, Any] | None = None

        self._original_search = collector.search
        self._original_collect = collector.collect
        self._original_collect_round = collector._collect_round
        self._original_merge_extracted = collector._merge_extracted
        self._original_call_qwen = collector._call_qwen

        collector.search = self._search
        collector.collect = self._collect
        collector._collect_round = self._collect_round
        collector._merge_extracted = self._merge_extracted
        collector._call_qwen = self._call_qwen

    def clear(self) -> None:
        """清空上一轮 search 和 collect 的诊断信息。"""
        with self._lock:
            self._search_diagnostics = None
            self._search_call_diagnostics = None
            self._last_diagnostics = None

    def snapshot(self) -> dict[str, Any] | None:
        """返回最近一次 search 和 collect 的诊断快照。"""
        with self._lock:
            diagnostics = self._last_diagnostics
            if diagnostics is None and self._search_diagnostics is not None:
                diagnostics = {
                    "company_id": self._search_diagnostics["company_name"],
                    "search": self._search_diagnostics,
                }
            return deepcopy(diagnostics)

    def _search(self, company_name: str):
        started_at = datetime.now()
        started_perf = time.perf_counter()
        with self._lock:
            self._search_call_diagnostics = None

        try:
            matches = self._original_search(company_name)
        except Exception as exc:
            self._finish_search(
                company_name=company_name,
                started_at=started_at,
                started_perf=started_perf,
                matches=None,
                error=f"{type(exc).__name__}: {exc}",
            )
            raise

        self._finish_search(
            company_name=company_name,
            started_at=started_at,
            started_perf=started_perf,
            matches=matches,
            error=None,
        )
        return matches

    def _call_qwen(
        self,
        prompt: str,
        company_id: str = "-",
        round_label: str = "-",
    ):
        result = self._original_call_qwen(
            prompt,
            company_id=company_id,
            round_label=round_label,
        )
        if round_label == "search":
            with self._lock:
                self._search_call_diagnostics = {
                    "attempts": result.attempts,
                    "error": result.error,
                    "payload": deepcopy(result.payload),
                }
        return result

    def _finish_search(
        self,
        company_name: str,
        started_at: datetime,
        started_perf: float,
        matches,
        error: str | None,
    ) -> None:
        completed_at = datetime.now()
        with self._lock:
            call_diagnostics = self._search_call_diagnostics or {}
            self._search_diagnostics = {
                "company_name": company_name,
                "started_at": started_at.isoformat(),
                "completed_at": completed_at.isoformat(),
                "elapsed_seconds": time.perf_counter() - started_perf,
                "attempts": call_diagnostics.get("attempts"),
                "error": call_diagnostics.get("error") or error,
                "payload": call_diagnostics.get("payload"),
                "matches": [
                    asdict(match) if is_dataclass(match) else str(match)
                    for match in (matches or [])
                ],
            }

    def _collect(self, company_id: str):
        started_at = datetime.now()
        started_perf = time.perf_counter()
        with self._lock:
            self._active = True
            self._rounds = []
            self._merge_elapsed_seconds = 0.0

        try:
            raw = self._original_collect(company_id)
        except Exception as exc:
            self._finish_collection(
                company_id=company_id,
                started_at=started_at,
                started_perf=started_perf,
                raw=None,
                error=f"{type(exc).__name__}: {exc}",
            )
            raise

        self._finish_collection(
            company_id=company_id,
            started_at=started_at,
            started_perf=started_perf,
            raw=raw,
            error=None,
        )
        return raw

    def _collect_round(
        self,
        label: str,
        prompt: str,
        company_id: str,
    ):
        started_perf = time.perf_counter()
        try:
            result = self._original_collect_round(label, prompt, company_id)
        except Exception as exc:
            self._append_round(
                {
                    "label": label,
                    "payload": None,
                    "attempts": 0,
                    "elapsed_seconds": time.perf_counter() - started_perf,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
            raise

        self._append_round(
            asdict(result) if is_dataclass(result) else dict(result)
        )
        return result

    def _merge_extracted(self, raw, extracted) -> None:
        started_perf = time.perf_counter()
        try:
            self._original_merge_extracted(raw, extracted)
        finally:
            elapsed_seconds = time.perf_counter() - started_perf
            with self._lock:
                if self._active:
                    self._merge_elapsed_seconds += elapsed_seconds

    def _append_round(self, round_result: dict[str, Any]) -> None:
        with self._lock:
            if self._active:
                self._rounds.append(deepcopy(round_result))

    def _finish_collection(
        self,
        company_id: str,
        started_at: datetime,
        started_perf: float,
        raw,
        error: str | None,
    ) -> None:
        with self._lock:
            self._active = False
            round_order = {
                label: index for index, label in enumerate(ROUND_LABEL_TO_COLUMN)
            }
            rounds = sorted(
                self._rounds,
                key=lambda item: round_order.get(str(item.get("label")), 999),
            )
            successful_rounds = sum(
                bool(round_result.get("payload")) for round_result in rounds
            )
            failed_rounds = len(rounds) - successful_rounds
            status = (
                "full_collection"
                if rounds and failed_rounds == 0
                else "partial_collection"
                if successful_rounds > 0
                else "collection_failed"
            )
            collect_diagnostics = {
                "started_at": started_at.isoformat(),
                "completed_at": datetime.now().isoformat(),
                "successful_rounds": successful_rounds,
                "failed_rounds": failed_rounds,
                "status": status,
                "error": error,
                "total_elapsed_seconds": time.perf_counter() - started_perf,
                "merge_elapsed_seconds": self._merge_elapsed_seconds,
                "rounds": rounds,
                "raw": asdict(raw) if raw is not None else None,
            }
            self._last_diagnostics = {
                "company_id": company_id,
                "search": self._search_diagnostics,
                **collect_diagnostics,
            }


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

def build_engine(
    output_dir: str,
    kimi_cfg: dict,
) -> tuple[AnalysisEngine, QwenCollectionProbe]:
    kimi_collector = QwenCollector(
        api_key=kimi_cfg["api_key"],
        base_url=kimi_cfg["base_url"],
        model=kimi_cfg["model"],
        timeout_seconds=kimi_cfg["timeout_seconds"],
    )
    qwen_probe = QwenCollectionProbe(kimi_collector)
    collectors = []
    if kimi_collector.is_configured():
        collectors.append(kimi_collector)

    collectors += [
        WebScraperAdapter(),
        MarketingChannelsAdapter(),
    ]

    return (
        AnalysisEngine(
            collector_manager=CollectorManager(collectors),
            storage=FileStorage(base_dir=output_dir),
            config_path=str(_COMPANY_PROFILE_DIR / "scoring_config.yaml"),
        ),
        qwen_probe,
    )


def _json_default(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    raise TypeError(f"无法序列化类型: {type(value).__name__}")


def _write_collection_diagnostics(
    output_dir: str,
    report_id: str,
    run_index: int,
    diagnostics: dict[str, Any],
) -> str:
    """将单次 Qwen collect 诊断数据写入对应报告目录。"""
    report_dir = Path(output_dir) / report_id
    report_dir.mkdir(parents=True, exist_ok=True)
    path = report_dir / "qwen_collection.json"
    payload = {
        "report_id": report_id,
        "run_index": run_index,
        **diagnostics,
    }
    path.write_text(
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
            default=_json_default,
        ),
        encoding="utf-8",
    )
    return str(path)


def _extract_round_elapsed_seconds(
    diagnostics: dict[str, Any] | None,
) -> dict[str, float]:
    if not diagnostics:
        return {}

    elapsed_by_column: dict[str, float] = {}
    collect_diagnostics = diagnostics.get("collect") or diagnostics
    for round_result in collect_diagnostics.get("rounds", []):
        if not isinstance(round_result, dict):
            continue
        column = ROUND_LABEL_TO_COLUMN.get(str(round_result.get("label", "")))
        elapsed = round_result.get("elapsed_seconds")
        if column and elapsed is not None:
            elapsed_by_column[column] = float(elapsed)
    return elapsed_by_column


# ============================================================
# Single company analysis (run 3 times, take max score per dimension)
# ============================================================

def analyze_one(
    company_name: str,
    output_dir: str,
    kimi_cfg: dict,
    company_index: int = 1,
    total_companies: int = 1,
) -> CompanyAnalysisRecord:
    """串行执行单家企业的多次分析，并返回最终结果及每次运行耗时。
    
    逻辑：
    1. 针对每个企业，调用 LOOP_TIMES 次收集的方法，按照维度评分
    2. 针对每个评分维度，取用 LOOP_TIMES 次收集评分的值，并统计每个维度的最高分
    3. 将所有维度的最高分加和，形成企业的最终报告
    """
    company_prefix = f"[企业 {company_index:02d}/{total_companies:02d}]"
    company_started_at = time.perf_counter()
    _console_print(f"{company_prefix} 开始：{company_name}")
    runs: list[RunTiming] = []
    try:
        engine, qwen_probe = build_engine(output_dir, kimi_cfg)
        results: list[AnalysisResult] = []
        for run_idx in range(LOOP_TIMES):
            run_prefix = (
                f"{company_prefix}[轮次 {run_idx + 1}/{LOOP_TIMES}]"
            )
            _console_print(f"{run_prefix} 开始")
            run_started_at = time.perf_counter()
            result: AnalysisResult | MultiMatchResult | None = None
            failure_logged = False
            qwen_probe.clear()
            try:
                result = engine.analyze(company_name)

                if isinstance(result, MultiMatchResult):
                    if not result.matches:
                        _console_print(f"{run_prefix} 失败：未找到匹配企业")
                        failure_logged = True
                        result = None
                    else:
                        result = engine.analyze_by_id(result.matches[0])

                if isinstance(result, AnalysisResult):
                    results.append(result)
                elif not failure_logged:
                    _console_print(f"{run_prefix} 失败：未生成分析结果")
            except Exception:
                _console_print(
                    f"{run_prefix} 异常：\n{traceback.format_exc().rstrip()}"
                )
                result = None

            elapsed_seconds = time.perf_counter() - run_started_at
            diagnostics = qwen_probe.snapshot()
            round_elapsed_seconds = _extract_round_elapsed_seconds(diagnostics)
            search_diagnostics = (
                diagnostics.get("search") if diagnostics else None
            )
            collect_diagnostics = (
                diagnostics.get("collect") or diagnostics
                if diagnostics
                else None
            )
            search_elapsed_seconds = (
                float(search_diagnostics["elapsed_seconds"])
                if search_diagnostics
                and search_diagnostics.get("elapsed_seconds") is not None
                else None
            )
            collect_elapsed_seconds = (
                float(collect_diagnostics["total_elapsed_seconds"])
                if collect_diagnostics
                and collect_diagnostics.get("total_elapsed_seconds") is not None
                else None
            )
            measured_qwen_seconds = (
                (search_elapsed_seconds or 0.0)
                + (collect_elapsed_seconds or 0.0)
            )
            other_non_collect_elapsed_seconds = max(
                0.0,
                elapsed_seconds - measured_qwen_seconds,
            )
            if diagnostics is not None:
                diagnostics["run_timing"] = {
                    "total_elapsed_seconds": elapsed_seconds,
                    "search_elapsed_seconds": search_elapsed_seconds,
                    "collect_elapsed_seconds": collect_elapsed_seconds,
                    "other_non_collect_elapsed_seconds": (
                        other_non_collect_elapsed_seconds
                    ),
                }

            diagnostics_path = None
            if isinstance(result, AnalysisResult) and diagnostics is not None:
                diagnostics_path = _write_collection_diagnostics(
                    output_dir=output_dir,
                    report_id=result.report_id,
                    run_index=run_idx + 1,
                    diagnostics=diagnostics,
                )

            merge_elapsed_seconds = (
                float(collect_diagnostics["merge_elapsed_seconds"])
                if collect_diagnostics
                and collect_diagnostics.get("merge_elapsed_seconds") is not None
                else None
            )
            runs.append(
                RunTiming(
                    report_id=result.report_id
                    if isinstance(result, AnalysisResult)
                    else None,
                    completed_at=datetime.now().strftime("%H:%M:%S"),
                    elapsed_seconds=elapsed_seconds,
                    search_elapsed_seconds=search_elapsed_seconds,
                    collect_elapsed_seconds=collect_elapsed_seconds,
                    other_non_collect_elapsed_seconds=(
                        other_non_collect_elapsed_seconds
                    ),
                    round_elapsed_seconds=round_elapsed_seconds,
                    merge_elapsed_seconds=merge_elapsed_seconds,
                    diagnostics_path=diagnostics_path,
                )
            )
            if isinstance(result, AnalysisResult):
                _console_print(
                    f"{run_prefix} 完成"
                    f" | 得分={result.score_result.total_score:.1f}"
                    f" | 总耗时={_format_duration(elapsed_seconds)}"
                    f" | search={_format_duration(search_elapsed_seconds)}"
                    f" | collect={_format_duration(collect_elapsed_seconds)}"
                    f" | 其他={_format_duration(other_non_collect_elapsed_seconds)}"
                )

        if not results:
            _console_print(
                f"{company_prefix} 失败：{LOOP_TIMES} 次分析均未成功"
                f" | 企业耗时="
                f"{_format_duration(time.perf_counter() - company_started_at)}"
                f" | {company_name}"
            )
            return CompanyAnalysisRecord(
                requested_name=company_name,
                result=None,
                runs=runs,
                completed_at=datetime.now().strftime("%H:%M:%S"),
            )

        # 取每个维度的最高分，合成最终结果
        final_result = _merge_max_scores(results, company_name)
        
        # 生成新的 report_id
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        short_uuid = uuid.uuid4().hex[:8]
        safe_name = re.sub(r'[\\/:*?"<>|（）\(\)\s]+', '_', company_name)
        safe_name = safe_name.strip('_')
        if len(safe_name) > 50:
            safe_name = safe_name[:50]
        final_result.report_id = f"{safe_name}_{ts}_{short_uuid}"

        sr = final_result.score_result

        reporter = ReportGenerator()
        md = reporter.generate_markdown(final_result)
        os.makedirs(output_dir, exist_ok=True)
        md_path = os.path.join(output_dir, f"{final_result.report_id}_report.md")
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(md)
        
        # 保存 score.json
        score_json_path = os.path.join(output_dir, f"{final_result.report_id}_score.json")
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
        _console_print(
            f"{company_prefix} 汇总完成：{company_name}"
            f" | 综合得分={sr.total_score:.1f}"
            f" | 等级={sr.probability_level}"
            f" | 企业耗时="
            f"{_format_duration(time.perf_counter() - company_started_at)}",
            f"{company_prefix} 报告：{md_path}",
        )
        
        return CompanyAnalysisRecord(
            requested_name=company_name,
            result=final_result,
            runs=runs,
            completed_at=datetime.now().strftime("%H:%M:%S"),
        )

    except Exception:
        _console_print(
            f"{company_prefix} 企业任务异常：{company_name}\n"
            f"{traceback.format_exc().rstrip()}"
        )
        return CompanyAnalysisRecord(
            requested_name=company_name,
            result=None,
            runs=runs,
            completed_at=datetime.now().strftime("%H:%M:%S"),
        )


def _merge_max_scores(results: list[AnalysisResult], company_name: str) -> AnalysisResult:
    """将多次分析的结果合并，取每个维度的最高分。"""
    from company_profile.lookalike.models import (
        DimensionScore,
        FeatureScore,
        SalesStrategy,
        ScoreResult,
    )
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


def _format_duration(seconds: float | None, *, include_hours: bool = False) -> str:
    if seconds is None:
        return "N/A"

    total_seconds = max(0, int(round(seconds)))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if include_hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{hours * 60 + minutes}:{secs:02d}"


def _join_run_values(
    runs: list[RunTiming],
    value_getter,
) -> str:
    values = [value_getter(run) for run in runs]
    if len(values) < LOOP_TIMES:
        values.extend(["N/A"] * (LOOP_TIMES - len(values)))
    return "|".join(values)


def write_timing_csv(
    records: list[CompanyAnalysisRecord],
    output_dir: str,
) -> str:
    """按对比时间.csv格式记录公司、run 和 collector 各阶段耗时。"""
    os.makedirs(output_dir, exist_ok=True)
    path = os.path.join(output_dir, TIMING_CSV_NAME)
    headers = [
        "序号",
        "汇总报告输出时间",
        "三次运行所花费的平均时间",
        "三次运行报告结果各自的输出时间",
        "本公司三次运行报告每份报告所花费的时间",
        "公司名称",
        "Collector-Search",
        "Collector-Collect总耗时",
        "Collector-其他非collect开销",
        "Collector-1a",
        "Collector-1b",
        "Collector-2",
        "Collector-3",
        "Collector-4",
        "Collector-信息统合",
    ]

    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(headers)
        for index, record in enumerate(records, start=1):
            successful_runs = [
                run for run in record.runs if run.report_id is not None
            ]
            average_seconds = (
                sum(run.elapsed_seconds for run in successful_runs)
                / len(successful_runs)
                if successful_runs
                else None
            )

            row = [
                f"{index:02d}",
                record.completed_at,
                _format_duration(average_seconds, include_hours=True)
                if average_seconds is not None
                else "",
                _join_run_values(
                    record.runs,
                    lambda run: run.completed_at
                    if run.report_id is not None
                    else "N/A",
                ),
                _join_run_values(
                    record.runs,
                    lambda run: _format_duration(run.elapsed_seconds)
                    if run.report_id is not None
                    else "N/A",
                ),
                record.requested_name,
                _join_run_values(
                    record.runs,
                    lambda run: _format_duration(run.search_elapsed_seconds),
                ),
                _join_run_values(
                    record.runs,
                    lambda run: _format_duration(run.collect_elapsed_seconds),
                ),
                _join_run_values(
                    record.runs,
                    lambda run: _format_duration(
                        run.other_non_collect_elapsed_seconds
                    ),
                ),
            ]

            for column in ROUND_LABEL_TO_COLUMN.values():
                row.append(
                    _join_run_values(
                        record.runs,
                        lambda run, column=column: _format_duration(
                            run.round_elapsed_seconds.get(column)
                        ),
                    )
                )
            row.append(
                _join_run_values(
                    record.runs,
                    lambda run: _format_duration(run.merge_elapsed_seconds),
                )
            )
            writer.writerow(row)

    print(f"✓ 耗时对比 CSV：{path}")
    return path


# ============================================================
# Batch runner
# ============================================================

def run_batch(companies: list[str], output_dir: str, kimi_cfg: dict, label: str):
    total = len(companies)
    max_workers = min(MAX_PARALLEL_COMPANIES, total)
    batch_started_at = time.perf_counter()

    _console_print(
        "",
        "=" * 72,
        f"批次【{label}】开始"
        f" | 企业={total}"
        f" | 并发={max_workers}"
        f" | 每家轮次={LOOP_TIMES}",
        f"输出目录：{output_dir}",
        "=" * 72,
    )

    records_by_index: dict[int, CompanyAnalysisRecord] = {}
    with ThreadPoolExecutor(
        max_workers=max_workers,
        thread_name_prefix="company-profile-batch",
    ) as executor:
        futures = {
            executor.submit(
                analyze_one,
                company,
                output_dir,
                kimi_cfg,
                index + 1,
                total,
            ): (index, company)
            for index, company in enumerate(companies)
        }
        for future in as_completed(futures):
            index, company = futures[future]
            try:
                record = future.result()
            except Exception:
                _console_print(
                    f"[企业 {index + 1:02d}/{total:02d}] 任务异常：{company}\n"
                    f"{traceback.format_exc().rstrip()}"
                )
                record = CompanyAnalysisRecord(
                    requested_name=company,
                    result=None,
                    runs=[],
                    completed_at=datetime.now().strftime("%H:%M:%S"),
                )
            records_by_index[index] = record
            completed = len(records_by_index)
            succeeded = sum(
                item.result is not None for item in records_by_index.values()
            )
            failed_count = completed - succeeded
            percent = completed / total * 100
            _console_print(
                f"[总进度 {completed:02d}/{total:02d} {percent:5.1f}%]"
                f" 已完成：{company}"
                f" | 成功={succeeded}"
                f" | 失败={failed_count}"
                f" | 批次耗时="
                f"{_format_duration(time.perf_counter() - batch_started_at)}"
            )

    records = [records_by_index[index] for index in range(total)]
    score_rows = [(record.requested_name, record.result) for record in records]
    write_batch_scores_csv(score_rows, output_dir)
    write_timing_csv(records, output_dir)

    success = sum(record.result is not None for record in records)
    failed = [
        record.requested_name for record in records if record.result is None
    ]

    summary_lines = [
        "",
        "=" * 72,
        f"批次【{label}】完成"
        f" | 成功={success}/{total}"
        f" | 失败={len(failed)}"
        f" | 总耗时={_format_duration(time.perf_counter() - batch_started_at)}",
        f"细项得分：{os.path.join(output_dir, 'batch_scores_detail.csv')}",
        f"耗时对比：{os.path.join(output_dir, TIMING_CSV_NAME)}",
    ]
    if failed:
        summary_lines.append(f"失败企业：{'、'.join(failed)}")
    summary_lines.extend(["=" * 72, ""])
    _console_print(*summary_lines)


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
