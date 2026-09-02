"""No-op storage used by the trial pipe to avoid report persistence."""

from __future__ import annotations


class NoopStorage:
    """Duck-typed replacement for FileStorage.

    The analysis engine only calls ``save_report`` in the trial flow.
    ``load_report`` is intentionally unsupported because rescore/report
    retrieval are out of scope for this pipe.
    """

    def save_report(self, report_id: str, data: object) -> str:
        return ""

    def load_report(self, report_id: str) -> object:
        raise NotImplementedError("Report loading is not supported in the trial pipe")
