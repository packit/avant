from packit_service.worker.checker.submit import IsUserMaintainer
from packit_service.worker.handlers.abstract import JobHandler, run_for_comment, reacts_to
from packit_service.worker.mixin import PackitAPIWithDownstreamMixin, ConfigFromEventMixin
from packit_service.worker.result import TaskResults
from packit_service.events import forgejo

@reacts_to(event=forgejo.pr.Action)
@run_for_comment("sync-package")
class SubmitPackageHandler(JobHandler, PackitAPIWithDownstreamMixin, ConfigFromEventMixin):
    @staticmethod
    def get_checkers():
        return (
            IsUserMaintainer
        )

    def run(self) -> TaskResults:
        msg = "Syncing package to dist-git"
        return TaskResults(success=True, details={"msg" : msg})

    def run_n_clean(self) -> TaskResults:
        pass
