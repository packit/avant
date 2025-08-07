import os
from typing import Optional

from packit.config import PackageConfig, JobConfig

from avant.handlers.abstract import TaskName, reacts_to
from packit_service.events.koji.result import Task
from packit_service.worker.checker.abstract import Checker
from packit_service.worker.checker.copr import (
    IsJobConfigTriggerMatching,
    IsGitForgeProjectAndEventOk,
    CanActorRunTestsJob,
)
from packit_service.worker.handlers.abstract import RetriableJobHandler
from packit_service.worker.handlers.mixin import GetCoprBuildJobHelperMixin
from packit_service.worker.mixin import PackitAPIWithDownstreamMixin, ConfigFromEventMixin
from packit_service.worker.result import TaskResults
from packit_service.events import forgejo


@reacts_to(forgejo.pr.Action)
class COPRBuildHandler(
    RetriableJobHandler,
    ConfigFromEventMixin,
    PackitAPIWithDownstreamMixin,
    GetCoprBuildJobHelperMixin,
):
    task_name = TaskName.copr_build

    def __init__(
        self,
        package_config: PackageConfig,
        job_config: JobConfig,
        event: dict,
        celery_task: Task,
        copr_build_group_id: Optional[int] = None,
    ):
        super().__init__(
            package_config=package_config,
            job_config=job_config,
            event=event,
            celery_task=celery_task,
        )
        self._copr_build_group_id = copr_build_group_id

    @staticmethod
    def get_checkers() -> tuple[type[Checker], ...]:
        return (
            IsJobConfigTriggerMatching,
            IsGitForgeProjectAndEventOk,
            CanActorRunTestsJob,
        )

    def run(self) -> TaskResults:
        # [XXX] For now cancel only when an environment variable is defined,
        # should allow for less stressful testing and also optionally turning
        # the cancelling on-and-off on the prod
        if os.getenv("CANCEL_RUNNING_JOBS"):
            self.copr_build_helper.cancel_running_builds()

        return self.copr_build_helper.run_copr_build_from_source_script()
