import celery
import pytest
import flexmock as flexmock
from packit.config import (
    PackageConfig,
    JobConfig,
    JobType,
    CommonPackageConfig
)

from packit_service.events import (
    gitlab,
    forgejo
)

from packit_service.worker.handlers import (
    CoprBuildEndHandler,
    CoprBuildStartHandler,
    CoprBuildHandler
)

from packit_service.worker.result import TaskResults
from packit_service.worker.minijobs import MiniJobs


def test_job_type_execution():
    pass
