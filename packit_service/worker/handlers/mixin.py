# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import logging
from abc import abstractmethod
from typing import Any, Optional, Protocol, Union

from packit.config import JobConfig, PackageConfig
from packit.vm_image_build import ImageBuilder

from packit_service.constants import COPR_SRPM_CHROOT
from packit_service.events import (
    copr,
    github,
    gitlab,
    pagure,
)
from packit_service.events.event_data import EventData
from packit_service.models import (
    BuildStatus,
    CoprBuildTargetModel,
    ProjectEventModel,
    SRPMBuildModel,
)
from packit_service.utils import get_packit_commands_from_comment
from packit_service.worker.handlers.abstract import CeleryTask
from packit_service.worker.helpers.build.copr_build import CoprBuildJobHelper
from packit_service.worker.mixin import Config, ConfigFromEventMixin
from packit_service.worker.monitoring import Pushgateway

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
    pushgateway: Optional[Pushgateway] = None

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
                pushgateway=self.pushgateway,
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
                pushgateway=self.pushgateway,
                build_targets_override=build_targets_override,
            )
        return self._copr_build_helper

class GetGithubCommentEvent(Protocol):
    @abstractmethod
    def is_comment_event(self) -> bool: ...

    @abstractmethod
    def is_copr_build_comment_event(self) -> bool: ...


class GetGithubCommentEventMixin(GetGithubCommentEvent, ConfigFromEventMixin):
    def is_comment_event(self) -> bool:
        return self.data.event_type in (
            github.pr.Comment.event_type(),
            gitlab.mr.Comment.event_type(),
            pagure.pr.Comment.event_type(),
        )

    def is_copr_build_comment_event(self) -> bool:
        return self.is_comment_event() and get_packit_commands_from_comment(
            self.data.event_dict.get("comment"),
            packit_comment_command_prefix=self.service_config.comment_command_prefix,
        )[0] in ("build", "copr-build")


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


class GetVMImageBuilderMixin(Config):
    _vm_image_builder: Optional[ImageBuilder] = None

    @property
    def vm_image_builder(self):
        if not self._vm_image_builder:
            self._vm_image_builder = ImageBuilder(
                self.service_config.redhat_api_refresh_token,
            )
        return self._vm_image_builder


class GetVMImageDataMixin(Config, GetCoprBuildJobHelper):
    job_config: JobConfig
    _copr_build: Optional[CoprBuildTargetModel] = None
    _copr_build_helper: Optional[CoprBuildJobHelper] = None

    @property
    def chroot(self) -> str:
        return self.job_config.copr_chroot

    @property
    def identifier(self) -> str:
        return self.job_config.identifier

    @property
    def owner(self) -> str:
        return self.job_config.owner or (self.copr_build.owner if self.copr_build else None)

    @property
    def project_name(self) -> str:
        return self.job_config.project or (
            self.copr_build.project_name if self.copr_build else None
        )

    @property
    def image_name(self) -> str:
        return f"{self.owner}/{self.project_name}/{self.data.pr_id}"

    @property
    def image_distribution(self) -> str:
        return self.job_config.image_distribution

    @property
    def image_request(self) -> dict:
        return self.job_config.image_request

    @property
    def image_customizations(self) -> dict:
        return self.job_config.image_customizations

    @property
    def copr_build(self) -> Optional[CoprBuildTargetModel]:
        if not self._copr_build:
            copr_builds = CoprBuildTargetModel.get_all_by(
                project_name=self.job_config.project or self.copr_build_helper.default_project_name,
                commit_sha=self.data.commit_sha,
                owner=self.job_config.owner or self.copr_build_helper.job_owner,
                target=self.job_config.copr_chroot,
                status=BuildStatus.success,
            )

            for copr_build in copr_builds:
                project_event_object = copr_build.get_project_event_object()
                # check whether the event trigger matches
                if project_event_object.id == self.data.db_project_object.id:
                    self._copr_build = copr_build
                    break
        return self._copr_build
