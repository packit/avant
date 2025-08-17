import logging

from ogr.services.forgejo.project import ForgejoProject
from packit_service.events.forgejo.abstract import ForgejoEvent
from packit_service.worker.checker.abstract import Checker
from packit_service.events import (
    forgejo
)

logger = logging.getLogger(__name__)

class IsUserMaintainer(Checker):
    def pre_check(self) -> bool:
        project: ForgejoProject = self.data.get_project()
        maintainers = project.get_contributors()
        if (
            self.data.event_type == forgejo.pr.Comment.event_type()
            and self.data.actor in maintainers
        ):
            return True

        logger.debug(f"Not a maintainer: {self.data.actor}")
        return False
