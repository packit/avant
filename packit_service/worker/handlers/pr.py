import logging
import os
import re
from typing import Optional, override

from celery import Task
from packit.config import CommonPackageConfig, JobConfig, PackageConfig
from packit.config.job_config import JobConfigTriggerType, JobType
from packit.utils.extensions import nested_get

from packit_service.events import forgejo
from packit_service.worker.handlers.abstract import (
    FedoraCIJobHandler,
    RetriableJobHandler,
    TaskName,
    configured_as,
    reacts_to_as_fedora_ci,
    run_for_comment_as_fedora_ci,
)
from packit_service.worker.handlers.mixin import GetCoprBuildJobHelperMixin
from packit_service.worker.mixin import ConfigFromEventMixin, PackitAPIWithDownstreamMixin
from packit_service.worker.result import TaskResults


@reacts_to_as_fedora_ci(forgejo.pr.Action)
@reacts_to_as_fedora_ci(forgejo.pr.Comment)
class AvantPRBuildHandler(
    FedoraCIJobHandler,
    RetriableJobHandler,
    GetCoprBuildJobHelperMixin,
    ConfigFromEventMixin,
    PackitAPIWithDownstreamMixin,
):
    task_name = TaskName.copr_build
    check_name: str = "Avant - PR Handler"

    def __init__(
        self,
        package_config: PackageConfig,
        job_config: JobConfig,
        event: dict,
        celery_task: Task,
        copr_build_group_id: Optional[int] = None,
    ):
        self.event = event
        self.package_name: Optional[str] = None

        # Parse the package name and dynamic config first
        dynamic_config = self._get_construct_config_from_pr()
        if not dynamic_config:
            raise RuntimeError("Failed to parse `package_name:` from PR body. Cannot construct package config.")

        self.package_config = dynamic_config

        # Continue as usual
        super().__init__(
            package_config=self.package_config,
            job_config=job_config,
            event=event,
            celery_task=celery_task,
        )

        self._copr_build_group_id = copr_build_group_id

    @override
    def get_package_name(self) -> Optional[str]:
        if self.package_name is None:
            logging.warning("get_package_name called but package_name is None", stack_info=True)
        return self.package_name

    def _get_construct_config_from_pr(self) -> Optional[PackageConfig]:
        logging.debug(f"Parsing PR body to construct config: {self.event}")
        body = nested_get(self.event, "body", default="")

        match = re.search(r"package_name:\s*(\S+)", body)
        if not match:
            logging.warning("No 'package_name:' found in PR body.")
            return None

        package_name = match.group(1).strip()
        self.package_name = package_name
        specfile_path = f"{package_name}.spec"

        return PackageConfig(
            packages={
                package_name: CommonPackageConfig(
                    specfile_path=specfile_path,
                    _targets=["fedora-all"],
                )
            },
            jobs=[
                JobConfig(
                    type=JobType.copr_build,
                    trigger=JobConfigTriggerType.pull_request,
                    packages={
                        package_name: CommonPackageConfig(
                            specfile_path=specfile_path,
                            _targets=["fedora-all"],
                        )
                    },
                )
            ],
        )

    def run(self) -> TaskResults:
        logging.debug(f"[run] Starting COPR build. package_config={self.package_config}")
        if os.getenv("CANCEL_RUNNING_JOBS"):
            self.copr_build_helper.cancel_running_builds()

        return self.copr_build_helper.run_copr_build_from_source_script()

    @classmethod
    def pre_check(cls, package_config: PackageConfig, job_config: JobConfig, event: dict) -> bool:
        logging.debug(f"[pre_check] Checking: package_config={package_config}, event={event}")
        checks_pass = True
        for checker_cls in cls.get_checkers():
            task_name = getattr(cls, "task_name", None)
            checker = checker_cls(
                package_config=package_config,
                job_config=job_config,
                event=event,
                task_name=task_name.value if task_name else None,
            )
            checks_pass = checks_pass and checker.pre_check()

        return checks_pass
