# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import logging
from abc import abstractmethod
from typing import Optional, Protocol, Union

from packit.config import JobConfig, PackageConfig

from packit_service.config import ProjectToSync
from packit_service.constants import COPR_SRPM_CHROOT
from avant.events import (
    copr,
    forgejo,
)
from avant.events.event_data import EventData
from packit_service.models import (
    CoprBuildTargetModel,
    ProjectEventModel,
    SRPMBuildModel,
)
from avant.worker.utils import get_packit_commands_from_comment
from avant.worker.handlers.abstract import CeleryTask
from avant.worker.helpers.build.copr_build import CoprBuildJobHelper
from avant.worker.helpers.testing_farm import TestingFarmJobHelper
from avant.worker.mixin import Config, ConfigFromEventMixin

logger = logging.getLogger(__name__)


class GetCoprBuildEvent(Protocol):
    data: EventData

    @property
    @abstractmethod
    def copr_event(self) -> copr.CoprBuild: ...


class GetCoprBuildEventMixin(ConfigFromEventMixin, GetCoprBuildEvent):
    _copr_build_event: Optional[copr.CoprBuild] = None

    @property
    def copr_event(self):
        if not self._copr_build_event:
            self._copr_build_event = copr.CoprBuild.from_event_dict(
                self.data.event_dict,
            )
        return self._copr_build_event


class GetSRPMBuild(Protocol):
    @property
    @abstractmethod
    def build(self) -> Optional[SRPMBuildModel]: ...

    @property
    @abstractmethod
    def db_project_event(self) -> Optional[ProjectEventModel]: ...


class GetCoprSRPMBuildMixin(GetSRPMBuild, GetCoprBuildEventMixin):
    _build: Optional[Union[CoprBuildTargetModel, SRPMBuildModel]] = None
    _db_project_event: Optional[ProjectEventModel] = None

    @property
    def build(self):
        if not self._build:
            build_id = str(self.copr_event.build_id)
            if self.copr_event.chroot == COPR_SRPM_CHROOT:
                self._build = SRPMBuildModel.get_by_copr_build_id(build_id)
            else:
                self._build = CoprBuildTargetModel.get_by_build_id(
                    build_id,
                    self.copr_event.chroot,
                )
        return self._build

    @property
    def db_project_event(self) -> Optional[ProjectEventModel]:
        if not self._db_project_event:
            self._db_project_event = self.build.get_project_event_model()
        return self._db_project_event


class GetCoprBuild(Protocol):
    build_id: Optional[int] = None

    @property
    @abstractmethod
    def db_project_event(self) -> Optional[ProjectEventModel]: ...


class GetCoprBuildMixin(GetCoprBuild, ConfigFromEventMixin):
    _build: Optional[CoprBuildTargetModel] = None
    _db_project_event: Optional[ProjectEventModel] = None

    @property
    def db_project_event(self) -> Optional[ProjectEventModel]:
        if not self._db_project_event:
            # copr build end
            if self.build_id:
                build = CoprBuildTargetModel.get_by_id(self.build_id)
                self._db_project_event = build.get_project_event_model()
            # other events
            else:
                self._db_project_event = self.data.db_project_event
        return self._db_project_event


class GetCoprBuildJobHelper(Protocol):
    package_config: PackageConfig
    job_config: JobConfig
    celery_task: Optional[CeleryTask] = None

    @property
    @abstractmethod
    def copr_build_helper(self) -> CoprBuildJobHelper: ...


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
                celery_task=self.celery_task,
            )
        return self._copr_build_helper


class GetCoprBuildJobHelperForIdMixin(
    GetCoprBuildJobHelper,
    GetCoprSRPMBuildMixin,
    ConfigFromEventMixin,
):
    _copr_build_helper: Optional[CoprBuildJobHelper] = None

    @property
    def copr_build_helper(self) -> CoprBuildJobHelper:
        # when reporting state of SRPM build built in Copr
        build_targets_override = (
            {
                (build.target, build.identifier)
                for build in CoprBuildTargetModel.get_all_by_build_id(
                    str(self.copr_event.build_id),
                )
            }
            if self.copr_event.chroot == COPR_SRPM_CHROOT
            else None
        )
        if not self._copr_build_helper:
            self._copr_build_helper = CoprBuildJobHelper(
                service_config=self.service_config,
                package_config=self.package_config,
                project=self.project,
                metadata=self.data,
                db_project_event=self.db_project_event,
                job_config=self.job_config,
                build_targets_override=build_targets_override,
            )
        return self._copr_build_helper


class GetTestingFarmJobHelper(Protocol):
    package_config: PackageConfig
    job_config: JobConfig
    celery_task: Optional[CeleryTask] = None

    @property
    @abstractmethod
    def testing_farm_job_helper(self) -> TestingFarmJobHelper: ...


class GetTestingFarmJobHelperMixin(
    GetTestingFarmJobHelper,
    GetCoprBuildMixin,
    ConfigFromEventMixin,
):
    _testing_farm_job_helper: Optional[TestingFarmJobHelper] = None

    @property
    def testing_farm_job_helper(self) -> TestingFarmJobHelper:
        if not self._testing_farm_job_helper:
            self._testing_farm_job_helper = TestingFarmJobHelper(
                service_config=self.service_config,
                package_config=self.package_config,
                project=self.project,
                metadata=self.data,
                db_project_event=self.db_project_event,
                job_config=self.job_config,
                build_targets_override=self.data.build_targets_override,
                tests_targets_override=self.data.tests_targets_override,
                celery_task=self.celery_task,
            )
        return self._testing_farm_job_helper


class GetGithubCommentEvent(Protocol):
    @abstractmethod
    def is_comment_event(self) -> bool: ...

    @abstractmethod
    def is_copr_build_comment_event(self) -> bool: ...


class GetGithubCommentEventMixin(GetGithubCommentEvent, ConfigFromEventMixin):
    def is_comment_event(self) -> bool:
        return self.data.event_type in (forgejo.pr.Comment.event_type(),)

    def is_copr_build_comment_event(self) -> bool:
        return self.is_comment_event() and get_packit_commands_from_comment(
            self.data.event_dict.get("comment"),
            packit_comment_command_prefix=self.service_config.comment_command_prefix,
        )[0] in ("build", "copr-build")


class GetProjectToSync(Protocol):
    @property
    @abstractmethod
    def dg_repo_name(self) -> str: ...

    @property
    @abstractmethod
    def dg_branch(self) -> str: ...

    @property
    @abstractmethod
    def project_to_sync(self) -> Optional[ProjectToSync]: ...


class GetProjectToSyncMixin(ConfigFromEventMixin, GetProjectToSync):
    _project_to_sync: Optional[ProjectToSync] = None

    @property
    def dg_repo_name(self) -> str:
        return self.data.event_dict.get("repo_name")

    @property
    def dg_branch(self) -> str:
        return self.data.event_dict.get("git_ref")

    @property
    def project_to_sync(self) -> Optional[ProjectToSync]:
        if self._project_to_sync is None and (
            project_to_sync := self.service_config.get_project_to_sync(
                dg_repo_name=self.dg_repo_name,
                dg_branch=self.dg_branch,
            )
        ):
            self._project_to_sync = project_to_sync
        return self._project_to_sync


class GetVMImageBuilder(Protocol):
    @property
    @abstractmethod
    def vm_image_builder(self): ...


class GetVMImageData(Protocol):
    @property
    @abstractmethod
    def build_id(self) -> str: ...

    @property
    @abstractmethod
    def chroot(self) -> str: ...

    @property
    @abstractmethod
    def identifier(self) -> str: ...

    @property
    @abstractmethod
    def owner(self) -> str: ...

    @property
    @abstractmethod
    def project_name(self) -> str: ...

    @property
    @abstractmethod
    def image_distribution(self) -> str: ...

    @property
    @abstractmethod
    def image_request(self) -> dict: ...

    @property
    @abstractmethod
    def image_customizations(self) -> dict: ...
