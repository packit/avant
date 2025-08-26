# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

from avant.events import forgejo
from avant.worker.checker.submit import IsUserMaintainer
from avant.worker.handlers.abstract import (
    FedoraCIJobHandler,
    AvantTaskName,
    reacts_to_as_fedora_ci,
    run_for_comment_as_fedora_ci,
)
from avant.worker.mixin import ConfigFromEventMixin, PackitAPIWithDownstreamMixin
from avant.worker.result import TaskResults


@run_for_comment_as_fedora_ci(command="sync-package")
@reacts_to_as_fedora_ci(event=forgejo.pr.Comment)
class SubmitPackageHandler(FedoraCIJobHandler, PackitAPIWithDownstreamMixin, ConfigFromEventMixin):
    task_name = AvantTaskName.submit_package
    check_name = "sync-package"

    @staticmethod
    def get_checkers():
        return (IsUserMaintainer,)

    def run(self) -> TaskResults:
        msg = "Syncing package to dist-git"
        return TaskResults(success=True, details={"msg": msg})
