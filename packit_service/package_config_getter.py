# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import logging
from typing import Optional

from packit.config import (
    CommonPackageConfig,
    JobConfig,
    JobConfigTriggerType,
    JobType,
    PackageConfig,
)
from packit.exceptions import (
    PackitConfigException,
    PackitMissingConfigException,
)
from specfile.specfile import Specfile

from ogr.abstract import GitProject
from packit_service.constants import (
    CONTACTS_URL,
    DOCS_HOW_TO_CONFIGURE_URL,
    DOCS_VALIDATE_CONFIG,
    DOCS_VALIDATE_HOOKS,
)
from packit_service.worker.reporting import comment_without_duplicating, create_issue_if_needed

logger = logging.getLogger(__name__)


class PackageConfigGetter:
    @staticmethod
    def get_package_config_from_repo(
        project: GitProject,
        reference: Optional[str] = None,
        base_project: Optional[GitProject] = None,
        pr_id: Optional[int] = None,
        fail_when_missing: bool = True,
    ) -> Optional[PackageConfig]:
        """
        Get the package config and catch the invalid config scenario and possibly no-config scenario
        """

        if not base_project and not project:
            return None

        try:
            logger.debug(f"Getting PR {pr_id} from project: {project.full_repo_name}")
            pull_request = project.get_pr(pr_id)
            logger.debug(f"Got pull request: {pull_request}")
            source_project = pull_request.source_project
            logger.debug(f"Source project: {source_project.full_repo_name}")
            logger.debug(f"Target project: {pull_request.target_project.full_repo_name}")
            # Use the reference passed in (should be head commit for PR events)
            logger.debug(f"Using reference: {reference} for source project search")
            logger.debug(
                f"Searching for spec files in source project: {source_project.full_repo_name} on commit {reference}"
            )
            spec_files = list(source_project.get_files(ref=reference, filter_regex=".spec"))
            if not spec_files:
                raise Exception(
                    f"No spec files found in {source_project.full_repo_name} on commit {reference}",
                )
            spec_path = spec_files[0]
            logger.debug(
                f"Found spec file: {spec_path} in {source_project.full_repo_name} on commit {reference}"
            )
            spec_content = source_project.get_file_content(
                path=spec_path,
                ref=reference,
            )
            specfile = Specfile(content=spec_content, sourcedir="/tmp/sources")
            if not specfile:
                raise PackitConfigException(
                    f"Failed to parse spec file {spec_path} in {source_project.full_repo_name} on commit {reference}",
                )

            package_name = specfile.name
            logger.debug(f"Parsed spec file: {specfile}, package name: {package_name}")

            package_config = PackageConfig(
                packages={
                    package_name: CommonPackageConfig(specfile_path=spec_path),
                },
                jobs=[
                    JobConfig(
                        type=JobType.copr_build,
                        trigger=JobConfigTriggerType.pull_request,
                        packages={
                            package_name: CommonPackageConfig(
                                _targets=["fedora-rawhide"],
                                specfile_path=spec_path,
                            ),
                        },
                    ),
                    JobConfig(
                        type=JobType.tests,
                        trigger=JobConfigTriggerType.pull_request,
                        packages={
                            package_name: CommonPackageConfig(
                                _targets=["fedora-rawhide"],
                                specfile_path=spec_path,
                            )
                        },
                    ),
                ],
            )
        except PackitConfigException as ex:
            # Error handling code remains the same
            message = (
                f"{ex}\n\n"
                if isinstance(ex, PackitMissingConfigException)
                else f"Failed to load packit config file:\n```\n{ex}\n```\n"
            )

            message += (
                "For more info, please check out "
                f"[the documentation]({DOCS_HOW_TO_CONFIGURE_URL}) "
                "or [contact the Packit team]"
                f"({CONTACTS_URL}). You can also use "
                f"our CLI command [`config validate`]({DOCS_VALIDATE_CONFIG}) or our "
                f"[pre-commit hooks]({DOCS_VALIDATE_HOOKS}) for validation of the configuration."
            )

            if pr_id:
                comment_without_duplicating(body=message, pr_or_issue=project.get_pr(pr_id))
            elif created_issue := create_issue_if_needed(
                project,
                title="Invalid config",
                message=message,
            ):
                logger.debug(
                    f"Created issue for invalid packit config: {created_issue.url}",
                )
            raise ex

        return package_config

