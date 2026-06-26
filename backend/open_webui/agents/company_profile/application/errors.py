"""Application-level company profile errors.

Each exception carries an ``error_code`` that maps to the API response ``output.code``:

+-------+-----------------------------+
| code  | meaning                     |
+=======+=============================+
| 1     | 大模型调用异常               |
+-------+-----------------------------+
| 2     | 调用失败（业务层）            |
+-------+-----------------------------+
| 3     | 企业画像生成失败（兜底）       |
+-------+-----------------------------+
"""


class CompanyProfileError(Exception):
    """Base class for errors exposed by the application layer."""

    error_code: int = 3
    """API response ``output.code`` — 3 (兜底) by default."""


class CompanyProfileConfigurationError(CompanyProfileError):
    """Raised when required runtime configuration is missing.

    Maps to ``output.code = 2`` — 调用失败.
    """

    error_code: int = 2


class CompanyNotFoundError(CompanyProfileError):
    """Raised when no usable public company data can be found.

    Maps to ``output.code = 2`` — 调用失败.
    """

    error_code: int = 2


class LLMServiceError(CompanyProfileError):
    """Raised when the LLM API call itself fails (network / timeout / server error).

    Maps to ``output.code = 1`` — 大模型调用异常.
    """

    error_code: int = 1
