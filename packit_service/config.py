# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import logging
import os
from pathlib import Path
from typing import NamedTuple, Optional, Union

from packit.config import (
    Config,
    RunCommandType,
)
from packit.config.common_package_config import Deployment
from packit.exceptions import (
    PackitException,
)
from yaml import safe_load

from packit_service.constants import (
    CONFIG_FILE_NAME,
    SANDCASTLE_DEFAULT_PROJECT,
    SANDCASTLE_IMAGE,
    SANDCASTLE_PVC,
    SANDCASTLE_WORK_DIR,
)

logger = logging.getLogger(__name__)


class MRTarget(NamedTuple):
    """
    A pair of repo and branch regexes.
    """

    repo: str
    branch: str

    def __repr__(self):
        return f"MRTarget(repo={self.repo}, branch={self.branch})"

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, MRTarget):
            raise NotImplementedError()

        return self.repo == other.repo and self.branch == other.branch


class ServiceConfig(Config):
    def __init__(
        self,
        deployment: Deployment = Deployment.stg,
        webhook_secret: str = "",
        validate_webhooks: bool = True,
        admins: Optional[list] = None,
        enabled_private_namespaces: Optional[Union[set[str], list[str]]] = None,
        gitlab_token_secret: str = "",
        gitlab_mr_targets_handled: Optional[list[MRTarget]] = None,
        enabled_projects_for_srpm_in_copr: Optional[Union[set[str], list[str]]] = None,
        comment_command_prefix: str = "/packit",
        package_config_path_override: Optional[str] = None,
        command_handler_storage_class: Optional[str] = None,
        appcode: Optional[str] = None,
        **kwargs,
    ):
        super().__init__(**kwargs)

        self.deployment = deployment
        self.webhook_secret = webhook_secret
        self.validate_webhooks = validate_webhooks

        # List of github users who are allowed to trigger p-s on any repository
        self.admins: set[str] = set(admins or [])

        # for flask SERVER_NAME so we can create links to logs
        self.server_name: str = ""

        # Gitlab token secret to decode JWT tokens
        self.gitlab_token_secret: str = gitlab_token_secret

        self.gitlab_mr_targets_handled: list[MRTarget] = gitlab_mr_targets_handled or []

        # Explicit list of private namespaces we work with (e.g. github.com/org, gitlab.com/group)
        self.enabled_private_namespaces: set[str] = set(
            enabled_private_namespaces or [],
        )

        self.enabled_projects_for_srpm_in_copr: set[str] = set(
            enabled_projects_for_srpm_in_copr or [],
        )
        self.comment_command_prefix = comment_command_prefix

        # Package config path to use, instead of searching for the default names.
        self.package_config_path_override = package_config_path_override

        # Storage class that is used for temporary volumes used by Sandcastle
        self.command_handler_storage_class = command_handler_storage_class

        # Appcode used in MP+ to differentiate applications
        self.appcode = appcode

    service_config = None

    def __repr__(self):
        def hide(token: str) -> str:
            token = token or ""
            return f"{token[:1]}***{token[-1:]}" if token else ""

        return (
            f"{self.__class__.__name__}("
            f"{super().__repr__()}, "
            f"deployment='{self.deployment}', "
            f"webhook_secret='{hide(self.webhook_secret or '')}', "
            f"validate_webhooks='{self.validate_webhooks}', "
            f"admins='{self.admins}', "
            f"gitlab_token_secret='{hide(self.gitlab_token_secret or '')}',"
            f"gitlab_mr_targets_handled='{self.gitlab_mr_targets_handled}', "
            f"enabled_private_namespaces='{self.enabled_private_namespaces}', "
            f"server_name='{self.server_name}', "
            f"enabled_projects_for_srpm_in_copr= '{self.enabled_projects_for_srpm_in_copr}', "
            f"comment_command_prefix='{self.comment_command_prefix}', "
            f"package_config_path_override='{self.package_config_path_override}', "
            f"appcode='{self.appcode}')"
        )

    @classmethod
    def get_from_dict(cls, raw_dict: dict) -> "ServiceConfig":
        # required to avoid circular imports
        from packit_service.schema import ServiceConfigSchema

        config = ServiceConfigSchema().load(raw_dict)

        if not isinstance(config, ServiceConfig):
            raise PackitException("Loaded config is not a ServiceConfig instance.")
        config.server_name = raw_dict.get("server_name", "localhost:5000")
        config.command_handler = RunCommandType.local
        a_h = raw_dict.get("command_handler")
        if a_h:
            config.command_handler = RunCommandType(a_h)
        config.command_handler_work_dir = raw_dict.get(
            "command_handler_work_dir",
            SANDCASTLE_WORK_DIR,
        )
        config.command_handler_pvc_env_var = raw_dict.get(
            "command_handler_pvc_env_var",
            SANDCASTLE_PVC,
        )
        config.command_handler_image_reference = raw_dict.get(
            "command_handler_image_reference",
            SANDCASTLE_IMAGE,
        )
        # default project for oc cluster up
        config.command_handler_k8s_namespace = raw_dict.get(
            "command_handler_k8s_namespace",
            SANDCASTLE_DEFAULT_PROJECT,
        )

        logger.debug(f"Loaded config: {config}")
        return config

    @classmethod
    def get_service_config(cls) -> "ServiceConfig":
        if cls.service_config is None:
            config_file = os.getenv(
                "PACKIT_SERVICE_CONFIG",
                Path.home() / ".config" / CONFIG_FILE_NAME,
            )
            logger.debug(f"Loading service config from: {config_file}")

            try:
                with open(config_file) as file_stream:
                    loaded_config = safe_load(file_stream)
            except Exception as ex:
                logger.error(f"Cannot load service config '{config_file}'.")
                raise PackitException(f"Cannot load service config: {ex}.") from ex

            cls.service_config = ServiceConfig.get_from_dict(raw_dict=loaded_config)
        return cls.service_config

    def get_github_account_name(self) -> str:
        return {
            Deployment.prod: "packit-as-a-service[bot]",
            Deployment.stg: "packit-as-a-service-stg[bot]",
            Deployment.dev: "packit-as-a-service-dev[bot]",
        }.get(self.deployment, "packit-as-a-service[bot]")
