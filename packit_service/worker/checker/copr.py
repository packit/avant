# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import logging


from packit_service.events import gitlab
from packit_service.worker.checker.abstract import (
    ActorChecker,
    Checker,
)
from packit_service.worker.handlers.mixin import (
    ConfigFromEventMixin,
    GetCoprBuildJobHelperForIdMixin,
    GetCoprBuildJobHelperMixin,
    GetCoprSRPMBuildMixin,
)
from packit_service.worker.reporting import BaseCommitStatus

logger = logging.getLogger(__name__)


class IsJobConfigTriggerMatching(
    Checker,
    ConfigFromEventMixin,
    GetCoprBuildJobHelperMixin,
):
    def pre_check(self) -> bool:
        return self.copr_build_helper.is_job_config_trigger_matching(self.job_config)


class IsGitForgeProjectAndEventOk(
    Checker,
    ConfigFromEventMixin,
    GetCoprBuildJobHelperMixin,
):
    def pre_check(
        self,
    ) -> bool:
        if (
            self.data.event_type == gitlab.mr.Action.event_type()
            and self.data.event_dict["action"] == gitlab.enums.Action.closed.value
        ):
            # Not interested in closed merge requests
            return False

        if not (self.copr_build_helper.job_build or self.copr_build_helper.job_tests_all):
            logger.info("No copr_build or tests job defined.")
            # we can't report it to end-user at this stage
            return False

        if self.copr_build_helper.is_custom_copr_project_defined():
            logger.debug(
                "Custom Copr owner/project set. "
                "Checking if this GitHub project can use this Copr project.",
            )
            if not self.copr_build_helper.check_if_custom_copr_can_be_used_and_report():
                return False

        return True


class AreOwnerAndProjectMatchingJob(Checker, GetCoprBuildJobHelperForIdMixin):
    def pre_check(self) -> bool:
        if (
            self.copr_event.owner == self.copr_build_helper.job_owner
            and self.copr_event.project_name == self.copr_build_helper.job_project
        ):
            return True

        logger.debug(
            f"The Copr project {self.copr_event.owner}/{self.copr_event.project_name} "
            f"does not match the configuration "
            f"({self.copr_build_helper.job_owner}/{self.copr_build_helper.job_project} expected).",
        )
        return False


class IsPackageMatchingJobView(Checker, GetCoprSRPMBuildMixin):
    """
    When running builds for multiple packages (in monorepo) in one job
    config, we need to check whether the package that we are handling matches
    the job configuration.
    """

    def pre_check(self) -> bool:
        build_for_package = self.build.get_package_name()
        if not self.job_config.package or build_for_package == self.job_config.package:
            return True

        logger.debug(
            f"The Copr build {self.copr_event.build_id} (pkg={build_for_package}) "
            f"does not match the package from the configuration "
            f"({self.job_config.package}).",
        )
        return False


class BuildNotAlreadyStarted(Checker, GetCoprSRPMBuildMixin):
    def pre_check(self) -> bool:
        build = self.build
        if not build:
            return True
        return not bool(build.build_start_time)


class CanActorRunTestsJob(
    ActorChecker,
    ConfigFromEventMixin,
    GetCoprBuildJobHelperMixin,
):
    """For external contributors, we need to be more careful when running jobs.
    This is a handler-specific permission check
    for a user who trigger the action on a PR.
    """

    def _pre_check(self) -> bool:
        # All test jobs are now allowed for external contributors
        # since Testing Farm integration has been removed
        logger.debug("All test jobs allowed for external contributors")
        return True
