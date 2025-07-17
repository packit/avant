from packit_service.worker.handlers.abstract import JobHandler, reacts_to, TaskName
from packit_service.events.forgejo.pr import Action as ForgejoPrAction
from packit_service.worker.result import TaskResults
import random
import logging
from packit_service.config import ServiceConfig
from typing import Optional
from ogr.services.forgejo.project import ForgejoProject
from ogr.services.forgejo.service import ForgejoService
from ogr.abstract import GitProject

logger = logging.getLogger(__name__)


class ForgejoNewPrHandler(JobHandler):
    # task_name = TaskName.forgejo_new_pr

    @property
    def service_config(self) -> Optional[ServiceConfig]:
        return ServiceConfig.get_service_config()

    @property
    def project(self) -> Optional[GitProject]:
        if hasattr(self, 'repo_name') and hasattr(self, 'namespace') and self.repo_name and self.namespace:
            return ForgejoProject(repo=str(self.repo_name), namespace=str(self.namespace), service=ForgejoService(instance_url="https://codeberg.org"))
        return None

    def run(self):
        # Extract info from the event
        event = getattr(self.data, 'event_dict', None)
        if not event:
            logger.warning("No event_dict found in self.data.")
            return TaskResults(success=False, details={"msg": "No event_dict found."})
        # These keys are set by the event's get_dict()
        repo_url = event.get("project_url")
        self.repo_name = event.get("target_repo_name")
        self.namespace = event.get("target_repo_namespace")
        package_name = self.repo_name
        package_author = self.namespace
        package_version = str(random.randint(1000, 9999))
        logger.info(f"ForgejoNewPrHandler: repo_url={repo_url}, package_name={
                    package_name}, package_author={package_author}, package_version={package_version}")
        return TaskResults(
            success=True,
            details={
                "msg": "Forgejo PR Action handled.",
                "repo_url": repo_url,
                "package_name": package_name,
                "package_author": package_author,
                "package_version": package_version,
            },
        )

    def get_package_name(self) -> Optional[str]:
        event = getattr(self.data, 'event_dict', None)
        if event:
            return event.get("target_repo_name")
        return None

    def clean_api(self):
        return None

    def packit_api(self):
        return None

    def project_url(self) -> str:
        return f"https://codeberg.org/{self.namespace}/{self.repo_name}"

    def get_package_config(self):
        return None
