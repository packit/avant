# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

from typing import Optional

from ogr.abstract import GitProject
from packit.config import PackageConfig
from ..abstract.base import ForgeIndependent


class ForgejoEvent(ForgeIndependent):
    def __init__(self, project_url: str, pr_id: Optional[int] = None, **kwargs):
        super().__init__(pr_id=pr_id)
        self.project_url: str = project_url
        # git ref that can be 'git checkout'-ed
        self.git_ref: Optional[str] = None
        self.identifier: Optional[str] = (
            None  # will be shown to users -- e.g. in logs or in the copr-project name
        )

    def get_packages_config(self) -> Optional[PackageConfig]:
        """
        For Forgejo events, we don't have package config since these are 
        typically administrative or issue-related events.
        """
        return None
