"""企业画像 PDF 导出模块 — 将分析结果渲染为 PDF 并返回下载链接"""

from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from company_profile.application import ProfileApplicationResult
from company_profile.lookalike.report import ReportGenerator


@dataclass(frozen=True)
class ProfilePdfExportConfig:
    """PDF 导出配置：临时目录、备份目录和下载基础 URL"""
    temp_dir: Path
    backup_dir: Path
    download_base_url: str


class ProfilePdfExporter:
    """企业画像 PDF 导出器：渲染 PDF → 写入临时目录 → 同步备份 → 返回下载链接"""

    def __init__(self, config: ProfilePdfExportConfig) -> None:
        self.config = config
        self._reporter = ReportGenerator()

    @classmethod
    def from_env(cls) -> "ProfilePdfExporter":
        """从环境变量构建导出器实例"""
        return cls(
            ProfilePdfExportConfig(
                temp_dir=Path("/app/data/pdf-temp"),
                backup_dir=Path("/app/data/pdf-backup"),
                download_base_url=os.getenv(
                    "PROFILE_PDF_DOWNLOAD_BASE_URL",
                    "http://localhost:8088/api/download",
                ),
            )
        )

    def export(self, result: ProfileApplicationResult) -> str:
        """导出 PDF：生成→写临时目录→复制备份（失败回滚）→返回公开下载链接"""
        self.config.temp_dir.mkdir(parents=True, exist_ok=True)
        self.config.backup_dir.mkdir(parents=True, exist_ok=True)

        file_name = self._file_name(result.report_id)
        temp_path = self.config.temp_dir / file_name
        backup_path = self.config.backup_dir / file_name

        # 生成 PDF 到临时目录
        self._reporter.generate_pdf(result.analysis, str(temp_path))

        # 复制到备份目录，失败时清理临时文件再抛出
        try:
            shutil.copy2(temp_path, backup_path)
        except Exception:
            try:
                temp_path.unlink()
            except FileNotFoundError:
                pass
            raise

        return f"{self.config.download_base_url.rstrip('/')}/{file_name}"

    @staticmethod
    def _file_name(report_id: str) -> str:
        """根据 report_id 生成安全的 PDF 文件名，保留中英文及数字，最长 120 字符"""
        safe = re.sub(r"[^A-Za-z0-9一-鿿_.-]+", "_", report_id).strip("._")
        if not safe:
            safe = "company_profile"
        return f"{safe[:120]}.pdf"
