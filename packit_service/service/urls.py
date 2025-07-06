# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

from packit_service.config import ServiceConfig


def get_srpm_build_info_url(id_: int) -> str:
    return _get_url_for_dashboard_results("srpm", id_)

def get_copr_build_info_url(id_: int) -> str:
    return _get_url_for_dashboard_results("copr", id_)

def get_openscanhub_info_url(id_: int) -> str:
    return _get_url_for_dashboard_results("openscanhub", id_)
