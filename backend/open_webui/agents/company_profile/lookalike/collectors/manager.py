"""Collector manager that coordinates multiple data source collectors."""

from __future__ import annotations

import logging
import time
from datetime import datetime

from .base import BaseCollector
from ..models import CompanyMatch, DataSource, RawCompanyData

logger = logging.getLogger(__name__)

MAX_RETRIES = 3
RETRY_DELAY_SECONDS = 5


class CollectorManager:
    """Manages multiple collectors, handling priority, retry, and data merging.

    Collectors are tried in priority order (first in list = highest priority).
    Failed calls are retried up to MAX_RETRIES times with RETRY_DELAY_SECONDS
    between attempts. Data from multiple sources is merged and deduplicated.
    """

    def __init__(self, collectors: list[BaseCollector]) -> None:
        self.collectors = collectors

    def search(self, company_name: str) -> list[CompanyMatch]:
        """Search for companies across all collectors in priority order.

        Returns the first non-empty result set, deduplicated by company_id.
        """
        for collector in self.collectors:
            result = self._call_with_retry(
                collector, "search", company_name=company_name
            )
            if result:  # non-None and non-empty
                return _deduplicate_matches(result)

        logger.warning("All collectors failed to search for '%s'", company_name)
        return []

    def collect(self, company_id: str) -> RawCompanyData:
        """Collect company data from all collectors and merge results.

        Each collector is tried independently. Data from successful collectors
        is merged, with earlier (higher-priority) collectors taking precedence
        for conflicting fields. Each piece of data records its source.
        """
        merged = RawCompanyData(company_id=company_id, company_name="")
        any_success = False

        for collector in self.collectors:
            result = self._call_with_retry(
                collector, "collect", company_id=company_id
            )
            if result is not None:
                any_success = True
                self._merge_data(merged, result, collector)

        if not any_success:
            logger.warning(
                "All collectors failed to collect data for '%s'", company_id
            )

        return merged

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _call_with_retry(
        self, collector: BaseCollector, method: str, **kwargs
    ):
        """Call a collector method with retry logic.

        Retries up to MAX_RETRIES times on failure, waiting
        RETRY_DELAY_SECONDS between attempts.

        Returns None if all attempts fail.
        """
        collector_name = type(collector).__name__
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                return getattr(collector, method)(**kwargs)
            except ValueError:
                # Configuration error (e.g. missing API key) — no point retrying
                logger.debug("%s.%s skipped: not configured", collector_name, method)
                return None
            except Exception:
                logger.error(
                    "%s.%s failed (attempt %d/%d)",
                    collector_name,
                    method,
                    attempt,
                    MAX_RETRIES,
                    exc_info=True,
                )
                if attempt < MAX_RETRIES:
                    time.sleep(RETRY_DELAY_SECONDS)
        return None

    @staticmethod
    def _merge_data(
        target: RawCompanyData,
        source: RawCompanyData,
        collector: BaseCollector,
    ) -> None:
        """Merge *source* into *target*, recording data provenance.

        For each dict field (business_info, recruitment_info, etc.), keys
        already present in *target* are NOT overwritten — higher-priority
        collectors keep precedence.  New keys are added and a DataSource
        entry is recorded for each.
        """
        collector_name = type(collector).__name__
        now = datetime.now()

        # Use the first non-empty company_name encountered
        if not target.company_name and source.company_name:
            target.company_name = source.company_name

        dict_fields = [
            "business_info",
            "recruitment_info",
            "market_activity",
            "litigation_info",
            "tech_products",
            "governance_info",
            "financial_info",
            "overseas_info",
            "contact_info",
        ]

        for field_name in dict_fields:
            target_dict: dict = getattr(target, field_name)
            source_dict: dict = getattr(source, field_name)
            for key, value in source_dict.items():
                if key not in target_dict:
                    target_dict[key] = value
                    target.sources.append(
                        DataSource(
                            source_name=collector_name,
                            url="",
                            collected_at=now,
                            field_name=f"{field_name}.{key}",
                        )
                    )


def _deduplicate_matches(matches: list[CompanyMatch]) -> list[CompanyMatch]:
    """Remove duplicate matches, keeping the first occurrence per company_id."""
    seen: set[str] = set()
    unique: list[CompanyMatch] = []
    for match in matches:
        if match.company_id not in seen:
            seen.add(match.company_id)
            unique.append(match)
    return unique
