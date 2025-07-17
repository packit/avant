import logging
from typing import Optional
from enum import Enum
import celery

from packit_service.events.event import Event
from packit.config import (PackageConfig, JobConfig,
                           JobType, JobConfigTriggerType, CommonPackageConfig)

from packit_service.worker.handlers import JobHandler, CoprBuildHandler
from packit_service.worker.handlers.abstract import (
    SUPPORTED_EVENTS_FOR_HANDLER, MAP_COMMENT_TO_HANDLER)
from packit_service.utils import get_packit_commands_from_comment
from packit_service.config import ServiceConfig
from packit_service.worker.helpers.build import CoprBuildJobHelper
from packit_service.worker.result import TaskResults
from packit_service.events import abstract

logger = logging.getLogger(__name__)


def enum_to_str(obj):
    if isinstance(obj, dict):
        return {k: enum_to_str(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [enum_to_str(i) for i in obj]
    elif isinstance(obj, Enum):
        return obj.value
    else:
        return obj


DUMMY_COMMON_PACKAGE = CommonPackageConfig(
    _targets=["fedora-rawhide-x86_64"],
    owner="dummy-owner",
)
DUMMY_JOB_CONFIG = JobConfig(
    type=JobType.copr_build,
    trigger=JobConfigTriggerType.pull_request,
    packages={"dummy": DUMMY_COMMON_PACKAGE},
)
DUMMY_PACKAGE_CONFIG = PackageConfig(
    jobs=[DUMMY_JOB_CONFIG],
    packages={"dummy": DUMMY_COMMON_PACKAGE},
)


class MiniJobs:
    def __init__(self, event: Event):
        self.event: Event = event

    def _make_signature(self, handler_cls, event, job):
        try:
            job_helper = self.initialize_job_helper(handler_cls)
            signature = handler_cls.get_signature(event=event, job=job)
            if signature.kwargs:
                for key in ("event", "package_config", "job_config"):
                    if key in signature.kwargs:
                        signature.kwargs[key] = enum_to_str(
                            signature.kwargs[key])
            return signature, None
        except Exception as ex:
            logger.error(f"Failed to create signature for handler {
                         handler_cls.__name__}: {ex}")
            return None, str(ex)

    def _process_comment_event(self) -> list[TaskResults]:
        results = []
        signatures = []
        try:
            comment_command_prefix = ServiceConfig.get_service_config().comment_command_prefix
            commands = get_packit_commands_from_comment(
                self.event.comment, comment_command_prefix)
            if not commands:
                msg = "No recognized Packit command found in the comment."
                logger.info(msg)
                return [TaskResults(success=True, details={"msg": msg})]

            handlers = MAP_COMMENT_TO_HANDLER.get(commands[0], set())
            if not handlers:
                msg = f"No handler found for command: {commands[0]}"
                logger.info(msg)
                return [TaskResults(success=True, details={"msg": msg})]

            for handler_cls in handlers:
                logger.info(f"MiniJobs: Creating Celery task for handler {
                            handler_cls.__name__} for command {commands[0]}")
                signature, error = self._make_signature(
                    handler_cls, self.event, DUMMY_JOB_CONFIG)
                if signature:
                    signatures.append(signature)
                    results.append(TaskResults(success=True, details={
                                   "msg": f"Task created for handler {handler_cls.__name__}"}))
                else:
                    results.append(TaskResults(success=False, details={
                                   "msg": f"Failed to create task for handler {handler_cls.__name__}: {error}"}))
        except Exception as ex:
            logger.error(f"Error processing comment event: {ex}")
            return [TaskResults(success=False, details={"msg": f"Error processing comment event: {ex}"})]

        self._send_to_celery(signatures)
        return results

    def _process_non_comment_event(self) -> list[TaskResults]:
        results = []
        signatures = []
        any_handler = False
        try:
            for handler_cls, supported_events in SUPPORTED_EVENTS_FOR_HANDLER.items():
                if any(isinstance(self.event, event_type) for event_type in supported_events):
                    any_handler = True
                    logger.info(f"MiniJobs: Creating Celery task for handler {
                                handler_cls.__name__} for event {type(self.event).__name__}")
                    signature, error = self._make_signature(
                        handler_cls, self.event, DUMMY_JOB_CONFIG)
                    if signature:
                        signatures.append(signature)
                        results.append(TaskResults(success=True, details={
                                       "msg": f"Task created for handler {handler_cls.__name__}"}))
                    else:
                        results.append(TaskResults(success=False, details={
                                       "msg": f"Failed to create task for handler {handler_cls.__name__}: {error}"}))
            if not any_handler:
                msg = f"No handler found for event: {
                    type(self.event).__name__}"
                logger.info(msg)
                return [TaskResults(success=True, details={"msg": msg})]
        except Exception as ex:
            logger.error(f"Error processing non-comment event: {ex}")
            return [TaskResults(success=False, details={"msg": f"Error processing event: {ex}"})]

        self._send_to_celery(signatures)
        return results

    def _send_to_celery(self, signatures):
        if signatures:
            try:
                logger.info(f"MiniJobs: Sending {
                            len(signatures)} signatures to Celery")
                celery.group(signatures).apply_async()
                logger.info(
                    "MiniJobs: Signatures were sent to Celery successfully")
            except Exception as ex:
                logger.error(f"Failed to send signatures to Celery: {ex}")

    def process(self) -> list[TaskResults]:
        try:
            if isinstance(self.event, abstract.comment.CommentEvent):
                return self._process_comment_event()
            else:
                return self._process_non_comment_event()
        except Exception as ex:
            logger.error(f"Unexpected error in MiniJobs.process(): {ex}")
            return [TaskResults(success=False, details={"msg": f"Unexpected error: {ex}"})]

    @classmethod
    def process_message(
            cls,
            event: dict,
            source: Optional[str] = None,
            event_type: Optional[str] = None,
    ) -> list[TaskResults]:
        try:
            from packit_service.worker.parser import Parser
            from packit.utils import nested_get

            parser = nested_get(
                Parser.MAPPING,
                source,
                event_type,
                default=Parser.parse_event,
            )
            event_object = parser(event)
            if not event_object:
                logger.info(
                    "Event could not be parsed, returning empty results")
                return []

            if not event_object.pre_check():
                logger.info("Event pre-check failed, returning empty results")
                return []

            mini_jobs = cls(event_object)
            return mini_jobs.process()
        except Exception as ex:
            logger.error(f"Error in MiniJobs.process_message: {ex}")
            return [TaskResults(success=False, details={"msg": f"Error in process_message: {ex}"})]    def initialize_job_helper(
            self,
            handler_cls: type[JobHandler],
            job_config: JobConfig
    ):
        params = {
            "service_config": ServiceConfig.get_service_config(),
            "package_config": DUMMY_PACKAGE_CONFIG,
            "project": self.event.project if hasattr(self.event, "project") else None,
            "metadata": self.event.get_dict() if hasattr(self.event, "get_dict") else None,
            "job_config": job_config
        }

        if handler_cls == CoprBuildHandler:
            helper_cls = CoprBuildJobHelper

        params.update({
            "build_id": self.event.build_id if hasattr(self.event, "build_id") else None,
            "build_target": self.event.build_target if hasattr(self.event, "build_target") else None,
        })
        return helper_cls(**params)

