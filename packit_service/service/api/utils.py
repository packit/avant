# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

from http import HTTPStatus
from typing import Any, Union

from flask.json import jsonify

from packit_service.models import (
    CoprBuildGroupModel,
    CoprBuildTargetModel,
    GitProjectModel,
    SRPMBuildModel,
    VMImageBuildTargetModel,
)


def response_maker(result: Any, status: HTTPStatus = HTTPStatus.OK):
    """response_maker is a wrapper around flask's make_response"""
    resp = jsonify(result)
    resp.status_code = status.value
    return resp


def get_project_info_from_build(
        build: Union[
            SRPMBuildModel,
            CoprBuildTargetModel,
            CoprBuildGroupModel,
            VMImageBuildTargetModel,
        ],
) -> dict[str, Any]:
    if not (project := build.get_project()):
        return {}

    result_dict = {
        "pr_id": build.get_pr_id(),
        "issue_id": build.get_issue_id(),
        "branch_name": build.get_branch_name(),
        "release": build.get_release_tag(),
    }
    result_dict.update(get_project_info(project))
    return result_dict


def get_project_info(project: Union[GitProjectModel]):
    result_dict = {}
    repo_namespace = project.namespace if project else ""
    repo_name = project.repo_name if project else ""
    project_url = project.project_url if project else ""

    result_dict["repo_namespace"] = repo_namespace
    result_dict["repo_name"] = repo_name
    result_dict["project_url"] = project_url

    return result_dict