import logging
from typing import Callable, List, Optional, Union

import celery
from celery import Task
from packit.config import JobConfig

from handlers.abstract import SUPPORTED_EVENTS_FOR_HANDLER, MAP_COMMENT_TO_HANDLER
from packit.utils import nested_get

from packit_service.constants import TASK_ACCEPTED
from packit_service.events import forgejo, testing_farm
from packit_service.events.event import Event
from packit_service.events.event_data import EventData
from packit_service.models import TFTTestRunTargetModel
from packit_service.utils import get_packit_commands_from_comment
from packit_service.worker.handlers import TestingFarmHandler, CoprBuildHandler
from packit_service.worker.handlers.abstract import JobHandler
from packit_service.worker.helpers.build import CoprBuildJobHelper
from packit_service.worker.helpers.testing_farm import TestingFarmJobHelper
from packit_service.worker.parser import Parser
from packit_service.worker.reporting import BaseCommitStatus
from packit_service.worker.result import TaskResults

logger = logging.getLogger(__name__)


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
    commands = get_packit_commands_from_comment(comment, packit_comment_command_prefix)
    if not commands:
        return set()

    handlers = MAP_COMMENT_TO_HANDLER[commands[0]]
    if not handlers:
        logger.debug(f"Command {commands[0]} not supported by packit.")
    return handlers


class Jobs:
    def __init__(self, event: Optional[Event] = None):
        self.event = event

    def get_handlers_for_event(self):
        matching_handlers = {
            handler
            for handler in SUPPORTED_EVENTS_FOR_HANDLER
            if isinstance(self.event, tuple(SUPPORTED_EVENTS_FOR_HANDLER[handler]))
        }

        if not matching_handlers:
            logger.debug(f"No handlers found for event {self.event}")

        logger.debug(f"Matching handlers: {matching_handlers}")

        return matching_handlers

    def process_message(
        self, event: dict, source: Optional[str] = None, event_type: Optional[str] = None
    ):
        processing_results = None
        parser = nested_get(Parser.MAPPING, source, event_type, default=Parser.parse_event)

        self.event = parser(event)

        if isinstance(
            self.event,
            (forgejo.pr.Action, forgejo.pr.Comment, testing_farm.Result)
            and self.event.db_project_object,
        ):
            processing_results = self.process_jobs()

        if processing_results is None:
            processing_results = [
                TaskResults.create_from(
                    success=True, msg="Job Created.", job_config=None, event=self.event
                )
            ]

        return processing_results

    def is_maintainer(self) -> bool:
        """
        Check before execution that the actor is maintainer or collaborator of the repository.
        """
        return True

    def process_jobs(self):
        if isinstance(self.event, forgejo.pr.Comment) and not get_handlers_for_comment(
            self.event.comment, packit_comment_command_prefix="/packit-ci"
        ):
            return [
                TaskResults(success=True, details={"msg": "No Packit command found in the comment"})
            ]

        handler_classes = self.get_handlers_for_event()
        if not handler_classes:
            logger.debug("No handler found for the given event")
            return []

        processing_results = []
        for kls in handler_classes:
            processing_results.extend(self.create_tasks(kls))
        return processing_results

    def create_tasks(self, handler_kls: type[JobHandler]) -> list[TaskResults]:
        signatures = []
        signatures.append(handler_kls.get_signature(event=self.event, job=None))
        celery.group(signatures).apply_async()
        logger.debug("Tasks were sent to Celery.")
        return signatures

    def report_task_accepted(
        self, handler_kls: type[JobHandler], job_config: JobConfig, update_feedback_time: Callable
    ):
        number_of_build_targets = None
        if handler_kls not in (CoprBuildHandler, TestingFarmHandler):
            return

        job_helper = self.initalize_job_helper_for_reporting(handler_kls, job_config)

        if isinstance(job_helper, CoprBuildJobHelper):
            number_of_build_targets = len(job_helper.build_targets)

        job_helper.report_status_to_configured_job(
            description=TASK_ACCEPTED,
            state=BaseCommitStatus.pending,
            url="",
            update_feedback_time=update_feedback_time,
        )

    def initalize_job_helper_for_reporting(
        self, handler_kls: type[JobHandler], job_config: JobConfig
    ):
        params = {
            "service_config": self.service_config,
            "package_config": None,
            "project": self.event.project,
            "metadata": EventData.from_event_dict(self.event.get_dict()),
            "db_project_object": self.event.db_project_object,
            "job_config": job_config,
        }

        helper_kls: type[Union[TestingFarmJobHelper, CoprBuildJobHelper]]

        if handler_kls == TestingFarmHandler:
            helper_kls = TestingFarmJobHelper
        elif handler_kls == CoprBuildHandler:
            helper_kls = CoprBuildJobHelper

        params.update(
            {
                "build_targets_override": self.event.build_targets_override,
                "test_targets_override": self.event.test_targets_override,
            },
        )

        return helper_kls
