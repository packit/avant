from typing import Optional

from ogr.abstract import GitProject
from packit.config import PackageConfig

from packit_service.events.event import Event


class NewPackageEvent(Event):
    def __init__(self,
                 package_name: str,
                 package_version: str,
                 author: str,
                 **kwargs):
        super().__init__(**kwargs)
        self.package_name = package_name
        self.package_version = package_version
        self.actor = author

    @classmethod
    def event_type(cls) -> str:
        return "new_package_event"

    @property
    def project(self) -> Optional[GitProject]:
        # For new package events, we don't have a specific project
        # since this is about creating a new package
        return None

    @property
    def base_project(self) -> Optional[GitProject]:
        # For new package events, we don't have a base project
        return None

    @property
    def packages_config(self) -> Optional[PackageConfig]:
        # For new package events, we don't have a package config
        # since this is about creating a new package
        return None

    def get_packages_config(self) -> Optional[PackageConfig]:
        # For new package events, we don't have a package config
        return None

    def get_project(self) -> Optional[GitProject]:
        # For new package events, we don't have a specific project
        return None