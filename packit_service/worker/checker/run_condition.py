# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import logging
import shutil
import tempfile
from pathlib import Path
from typing import Optional

from packit.actions_handler import ActionsHandler
from packit.command_handler import (
    RUN_COMMAND_HANDLER_MAPPING,
    CommandHandler,
    SandcastleCommandHandler,
)
from packit.config import JobConfig, PackageConfig

from packit_service.worker.checker.abstract import Checker
from packit_service.worker.mixin import ConfigFromEventMixin, PackitAPIWithUpstreamMixin

logger = logging.getLogger(__name__)


class IsRunConditionSatisfied(Checker, ConfigFromEventMixin, PackitAPIWithUpstreamMixin):
    def __init__(
        self,
        package_config: PackageConfig,
        job_config: JobConfig,
        event: dict,
        task_name: Optional[str] = None,
    ):
        super().__init__(
            package_config=package_config,
            job_config=job_config,
            event=event,
            task_name=task_name,
        )
        self._handler_kls = None
        self._working_dir: Optional[Path] = None
        self._command_handler: Optional[CommandHandler] = None
        self._actions_handler: Optional[ActionsHandler] = None

    @property
    def handler_kls(self):
        if self._handler_kls is None:
            logger.debug(f"Command handler: {self.service_config.command_handler}")
            self._handler_kls = RUN_COMMAND_HANDLER_MAPPING[self.service_config.command_handler]
        return self._handler_kls

    @property
    def working_dir(self) -> Optional[Path]:
        if not self._working_dir:
            if self.handler_kls == SandcastleCommandHandler:
                path = (
                    Path(self.service_config.command_handler_work_dir) / "run-condition-working-dir"
                )
                path.mkdir(parents=True, exist_ok=True)
                self._working_dir = path
            else:
                self._working_dir = Path(tempfile.mkdtemp())
            logger.info(
                f"Created directory for the run-condition action: {self._working_dir}",
            )
        return self._working_dir

    @property
    def command_handler(self) -> CommandHandler:
        if self._command_handler is None:
            self._command_handler = self.handler_kls(
                config=self.service_config,
                working_dir=self.working_dir,
            )
        return self._command_handler

    @property
    def actions_handler(self) -> ActionsHandler:
        if not self._actions_handler:
            self._actions_handler = ActionsHandler(
                self.job_config,
                self.command_handler,
            )
        return self._actions_handler

    def common_env(
        self, version: Optional[str] = None, extra_env: Optional[dict[str, str]] = None
    ) -> dict[str, str]:
        env = self.job_config.get_base_env()
        if version:
            env["PACKIT_PROJECT_VERSION"] = version
        if extra_env:
            env.update(extra_env)
        return env

    def clean_working_dir(self) -> None:
        if self.job_config.clone_repos_before_run_condition:
            self.packit_api.up.clean_working_dir()
        else:
            if self._working_dir:
                logger.debug(f"Cleaning: {self.working_dir}")
                shutil.rmtree(self.working_dir, ignore_errors=True)
