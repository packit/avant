# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import logging
from typing import Optional

from ogr.abstract import CommitStatus
from ogr.exceptions import ForgejoAPIException

from packit_service.worker.reporting import BaseCommitStatus
from packit_service.worker.reporting.enums import DuplicateCheckMode
from packit_service.worker.reporting.reporters.base import StatusReporter

logger = logging.getLogger(__name__)


class StatusReporterForgejo(StatusReporter):
    @staticmethod
    def get_commit_status(state: BaseCommitStatus):
        mapped_state = StatusReporter.get_commit_status(state)

        # Forgejo doesn't support 'running' state, map to pending like GitHub
        if mapped_state == CommitStatus.running:
            mapped_state = CommitStatus.pending
        # Forgejo doesn't support 'error' state, map to failure
        elif mapped_state == CommitStatus.error:
            mapped_state = CommitStatus.failure
        return mapped_state

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
        state_to_set = self.get_commit_status(state)
        logger.debug(f"Setting Forgejo status '{state_to_set.name}'")

        try:
            self.project_with_commit.set_commit_status(
                self.commit_sha, state_to_set, url, description, check_name, trim=True
            )

        except (ForgejoAPIException, ValueError) as e:
            logger.debug(f"Failed to set status: {e}")

            self._add_commit_comment_with_status(state, description, check_name, url)

    def comment(
        self,
        body: str,
        duplicate_check: DuplicateCheckMode = DuplicateCheckMode.do_not_check,
        to_commit: bool = False,
    ):
        """Override comment method to handle Forgejo's NotImplementedError in get_comments."""
        try:
            self.pull_request_object.comment(body=body)
        except NotImplementedError as e:
            logger.debug(
                f"Forgejo get_comments not implemented, falling back to simple comment: {e}"
            )
