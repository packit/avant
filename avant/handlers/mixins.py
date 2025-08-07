from abc import abstractmethod
from typing import Optional, Protocol, Union

from ogr.abstract import GitProject
from packit.config import JobConfig, PackageConfig

from packit_service.config import ServiceConfig
from packit_service.events.event_data import EventData
from packit_service.worker.celery_task import CeleryTask
from packit_service.worker.reporting import BaseCommitStatus


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

