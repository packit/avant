import logging
from typing import Optional

from packit_service.worker.reporting import BaseCommitStatus
from packit_service.worker.reporting.reporters.base import DuplicateCheckMode, StatusReporter

logger = logging.getLogger(__name__)

class StatusReporterForgejo(StatusReporter):

    @staticmethod
    def get_commit_status(state: BaseCommitStatus):
        mapped_state = StatusReporter.get_commit_status(state)
        return mapped_state

    # TODO: add the reports for fedora-review and build status and testing farm logs.

    def set_status(
        self,
        state: BaseCommitStatus,
        description: str,
        check_name: str,
        url: str = "",
        links_to_external_services: Optional[dict[str, str]] = None,
        markdown_content: Optional[str] = None,
        target_branch: Optional[str] = None,
    ):
        state_to_set=state.value
        self.comment(
            body=description,
            duplicate_check=DuplicateCheckMode.do_not_check,
            to_commit=False,
        )