# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import logging
import re
from abc import abstractmethod
from pathlib import Path
from typing import Optional, Protocol, Union

from fasjson_client import Client
from fasjson_client.errors import APIError
from packit.api import PackitAPI
from packit.local_project import CALCULATE, LocalProject, LocalProjectBuilder
from packit.utils.repo import RepositoryCache

from ogr.abstract import GitProject, Issue, PullRequest
from packit_service.config import ServiceConfig
from packit_service.constants import (
    FASJSON_URL,
    SANDCASTLE_DG_REPO_DIR,
    SANDCASTLE_LOCAL_PROJECT_DIR,
)
from packit_service.events.event_data import EventData
from packit_service.worker.helpers.job_helper import BaseJobHelper
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
    def service_config(self) -> ServiceConfig:
        if not self._service_config:
            self._service_config = ServiceConfig.get_service_config()
        return self._service_config

    @property
    def project(self) -> Optional[GitProject]:
        if not self._project and self.data.project_url:
            self._project = self.service_config.get_project(url=self.data.project_url)
        return self._project

    @property
    def project_url(self) -> str:
        return self.data.project_url


class ConfigFromUrlMixin(Config):
    _project: Optional[GitProject] = None
    _service_config: Optional[ServiceConfig] = None
    _project_required: bool = True
    _project_url: str
    data: EventData

    @property
    def service_config(self) -> ServiceConfig:
        if not self._service_config:
            self._service_config = ServiceConfig.get_service_config()
        return self._service_config

    @property
    def project(self) -> Optional[GitProject]:
        if not self._project and self.project_url:
            self._project = self.service_config.get_project(
                url=self.project_url,
                required=self._project_required,
            )
        return self._project

    @property
    def project_url(self) -> str:
        return self._project_url


class ConfigFromDistGitUrlMixin(Config):
    _project: Optional[GitProject] = None
    _service_config: Optional[ServiceConfig] = None
    _project_url: str
    data: EventData

    @property
    def service_config(self) -> ServiceConfig:
        if not self._service_config:
            self._service_config = ServiceConfig.get_service_config()
        return self._service_config

    @property
    def project(self) -> Optional[GitProject]:
        if not self._project and self.data.event_dict["dist_git_project_url"]:
            self._project = self.service_config.get_project(url=self.project_url)
        return self._project

    @property
    def project_url(self) -> str:
        return self.data.event_dict["dist_git_project_url"]


class PackitAPIProtocol(Config):
    local_project: Optional[LocalProject] = None

    @property
    @abstractmethod
    def packit_api(self) -> PackitAPI: ...

    @abstractmethod
    def clean_api(self) -> None: ...


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


class PackitAPIWithUpstreamMixin(PackitAPIProtocol):
    _packit_api: Optional[PackitAPI] = None

    @property
    def packit_api(self):
        if not self._packit_api:
            self._packit_api = PackitAPI(
                self.service_config,
                self.job_config,
                upstream_local_project=self.local_project,
                dist_git_clone_path=Path(self.service_config.command_handler_work_dir)
                / SANDCASTLE_DG_REPO_DIR,
                non_git_upstream=self.non_git_upstream,
            )
        return self._packit_api

    @property
    def non_git_upstream(self):
        return self.check_for_non_git_upstreams and self.job_config.upstream_project_url is None

    def clean_api(self) -> None:
        if self._packit_api:
            self._packit_api.clean()


class LocalProjectMixin(Config):
    _local_project: Optional[LocalProject] = None

    @property
    def local_project(self) -> LocalProject:
        if not self._local_project:
            builder = LocalProjectBuilder(
                cache=(
                    RepositoryCache(
                        cache_path=self.service_config.repository_cache,
                        add_new=self.service_config.add_repositories_to_repository_cache,
                    )
                    if self.service_config.repository_cache
                    else None
                ),
            )
            working_dir = Path(
                Path(self.service_config.command_handler_work_dir) / SANDCASTLE_LOCAL_PROJECT_DIR,
            )
            kwargs = {
                "repo_name": CALCULATE,
                "full_name": CALCULATE,
                "namespace": CALCULATE,
                "working_dir": working_dir,
                "git_repo": CALCULATE,
            }

            if self.project:
                kwargs["git_project"] = self.project
            else:
                kwargs["git_url"] = self.project_url

            self._local_project = builder.build(**kwargs)

        return self._local_project


class GetIssue(Protocol):
    @property
    @abstractmethod
    def issue(self) -> Issue: ...


class GetIssueMixin(GetIssue, ConfigFromEventMixin):
    _issue: Optional[Issue] = None

    @property
    def issue(self):
        if not self._issue:
            self._issue = self.project.get_issue(self.data.issue_id)
        return self._issue


class GetBranches(Protocol):
    @property
    @abstractmethod
    def branches(self) -> list[str]: ...


class GetReporter(Protocol):
    @abstractmethod
    def report(
        self,
        state: BaseCommitStatus,
        description: str,
        url: str = "",
        check_names: Union[str, list, None] = None,
        markdown_content: Optional[str] = None,
    ) -> None: ...


class GetReporterFromJobHelperMixin(Config):
    _job_helper: BaseJobHelper = None

    @property
    def job_helper(self):
        if not self._job_helper:
            self._job_helper = BaseJobHelper(
                self.service_config,
                self.package_config,
                self.project,
                self.data,
                self.data.db_project_event,
                self.job_config,
                None,
            )
        return self._job_helper

    def report(
        self,
        state: BaseCommitStatus,
        description: str,
        url: str = "",
        check_names: Union[str, list, None] = None,
        markdown_content: Optional[str] = None,
    ) -> None:
        self.job_helper._report(state, description, url, check_names, markdown_content)
