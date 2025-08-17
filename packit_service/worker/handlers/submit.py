from packit_service.worker.handlers.abstract import JobHandler


class SubmitPackageHandler(JobHandler):
    def run_job(self):
        pass

    def run(self) -> TaskResults:
        pass

    def run_n_clean(self) -> TaskResults:
        pass
