import logging
from abc import abstractmethod
from typing import Optional, Protocol, Union

from fasjson_client import Client
from fasjson_client.errors import APIError
from ogr.abstract import GitProject
from packit.api import PackitAPI
from packit.config import JobConfig, PackageConfig

from packit_service.config import ServiceConfig
from packit_service.constants import FASJSON_URL
from packit_service.events.event_data import EventData
from packit_service.worker.celery_task import CeleryTask
from packit_service.worker.helpers.build import CoprBuildJobHelper
from packit_service.worker.mixin import PackitAPIProtocol
from packit_service.worker.reporting import BaseCommitStatus

logger = logging.getLogger(__name__)

class Config(Protocol):
    data: EventData

    @property
    @abstractmethod
    def project(self) -> Optional[GitProject]: ...

    @property
    @abstractmethod
    def service_config(self) -> Optional[ServiceConfig]: ...

    @property
    @abstractmethod
    def project_url(self) -> str: ...


class ConfigFromEventMixin(Config):
    _project: Optional[GitProject] = None
    _service_config: Optional[ServiceConfig] = None
    data: EventData

    @property
    def project(self) -> Optional[GitProject]:
        if self._project is None:
            self._project = self.service_config.get_project(url=self.data.project_url)
        return self._project

    @property
    def service_config(self) -> Optional[ServiceConfig]:
        if not self._service_config:
            self._service_config = ServiceConfig.get_service_config()
        return self._service_config

    @property
    def project_url(self) -> str:
        return self.data.project_url


class GetReporter(Protocol):
    @abstractmethod
    def report(
        self,
        state: BaseCommitStatus,
        description: str = "",
        url: str = "",
        check_names: Union[str, list, None] = None,
        markdown_content: Optional[str] = None,
    ): ...


class GetCoprBuildJobHelper(Protocol):
    package_config: PackageConfig
    job_config: JobConfig
    celery_task: Optional[CeleryTask] = None

    @property
    @abstractmethod
    def copr_build_helper(self): ...


class GetCoprBuildJobHelperMixin(Config, GetCoprBuildJobHelper):
    _copr_build_helper: Optional[CoprBuildJobHelper] = None

    @property
    def copr_build_helper(self) -> CoprBuildJobHelper:
        if not self._copr_build_helper:
            self._copr_build_helper = CoprBuildJobHelper(
                service_config=self.service_config,
                package_config=self.package_config,
                project=self.project,
                metadata=self.data,
                db_project_event=self.data.db_project_event,
                job_config=self.job_config,
                build_targets_override=self.data.build_targets_override,
                tests_targets_override=self.data.tests_targets_override,
                pushgateway=self.pushgateway,
                celery_task=self.celery_task,
            )
        return self._copr_build_helper



class PackitAPIWithDownstreamProtocol(PackitAPIProtocol):
    _packit_api: Optional[PackitAPI] = None

    @abstractmethod
    def is_packager(self, user) -> bool:
        """Check that the given FAS user
        is a packager

        Args:
            user (str) FAS user account name
        Returns:
            true if a packager false otherwise
        """
        ...


class PackitAPIWithDownstreamMixin(PackitAPIWithDownstreamProtocol):
    _packit_api: Optional[PackitAPI] = None

    @property
    def packit_api(self):
        if not self._packit_api:
            self._packit_api = PackitAPI(
                self.service_config,
                self.job_config,
                downstream_local_project=self.local_project,
            )
        return self._packit_api

    def is_packager(self, user):
        self.packit_api.init_kerberos_ticket()
        client = Client(FASJSON_URL)
        try:
            groups = client.list_user_groups(username=user)
        except APIError:
            logger.debug(f"Unable to get groups for user {user}.")
            return False
        return "packager" in [group["groupname"] for group in groups.result]

    def clean_api(self) -> None:
        """TODO: probably we should clean something even here
        but for now let it do the same as before the refactoring
        """
