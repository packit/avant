# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

from packit_service.events import forgejo
from packit_service.worker.checker.submit import IsUserMaintainer
from packit_service.worker.handlers.abstract import (
    FedoraCIJobHandler,
    TaskName,
    reacts_to_as_fedora_ci,
    run_for_comment_as_fedora_ci,
)
from packit_service.worker.mixin import ConfigFromEventMixin, PackitAPIWithDownstreamMixin
from packit_service.worker.result import TaskResults


# Placeholder handler for package sync to dist git, checks if the actor is maintainer or not.
@run_for_comment_as_fedora_ci(command="sync-package")
@reacts_to_as_fedora_ci(event=forgejo.pr.Comment)
class SubmitPackageHandler(FedoraCIJobHandler, PackitAPIWithDownstreamMixin, ConfigFromEventMixin):
    task_name = TaskName.submit_package
    check_name = "sync-package"

    @staticmethod
    def get_checkers():
        return (IsUserMaintainer,)

    def run(self) -> TaskResults:
        msg = "Syncing package to dist-git"
        return TaskResults(success=True, details={"msg": msg})
