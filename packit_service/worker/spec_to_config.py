# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

from gettext import install
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
from packit_service.worker.reporting import comment_without_duplicating

logger = logging.getLogger(__name__)


class SpecToConfig:
    @staticmethod
    def get_package_config_from_repo(
            project: GitProject,
            reference: Optional[str] = None,
            base_project: Optional[GitProject] = None,
            pr_id: Optional[int] = None,
            fail_when_missing: bool = True,
    ) -> Optional[PackageConfig]:
        """
        Construct a package config from the specfile in the repo.

        The config has the following jobs
        - copr_build
        - tests
        """

        if not base_project and not project:
            return None

        try:
            pull_request = project.get_pr(pr_id)
            source_project = pull_request.source_project
            spec_files = list(source_project.get_files(ref=reference, filter_regex=".spec"))
            if not spec_files:
                raise Exception(
                    f"No spec files found in {source_project.full_repo_name} on commit {reference}",
                )
            packages = {}
            for spec_file in spec_files:
                spec_path = spec_file
                logger.debug(
                    f"Found spec file: {spec_path} in "
                    f"{source_project.full_repo_name} on commit {reference}"
                )
                spec_content = source_project.get_file_content(
                    path=spec_path,
                    ref=reference,
                )
                specfile = Specfile(content=spec_content, sourcedir="/tmp/sources")
                if not specfile:
                    raise PackitConfigException(
                        f"Failed to parse spec file {spec_path} in "
                        f"{source_project.full_repo_name} on commit {reference}",
                    )

                packages[specfile.name] = CommonPackageConfig(
                    specfile_path=spec_path, _targets=["fedora-rawhide-x86_64"],
                )

            rpmlint_package = {}
            rpmlint_package[specfile.name] = CommonPackageConfig(
                specfile_path=spec_path,
                _targets=["fedora-rawhide-x86_64"],
                identifier="rpmlint",
                fmf_url="https://github.com/packit/tmt-plans",
                tmt_plan="/plans/rpmlint",
                fmf_ref="main"
            )

            install_package = {}
            install_package[specfile.name] = CommonPackageConfig(
                specfile_path=spec_path,
                _targets=["fedora-rawhide-x86_64"],
                identifier="installation",
                fmf_url="https://gitlab.com/testing-farm/tests",
                tmt_plan="/packit/installation",
                fmf_ref="main"
            )

            package_config = PackageConfig(
                packages=packages,
                jobs=[
                    JobConfig(
                        type=JobType.copr_build,
                        trigger=JobConfigTriggerType.pull_request,
                        packages=packages,
                    ),
                    JobConfig(
                        type=JobType.tests,
                        trigger=JobConfigTriggerType.pull_request,
                        packages=install_package,
                    ),
                    JobConfig(
                        type=JobType.tests,
                        trigger=JobConfigTriggerType.pull_request,
                        packages=rpmlint_package
                    )
                ],
            )
        except PackitConfigException as ex:
            message = (
                f"{ex}\n\n"
                if isinstance(ex, PackitMissingConfigException)
                else f"No spec files found:\n```\n{ex}\n```\n"
            )

            if pr_id:
                comment_without_duplicating(body=message, pr_or_issue=project.get_pr(pr_id))
            raise ex

        return package_config
