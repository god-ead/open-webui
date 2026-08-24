"""Base collector abstract class for data collection adapters."""

from __future__ import annotations

from abc import ABC, abstractmethod

from ..models import CompanyMatch, RawCompanyData


class BaseCollector(ABC):
    """Abstract base class for all data source collectors.

    Each collector adapts a specific external data source (e.g. Qichacha,
    Tianyancha) to a unified interface for searching and collecting company data.
    """

    @abstractmethod
    def search(self, company_name: str) -> list[CompanyMatch]:
        """Search for companies matching the given name.

        Args:
            company_name: The company name to search for.

        Returns:
            A list of matching companies ranked by relevance.
        """

    @abstractmethod
    def collect(self, company_id: str) -> RawCompanyData:
        """Collect detailed data for a specific company.

        Args:
            company_id: The unique identifier of the company.

        Returns:
            Raw company data from this data source.
        """
