# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

"""
We love you, Steve Jobs.
"""

import logging
from datetime import datetime
from functools import cached_property
from re import match
from typing import Callable, Optional, Union, List

import celery
from ogr.exceptions import GithubAppNotInstalledError
from packit.config import JobConfig, JobConfigTriggerType, JobConfigView, JobType, PackageConfig
from packit.utils import nested_get

from packit_service.config import ServiceConfig
from packit_service.constants import (
    COMMENT_REACTION,
    PACKIT_VERIFY_FAS_COMMAND,
    TASK_ACCEPTED,
)
from packit_service.events import (
    abstract,
    github,
    koji,
    pagure,
    testing_farm,
    new_package,
    forgejo
)
from packit_service.events.event import Event
from packit_service.events.event_data import EventData
from packit_service.package_config_getter import PackageConfigGetter
from packit_service.utils import (
    elapsed_seconds,
    get_packit_commands_from_comment,
    pr_labels_match_configuration,
)
from packit_service.worker.allowlist import Allowlist
from packit_service.worker.handlers import (
    CoprBuildHandler,
    GithubAppInstallationHandler,
    GithubFasVerificationHandler,
    KojiBuildHandler,
    ProposeDownstreamHandler,
    TestingFarmHandler,
)
from packit_service.worker.handlers.abstract import (
    MAP_CHECK_PREFIX_TO_HANDLER,
    MAP_COMMENT_TO_HANDLER,
    MAP_COMMENT_TO_HANDLER_FEDORA_CI,
    MAP_JOB_TYPE_TO_HANDLER,
    MAP_REQUIRED_JOB_TYPE_TO_HANDLER,
    SUPPORTED_EVENTS_FOR_HANDLER,
    SUPPORTED_EVENTS_FOR_HANDLER_FEDORA_CI,
    FedoraCIJobHandler,
    JobHandler,
    NewPackageHandler,
    SUPPORTED_EVENTS_FOR_HANDLER_NEW_PACKAGE,
    MAP_COMMENT_TO_HANDLER_NEW_PACKAGE
)
from packit_service.worker.helpers.build import (
    BaseBuildJobHelper,
    CoprBuildJobHelper,
    KojiBuildJobHelper,
)
from packit_service.worker.helpers.fedora_ci import FedoraCIHelper
from packit_service.worker.helpers.sync_release.propose_downstream import (
    ProposeDownstreamJobHelper,
)
from packit_service.worker.helpers.testing_farm import TestingFarmJobHelper
from packit_service.worker.monitoring import Pushgateway
from packit_service.worker.parser import Parser
from packit_service.worker.reporting import BaseCommitStatus
from packit_service.worker.result import TaskResults

logger = logging.getLogger(__name__)


MANUAL_OR_RESULT_EVENTS = [
    abstract.comment.CommentEvent, abstract.base.Result, github.check.Rerun]


def get_handlers_for_comment(
    comment: str,
    packit_comment_command_prefix: str,
) -> set[type[JobHandler]]:
    """
    Get handlers for the given command respecting packit_comment_command_prefix.

    Args:
        comment: comment we are reacting to
        packit_comment_command_prefix: `/packit` for packit-prod or `/packit-stg` for stg

    Returns:
        Set of handlers that are triggered by a comment.
    """
    commands = get_packit_commands_from_comment(
        comment, packit_comment_command_prefix)
    if not commands:
        return set()

    handlers = MAP_COMMENT_TO_HANDLER[commands[0]]
    if not handlers:
        logger.debug(f"Command {commands[0]} not supported by packit.")
    return handlers

def get_handlers_for_comment_new_package(
    comment: str,
    packit_comment_command_prefix: str,
) -> set[type[NewPackageHandler]]:
    """
    Get handlers for the given command respecting packit_comment_command_prefix.
    """
    commands = get_packit_commands_from_comment(
        comment, packit_comment_command_prefix)
    if not commands:
        return set()
    
    handlers = MAP_COMMENT_TO_HANDLER_NEW_PACKAGE[commands[0]]
    if not handlers:
        logger.debug(f"Command {commands[0]} not supported by packit.")
    return handlers

def get_handlers_for_comment_fedora_ci(
    comment: str,
    packit_comment_command_prefix: str,
) -> set[type[FedoraCIJobHandler]]:
    """
    Get handlers for the given Fedora CI command respecting packit_comment_command_prefix.

    Args:
        comment: comment we are reacting to
        packit_comment_command_prefix: `/packit-ci` for prod or `/packit-ci-stg` for stg

    Returns:
        Set of handlers that are triggered by a comment.
    """
    # TODO: remove this once Fedora CI has its own instances and comment_command_prefixes
    # comment_command_prefixes for Fedora CI are /packit-ci and /packit-ci-stg
    if packit_comment_command_prefix.endswith("-stg"):
        packit_comment_command_prefix = "/packit-ci-stg"
    else:
        packit_comment_command_prefix = "/packit-ci"

    commands = get_packit_commands_from_comment(
        comment, packit_comment_command_prefix)
    if not commands:
        return set()

    handlers = MAP_COMMENT_TO_HANDLER_FEDORA_CI[commands[0]]
    if not handlers:
        logger.debug(f"Command {commands[0]} not supported by packit.")
    return handlers


def get_handlers_for_check_rerun(comment: str, packit_comment_command_prefix: str) -> set[type[JobHandler]]:
    """
    Get handlers for the given check name.

    Args:
        check_name_job: check name we are reacting to

    Returns:
        Set of handlers that are triggered by a check rerun.
    """
    commands = get_packit_commands_from_comment(
        comment,
        packit_comment_command_prefix=packit_comment_command_prefix,
    )

    if packit_comment_command_prefix.endswith("-test"):
        packit_comment_command_prefix = "/packit-test"
    else:
        packit_comment_command_prefix = "/packit"

    handlers = MAP_CHECK_PREFIX_TO_HANDLER[commands[0]]
    if not handlers:
        logger.debug(
            f"Rerun for check with {
                commands[0]} prefix not supported by packit.",
        )
    return handlers


class SteveJobs:
    """
    Steve makes sure all the jobs are done with precision.
    """

    def __init__(self, event: Optional[Event] = None) -> None:
        self.event = event
        self.pushgateway = Pushgateway()

    @cached_property
    def service_config(self) -> ServiceConfig:
        return ServiceConfig.get_service_config()

    @classmethod
    def process_message(
        cls,
        event: dict,
        source: Optional[str] = None,
        event_type: Optional[str] = None,
    ) -> list[TaskResults]:
        """
        Entrypoint for message processing.

        For values of 'source' and 'event_type' see Parser.MAPPING.

        Args:
            event: Dict with webhook/fed-msg payload.
            source: Source of the event, for example: "github".
            event_type: Type of the event.

        Returns:
            List of results of the processing tasks.
        """
        parser = nested_get(
            Parser.MAPPING,
            source,
            event_type,
            default=Parser.parse_event,
        )
        event_object: Optional[Event] = parser(event)
        steve = cls(event_object)
        steve.pushgateway.events_processed.inc()
        if event_not_handled := not event_object:
            steve.pushgateway.events_not_handled.inc()
        elif pre_check_failed := not event_object.pre_check():
            steve.pushgateway.events_pre_check_failed.inc()

        result = [] if (
            event_not_handled or pre_check_failed) else steve.process()

        steve.pushgateway.push()
        return result

    def process(self) -> list[TaskResults]:
        """
        Processes the event object attribute of SteveJobs - runs the checks for
        the given event and creates tasks that match the event,
        example usage: SteveJobs(event_object).process()

        Returns:
            List of processing task results.
        """
        try:
            if not self.is_project_public_or_enabled_private():
                return []
        except GithubAppNotInstalledError:
            host, namespace, repo = (
                self.event.project.service.hostname,
                self.event.project.namespace,
                self.event.project.repo,
            )
            logger.info(
                "Packit is not installed on %s/%s/%s, skipping.",
                host,
                namespace,
                repo,
            )
            return []

        processing_results = None

        # installation is handled differently b/c app is installed to GitHub account
        # not repository, so package config with jobs is missing

        if isinstance(
            self.event,
            new_package.NewPackageEvent
        ):
            processing_results = self.process_organisation_jobs()
            if not processing_results:
                # processing the jobs from the config
                processing_results = self.process_jobs()

        if isinstance(self.event, forgejo.issue.Comment):
            processing_results = self.process_jobs()

        if processing_results is None:
            processing_results = [
                TaskResults.create_from(
                    success=True,
                    msg="Job created.",
                    job_config=None,
                    event=self.event,
                ),
            ]

        return processing_results

    def process_organisation_jobs(self) -> List[TaskResults]:
        if isinstance(self.event, new_package.NewPackageEvent):
            logger.info("Package Received")
            results = []
            for handler_cls in self.get_new_package_handlers_for_event():
                handler = handler_cls(event=self.event.__dict__)
                result = handler.run_job()
                results.append(result)
            return results
        return []

    def get_new_package_handlers_for_event(self):
        matching_handlers = set()
        for handler, events in SUPPORTED_EVENTS_FOR_HANDLER_NEW_PACKAGE.items():
            if isinstance(self.event, tuple(events)):
                matching_handlers.add(handler)
        return matching_handlers

    def initialize_job_helper(
        self,
        handler_kls: type[JobHandler],
        job_config: JobConfig,
    ) -> Union[ProposeDownstreamJobHelper, BaseBuildJobHelper]:
        """
        Initialize job helper with arguments
        based on what type of handler is used.

        Args:
            handler_kls: The class for the Handler that will handle the job.
            job_config: Corresponding job config.

        Returns:
            The correct job helper.
        """
        params = {
            "service_config": self.service_config,
            "package_config": (
                self.event.packages_config.get_package_config_for(job_config)
                if self.event.packages_config
                else None
            ),
            "project": self.event.project,
            "metadata": EventData.from_event_dict(self.event.get_dict()),
            "db_project_event": self.event.db_project_event,
            "job_config": job_config,
        }

        if handler_kls == ProposeDownstreamHandler:
            propose_downstream_helper = ProposeDownstreamJobHelper
            params["branches_override"] = self.event.branches_override
            return propose_downstream_helper(**params)

        helper_kls: type[Union[TestingFarmJobHelper,
                               CoprBuildJobHelper, KojiBuildJobHelper]]

        if handler_kls == TestingFarmHandler:
            helper_kls = TestingFarmJobHelper
        elif handler_kls == CoprBuildHandler:
            helper_kls = CoprBuildJobHelper
        else:
            helper_kls = KojiBuildJobHelper

        params.update(
            {
                "build_targets_override": self.event.build_targets_override,
                "tests_targets_override": self.event.tests_targets_override,
            },
        )
        return helper_kls(**params)

    def report_task_accepted(
        self,
        handler_kls: type[JobHandler],
        job_config: JobConfig,
        update_feedback_time: Callable,
    ) -> None:
        """
        For the upstream events report the initial status "Task was accepted" to
        inform user we are working on the request. Measure the time how much did it
        take to set the status from the time when the event was triggered.

        Args:
            handler_kls: The class for the Handler that will be used.
            job_config: Job config that is being used.
            update_feedback_time: A callable which tells the caller when a check
                status has been updated.
        """
        number_of_build_targets = None
        if handler_kls not in (
            CoprBuildHandler,
        ):
            # no reporting, no metrics
            return

        job_helper = self.initialize_job_helper(handler_kls, job_config)

        if isinstance(job_helper, CoprBuildJobHelper):
            number_of_build_targets = len(job_helper.build_targets)

        job_helper.report_status_to_configured_job(
            description=TASK_ACCEPTED,
            state=BaseCommitStatus.pending,
            url="",
            update_feedback_time=update_feedback_time,
        )

        self.push_copr_metrics(handler_kls, number_of_build_targets)

    def process_jobs(self) -> list[TaskResults]:
        """
        Create Celery tasks for a job handler (if trigger matches) for every
        job defined in config.

        Returns:
            List of the results of each task.
        """
        # --- new_package command path ---
        from packit_service.events.forgejo.issue import Comment as ForgejoIssueComment
        if isinstance(self.event, ForgejoIssueComment):
            handlers = self.get_handlers_for_event_new_package()
            if not handlers:
                return [
                    TaskResults(
                        success=True,
                        details={"msg": "No new_package command handler found in the comment."},
                    ),
                ]
            results = []
            for handler_cls in handlers:
                # For new_package, we may not have package_config/job_config, so pass None or minimal
                handler = handler_cls(
                    event=self.event.get_dict() if hasattr(self.event, 'get_dict') else self.event.__dict__
                )
                results.append(handler.run_job())
            return results
        # --- end new_package command path ---

        if isinstance(
            self.event,
            abstract.comment.CommentEvent,
        ) and not get_handlers_for_comment(
            self.event.comment,
            packit_comment_command_prefix=self.service_config.comment_command_prefix,
        ):
            return [
                TaskResults(
                    success=True,
                    details={"msg": "No Packit command found in the comment."},
                ),
            ]

        handler_classes = self.get_handlers_for_event()

        if not handler_classes:
            logger.debug(
                f"There is no handler for {
                    self.event} event suitable for the configuration.",
            )
            return []

        allowlist = Allowlist(service_config=self.service_config)
        processing_results: list[TaskResults] = []

        statuses_check_feedback: list[datetime] = []
        for handler_kls in handler_classes:
            # TODO: merge to to get_handlers_for_event so
            # so we don't need to go through the similar process twice.
            job_configs = self.get_config_for_handler_kls(
                handler_kls=handler_kls,
            )

            # check allowlist approval for every job to be able to track down which jobs
            # failed because of missing allowlist approval
            if not allowlist.check_and_report(
                self.event,
                self.event.project,
                job_configs=job_configs,
            ):
                return [
                    TaskResults.create_from(
                        success=False,
                        msg="Account is not allowlisted!",
                        job_config=job_config,
                        event=self.event,
                    )
                    for job_config in job_configs
                ]

            processing_results.extend(
                self.create_tasks(job_configs, handler_kls,
                                  statuses_check_feedback),
            )
        self.push_statuses_metrics(statuses_check_feedback)

        return processing_results

    def create_tasks(
        self,
        job_configs: list[JobConfig],
        handler_kls: type[JobHandler],
        statuses_check_feedback: list[datetime],
    ) -> list[TaskResults]:
        """
        Create handler tasks for handler and job configs.

        Args:
            job_configs: Matching job configs.
            handler_kls: Handler class that will be used.
        """
        processing_results: list[TaskResults] = []
        signatures = []
        # we want to run handlers for all possible jobs, not just the first one
        for job_config in job_configs:
            if self.should_task_be_created_for_job_config_and_handler(
                job_config,
                handler_kls,
            ):
                self.report_task_accepted(
                    handler_kls=handler_kls,
                    job_config=job_config,
                    update_feedback_time=lambda t: statuses_check_feedback.append(
                        t),
                )
                if handler_kls in (
                    CoprBuildHandler,
                    TestingFarmHandler,
                    KojiBuildHandler,
                ):
                    self.event.store_packages_config()

                signatures.append(
                    handler_kls.get_signature(
                        event=self.event, job=job_config),
                )
                logger.debug(
                    f"Got signature for handler={
                        handler_kls} and job_config={job_config}.",
                )
                processing_results.append(
                    TaskResults.create_from(
                        success=True,
                        msg="Job created.",
                        job_config=job_config,
                        event=self.event,
                    ),
                )
        logger.debug("Signatures are going to be sent to Celery.")
        # https://docs.celeryq.dev/en/stable/userguide/canvas.html#groups
        celery.group(signatures).apply_async()
        logger.debug("Signatures were sent to Celery.")
        return processing_results

    def should_task_be_created_for_job_config_and_handler(
        self,
        job_config: JobConfig,
        handler_kls: type[JobHandler],
    ) -> bool:
        """
        Check whether a new task should be created for job config and handler.

        Args:
            job_config: Job config to check.
            handler_kls: Type of handler class to check.

        Returns:
            Whether the task should be created.
        """
        if self.service_config.deployment not in job_config.packit_instances:
            logger.debug(
                f"Current deployment ({self.service_config.deployment}) "
                f"does not match the job configuration ({
                    job_config.packit_instances}). "
                "The job will not be run.",
            )
            return False

        return handler_kls.pre_check(
            package_config=(
                self.event.packages_config.get_package_config_for(job_config)
                if self.event.packages_config
                else None
            ),
            job_config=job_config,
            event=self.event.get_dict(),
        )

    def is_project_public_or_enabled_private(self) -> bool:
        """
        Checks whether the project is public or if it is private, explicitly enabled
        in our service configuration.

        Returns:
            `True`, if the project is public or enabled in our service config
            or the check is skipped,
            `False` otherwise.
        """
        # Skip the check for Forgejo issue comments
        if isinstance(self.event, forgejo.issue.Comment):
            return True
        # do the check only for events triggering the pipeline
        if isinstance(self.event, abstract.base.Result):
            logger.debug(
                "Skipping private repository check for this type of event.")

        # CoprBuildEvent.get_project returns None when the build id is not known
        elif not (self.event and hasattr(self.event, "project") and self.event.project):
            logger.warning(
                "Cannot obtain project from this event! Skipping private repository check!",
            )
        # TODO: this is totally not OK and a lazy hack! need to check project type properly.
        elif (
            hasattr(self.event, "project")
            and self.event.project
            and hasattr(self.event.project, "is_private")
            and self.event.project.is_private()
            and hasattr(self.event.project, "service")
            and self.event.project.service
            and hasattr(self.event.project, "namespace")
            and self.event.project.namespace
        ):
            service_with_namespace = (
                f"{self.event.project.service.hostname}/{self.event.project.namespace}"
            )
            if service_with_namespace not in self.service_config.enabled_private_namespaces:
                logger.info(
                    f"We do not interact with private repositories by default. "
                    f"Add `{service_with_namespace}` to the `enabled_private_namespaces` "
                    f"in the service configuration.",
                )
                return False
            logger.debug(
                f"Working in `{service_with_namespace}` namespace "
                f"which is private but enabled via configuration.",
            )

        return True

    def check_explicit_matching(self) -> list[JobConfig]:
        """Force explicit event/jobs matching for triggers

        Returns:
            List of job configs.
        """

        def compare_jobs_without_triggers(a, b):
            # check if two jobs are the same or differ only in trigger
            ad = dict(a.__dict__)
            ad.pop("trigger")
            bd = dict(b.__dict__)
            bd.pop("trigger")
            return ad == bd

        matching_jobs: list[JobConfig] = []
        if isinstance(self.event, pagure.pr.Comment):
            for job in self.event.packages_config.get_job_views():
                if (
                    job.type in [JobType.koji_build, JobType.bodhi_update]
                    and job.trigger
                    in (JobConfigTriggerType.commit, JobConfigTriggerType.koji_build)
                    and self.event.job_config_trigger_type == JobConfigTriggerType.pull_request
                ):
                    if job.type == JobType.koji_build:
                        # avoid having duplicate koji_build jobs
                        if any(j for j in matching_jobs if compare_jobs_without_triggers(job, j)):
                            continue
                    matching_jobs.append(job)
                elif (
                    job.type == JobType.pull_from_upstream
                    and job.trigger == JobConfigTriggerType.release
                    and self.event.job_config_trigger_type == JobConfigTriggerType.pull_request
                ):
                    # A pull_from_upstream job with release trigger
                    # can be re-triggered by a comment in a dist-git PR
                    matching_jobs.append(job)
        elif isinstance(self.event, abstract.comment.Issue):
            for job in self.event.packages_config.get_job_views():
                if (
                    job.type in (JobType.koji_build, JobType.bodhi_update)
                    and job.trigger
                    in (JobConfigTriggerType.commit, JobConfigTriggerType.koji_build)
                    and self.event.job_config_trigger_type == JobConfigTriggerType.release
                ):
                    # avoid having duplicate koji_build jobs
                    if job.type == JobType.koji_build and any(
                        j for j in matching_jobs if compare_jobs_without_triggers(job, j)
                    ):
                        continue
                    # A koji_build/bodhi_update can be re-triggered by a
                    # comment in a issue in the repository issues
                    # after a failed release event
                    # (which has created the issue)
                    # matching_jobs.append(job)
        elif isinstance(self.event, koji.tag.Build):
            # create a virtual job config
            job_config = JobConfig(
                JobType.koji_build_tag,
                JobConfigTriggerType.koji_build,
                self.event.packages_config.packages,
            )
            for package, config in self.event.packages_config.packages.items():
                if config.downstream_package_name == self.event.package_name:
                    job = JobConfigView(job_config, package)
                    matching_jobs.append(job)
                    # if there are multiple packages with the same downstream_package_name,
                    # choose any of them (the handler should ignore the config anyway)
                    break

        return matching_jobs

    def get_jobs_matching_event(self) -> list[JobConfig]:
        """
        Get list of non-duplicated all jobs that matches with event's trigger.

        Returns:
            List of all jobs that match the event's trigger.
        """
        jobs_matching_trigger = []
        for job in self.event.packages_config.get_job_views():
            if (
                job.trigger == self.event.job_config_trigger_type
                and (
                    not isinstance(self.event, github.check.Rerun)
                    or self.event.job_identifier == job.identifier
                )
                and job not in jobs_matching_trigger
                # Manual trigger condition
                and (
                    not job.manual_trigger
                    or any(
                        isinstance(self.event, event_type) for event_type in MANUAL_OR_RESULT_EVENTS
                    )
                )
                and (
                    job.trigger != JobConfigTriggerType.pull_request
                    or not (job.require.label.present or job.require.label.absent)
                    or not isinstance(self.event, abstract.base.ForgeIndependent)
                    or pr_labels_match_configuration(
                        pull_request=self.event.pull_request_object,
                        configured_labels_absent=job.require.label.absent,
                        configured_labels_present=job.require.label.present,
                    )
                )
            ):
                jobs_matching_trigger.append(job)

        jobs_matching_trigger.extend(self.check_explicit_matching())

        return jobs_matching_trigger

    def get_handlers_for_comment_and_rerun_event(self) -> set[type[JobHandler]]:
        """
        Get all handlers that can be triggered by comment (e.g. `/packit build`) or check rerun.

        For comment events we want to get handlers mapped to comment commands. For check rerun
        event we want to get handlers mapped to check name job.
        These two sets of handlers are mutually exclusive.

        Returns:
            Set of handlers that are triggered by a comment or check rerun job.
        """
        handlers_triggered_by_job = None

        if isinstance(self.event, abstract.comment.CommentEvent):
            handlers_triggered_by_job = get_handlers_for_comment(
                self.event.comment,
                self.service_config.comment_command_prefix,
            )

            if handlers_triggered_by_job and not isinstance(
                self.event,
                (pagure.pr.Comment, abstract.comment.Commit),
            ):
                self.event.comment_object.add_reaction(COMMENT_REACTION)

        if isinstance(self.event, github.check.Rerun):
            handlers_triggered_by_job = get_handlers_for_check_rerun(
                self.event.check_name_job,
            )

        return handlers_triggered_by_job

    def get_handlers_for_comment_and_rerun_event_new_package(self) -> set[type[NewPackageHandler]]:
        """
        Get all handlers that can be triggered by new_package comment commands.
        """
        handlers_triggered_by_job = None
        # You may want to define a specific event type for new_package comment events
        # For now, let's assume it's a forgejo.issue.Comment with a new_package command
        from packit_service.events.forgejo.issue import Comment as ForgejoIssueComment
        if isinstance(self.event, ForgejoIssueComment):
            handlers_triggered_by_job = get_handlers_for_comment_new_package(
                self.event.comment,
                self.service_config.comment_command_prefix,
            )
        return handlers_triggered_by_job or set()

    def get_handlers_for_event_new_package(self) -> set[type[NewPackageHandler]]:
        """
        Get all handlers that we need to run for the given new_package event.
        """
        # For new_package, we don't have jobs_matching_trigger, just use the comment handler mapping
        handlers_triggered_by_job = self.get_handlers_for_comment_and_rerun_event_new_package()
        matching_handlers: set[type[NewPackageHandler]] = set()
        for handler in handlers_triggered_by_job:
            matching_handlers.add(handler)
        logger.debug(f"Matching new_package handlers: {matching_handlers}")
        return matching_handlers

    def get_handlers_for_event(self) -> set[type[JobHandler]]:
        """
        Get all handlers that we need to run for the given event.

        We need to return all handler classes that:
        - can react to the given event **and**
        - are configured in the package_config (either directly or as a required job)

        Examples of the matching can be found in the tests:
        ./tests/unit/test_jobs.py:test_get_handlers_for_event

        Returns:
            Set of handler instances that we need to run for given event and user configuration.
        """

        jobs_matching_trigger = self.get_jobs_matching_event()

        handlers_triggered_by_job = self.get_handlers_for_comment_and_rerun_event()

        matching_handlers: set[type[JobHandler]] = set()
        for job in jobs_matching_trigger:
            for handler in (
                MAP_JOB_TYPE_TO_HANDLER[job.type] | MAP_REQUIRED_JOB_TYPE_TO_HANDLER[job.type]
            ):
                if self.is_handler_matching_the_event(
                    handler=handler,
                    allowed_handlers=handlers_triggered_by_job,
                ):
                    matching_handlers.add(handler)

        if not matching_handlers:
            logger.debug(
                f"We did not find any handler for a following event:\n{
                    self.event.event_type()}",
            )

        logger.debug(f"Matching handlers: {matching_handlers}")

        return matching_handlers

    def is_handler_matching_the_event(
        self,
        handler: type[JobHandler],
        allowed_handlers: set[type[JobHandler]],
    ) -> bool:
        """
        Decides whether handler matches to comment or check rerun job and given event
        supports handler.

        Args:
            handler: Handler which we are observing whether it is matching to job.
            allowed_handlers: Set of handlers that are triggered by a comment or check rerun
             job.

        Returns:
            `True` if handler matches the event, `False` otherwise.
        """
        handler_matches_to_comment_or_check_rerun_job = (
            allowed_handlers is None or handler in allowed_handlers
        )

        return (
            isinstance(self.event, tuple(
                SUPPORTED_EVENTS_FOR_HANDLER[handler]))
            and handler_matches_to_comment_or_check_rerun_job
        )

    def get_config_for_handler_kls(
        self,
        handler_kls: type[JobHandler],
    ) -> list[JobConfig]:
        """
        Get a list of JobConfigs relevant to event and the handler class.

        We need to find all job configurations that:
        - can be run by the given handler class, **and**
        - that matches the trigger of the event

        If there is no matching job-config found, we will pick the ones that are required.
        e.g.: For build handler, you can pick the test config since tests require the build.

        Examples of the matching can be found in the tests:
        ./tests/unit/test_jobs.py:test_get_config_for_handler_kls

        Args:
            handler_kls: class that will use the JobConfig

        Returns:
            List of JobConfigs relevant to the given handler and event
            preserving the order in the config.
        """
        jobs_matching_trigger: list[JobConfig] = self.get_jobs_matching_event()

        matching_jobs: list[JobConfig] = [
            job for job in jobs_matching_trigger if handler_kls in MAP_JOB_TYPE_TO_HANDLER[job.type]
        ]

        if not matching_jobs:
            logger.debug(
                "No config found, let's see the jobs that requires this handler.",
            )
            matching_jobs = [
                job
                for job in jobs_matching_trigger
                if handler_kls in MAP_REQUIRED_JOB_TYPE_TO_HANDLER[job.type]
            ]

        if not matching_jobs:
            logger.warning(
                f"We did not find any config for {
                    handler_kls} and a following event:\n"
                f"{self.event.event_type()}",
            )

        logger.debug(
            "Jobs matching %s: %s",
            handler_kls.__qualname__,
            [str(j) for j in matching_jobs],
        )

        return matching_jobs

    def push_statuses_metrics(
        self,
        statuses_check_feedback: list[datetime],
    ) -> None:
        """
        Push the metrics about the time of setting initial statuses for the first and last check.

        Args:
            statuses_check_feedback: A list of times it takes to set every initial status check.
        """
        if not statuses_check_feedback:
            # no feedback, nothing to do
            return

        response_time = elapsed_seconds(
            begin=self.event.created_at,
            end=statuses_check_feedback[0],
        )
        logger.debug(
            f"Reporting first initial status check time: {
                response_time} seconds.",
        )
        self.pushgateway.first_initial_status_time.observe(response_time)
        if response_time > 25:
            self.pushgateway.no_status_after_25_s.inc()
        if response_time > 15:
            # https://github.com/packit/packit-service/issues/1728
            # we need more info why this has happened
            logger.debug(f"Event dict: {self.event}.")
            logger.error(
                f"Event {self.event.event_type(
                )} took more than 15s to process.",
            )
        # set the time when the accepted status was set so that we
        # can use it later for measurements
        self.event.task_accepted_time = statuses_check_feedback[0]

        response_time = elapsed_seconds(
            begin=self.event.created_at,
            end=statuses_check_feedback[-1],
        )
        logger.debug(
            f"Reporting last initial status check time: {
                response_time} seconds.",
        )
        self.pushgateway.last_initial_status_time.observe(response_time)

    def push_copr_metrics(
        self,
        handler_kls: type[JobHandler],
        built_targets: int = 0,
    ) -> None:
        """
        Push metrics about queued Copr builds.

        Args:
            handler_kls: The class for the Handler that will handle the job.
            built_targets: Number of build targets in case of CoprBuildHandler.
        """
        # TODO(Friday): Do an early-return, but fix »all« **36** f-ing tests
        if handler_kls == CoprBuildHandler and built_targets:
            # handler wasn't matched or 0 targets were built
            self.pushgateway.copr_builds_queued.inc(built_targets)

    def is_fas_verification_comment(self, comment: str) -> bool:
        """
        Checks whether the comment contains Packit verification command:
        `/packit(-stg) verify-fas`

        Args:
            comment: Comment to be checked.

        Returns:
            `True`, if is verification comment, `False` otherwise.
        """
        command = get_packit_commands_from_comment(
            comment,
            self.service_config.comment_command_prefix,
        )

        return bool(command and command[0] == PACKIT_VERIFY_FAS_COMMAND)
