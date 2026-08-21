from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


SCRIPT_DIR = Path(__file__).resolve().parent
INPUT_PATH = SCRIPT_DIR / "测试对比.csv"    # 设置为 run_batch.py 输出的 *.csv

METRICS = {
    "本公司三次运行报告每份报告所花费的时间": {
        "name": "每份报告生成耗时",
        "slug": "report",
    },
    "Collector-Search": {
        "name": "每份报告Collector-Search阶段耗时",
        "slug": "collector_search",
    },
    "Collector-Collect总耗时": {
        "name": "每份报告Collector-Collect阶段总耗时",
        "slug": "collector_collect",
    },
}


def parse_duration_to_seconds(value):
    """
    将耗时字符串转换为秒。
    支持 m:s、h:m:s；空值和 N/A 返回 None。
    """
    if pd.isna(value):
        return None

    value = str(value).strip()
    if not value or value.upper() == "N/A":
        return None

    parts = value.split(":")

    try:
        if len(parts) == 2:
            minutes, seconds = map(int, parts)
            return minutes * 60 + seconds

        if len(parts) == 3:
            hours, minutes, seconds = map(int, parts)
            return hours * 3600 + minutes * 60 + seconds
    except ValueError:
        return None

    return None


def format_seconds(seconds):
    """将秒转换为 m:ss 格式。"""
    if pd.isna(seconds):
        return ""

    seconds = int(round(seconds))
    minutes = seconds // 60
    remain_seconds = seconds % 60
    return f"{minutes}:{remain_seconds:02d}"


def load_metrics(path):
    """将三项以 | 分隔的运行耗时展开为逐报告明细。"""
    source_df = pd.read_csv(path, encoding="utf-8-sig")
    records = []

    missing_columns = [
        column
        for column in ["公司名称", *METRICS]
        if column not in source_df.columns
    ]
    if missing_columns:
        raise ValueError(f"CSV 缺少必要字段：{', '.join(missing_columns)}")

    for _, row in source_df.iterrows():
        for column, metric in METRICS.items():
            if pd.isna(row[column]):
                continue

            for run_idx, item in enumerate(str(row[column]).split("|"), start=1):
                seconds = parse_duration_to_seconds(item)
                if seconds is None:
                    continue

                records.append(
                    {
                        "指标": metric["name"],
                        "公司名称": row["公司名称"],
                        "运行次数": run_idx,
                        "原始耗时": item.strip(),
                        "秒": seconds,
                        "分钟": seconds / 60,
                    }
                )

    return pd.DataFrame(records)


def calc_stats(df, metric_name):
    """计算指定指标的统计量。"""
    seconds = df["秒"]

    return {
        "指标": metric_name,
        "样本数": len(seconds),
        "最小值": format_seconds(seconds.min()),
        "P25": format_seconds(seconds.quantile(0.25)),
        "中位数": format_seconds(seconds.median()),
        "P75": format_seconds(seconds.quantile(0.75)),
        "最大值": format_seconds(seconds.max()),
        "平均值": format_seconds(seconds.mean()),
        "标准差": format_seconds(seconds.std()),
        "P90": format_seconds(seconds.quantile(0.90)),
        "P95": format_seconds(seconds.quantile(0.95)),
    }


def build_distribution_table(df):
    """按 30 秒区间统计各指标的报告数量。"""
    records = []

    for metric_name, metric_df in df.groupby("指标", sort=False):
        max_seconds = metric_df["秒"].max()
        upper_bound = max(30, int(np.ceil(max_seconds / 30) * 30))
        bins = np.arange(0, upper_bound + 30, 30)
        if max_seconds == upper_bound:
            bins = np.append(bins, upper_bound + 30)
        labels = [
            f"{format_seconds(bins[index])}-{format_seconds(bins[index + 1])}"
            for index in range(len(bins) - 1)
        ]
        counts = pd.cut(
            metric_df["秒"],
            bins=bins,
            labels=labels,
            right=False,
            include_lowest=True,
        ).value_counts(sort=False)

        for duration_range, count in counts.items():
            records.append(
                {
                    "指标": metric_name,
                    "耗时区间": duration_range,
                    "报告数量": count,
                }
            )

    return pd.DataFrame(records)


def plot_histograms(df, output_path):
    """在同一张图中绘制三项耗时直方图。"""
    fig, axes = plt.subplots(len(METRICS), 1, figsize=(10, 14))

    for axis, metric in zip(axes, METRICS.values()):
        metric_df = df[df["指标"] == metric["name"]]
        max_minutes = metric_df["分钟"].max()
        bin_width = 0.5
        histogram_max = max(
            bin_width,
            np.ceil(max_minutes / bin_width) * bin_width,
        )
        bins = np.arange(0, histogram_max + bin_width, bin_width)

        axis.hist(metric_df["分钟"], bins=bins, edgecolor="black")
        axis.axvline(
            metric_df["分钟"].mean(),
            linestyle="--",
            label=f"Mean: {metric_df['分钟'].mean():.2f} min",
        )
        axis.axvline(
            metric_df["分钟"].median(),
            linestyle=":",
            label=f"Median: {metric_df['分钟'].median():.2f} min",
        )
        axis.set_title(metric["slug"].replace("_", " ").title())
        axis.set_xlabel("Duration (minutes)")
        axis.set_ylabel("Report count")
        axis.grid(axis="y", alpha=0.3)
        axis.legend()

    fig.tight_layout()
    fig.savefig(output_path, dpi=200)
    plt.close(fig)


def plot_boxplot(df, output_path):
    """绘制三项耗时对比箱线图。"""
    metric_names = [metric["name"] for metric in METRICS.values()]
    data = [df[df["指标"] == name]["分钟"] for name in metric_names]
    labels = [metric["slug"].replace("_", "\n") for metric in METRICS.values()]

    fig, axis = plt.subplots(figsize=(10, 6))
    axis.boxplot(data, tick_labels=labels, showmeans=True)
    axis.set_title("Duration Comparison")
    axis.set_ylabel("Duration (minutes)")
    axis.grid(axis="y", alpha=0.3)

    fig.tight_layout()
    fig.savefig(output_path, dpi=200)
    plt.close(fig)


def main():
    detail_df = load_metrics(INPUT_PATH)

    stats_df = pd.DataFrame(
        [
            calc_stats(
                detail_df[detail_df["指标"] == metric["name"]],
                metric["name"],
            )
            for metric in METRICS.values()
        ]
    )
    distribution_df = build_distribution_table(detail_df)

    print("统计结果：")
    print(stats_df.to_string(index=False))
    print("\n耗时区间分布：")
    print(distribution_df.to_string(index=False))

    detail_df.to_csv(
        SCRIPT_DIR / "report_duration_detail.csv",
        index=False,
        encoding="utf-8-sig",
    )
    stats_df.to_csv(
        SCRIPT_DIR / "report_duration_stats.csv",
        index=False,
        encoding="utf-8-sig",
    )
    distribution_df.to_csv(
        SCRIPT_DIR / "report_duration_distribution.csv",
        index=False,
        encoding="utf-8-sig",
    )

    plot_histograms(detail_df, SCRIPT_DIR / "report_duration_histograms.png")
    plot_boxplot(detail_df, SCRIPT_DIR / "report_duration_boxplot.png")


if __name__ == "__main__":
    main()
