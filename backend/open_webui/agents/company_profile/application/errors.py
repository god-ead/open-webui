"""Application-level company profile errors."""


class CompanyProfileError(Exception):
    """Base class for errors exposed by the application layer."""


class CompanyProfileConfigurationError(CompanyProfileError):
    """Raised when required runtime configuration is missing."""


class CompanyNotFoundError(CompanyProfileError):
    """Raised when no usable public company data can be found."""
