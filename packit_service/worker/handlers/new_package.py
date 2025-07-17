import logging
from packit_service.worker.handlers.abstract import (
    FedoraCIJobHandler, TaskName, run_for_comment_as_fedora_ci, reacts_to_as_fedora_ci
)
from packit_service.worker.result import TaskResults
from packit_service.events.pagure.pr import Comment as PagurePRComment

logger = logging.getLogger(__name__)

@reacts_to_as_fedora_ci(event=PagurePRComment)
class FedoraCINewPackageHandler(FedoraCIJobHandler):
    task_name = TaskName.new_package

    def __init__(self, package_config, job_config, event, celery_task=None):
        super().__init__(package_config, job_config, event)
        self.celery_task = celery_task

    def run(self) -> TaskResults:
        logger.info("FedoraCINewPackageHandler triggered via Fedora CI comment command.")
        logger.info(f"Event: {self.data.event_dict}")
        # Stub logic: just log and return success
        return TaskResults(
            success=True,
            details={
                "msg": "Fedora CI new-package command processed successfully.",
                "event": self.data.event_dict,
                "handler": self.__class__.__name__,
            },
        )

