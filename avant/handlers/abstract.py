from collections import defaultdict
from enum import Enum
from typing import Dict, Set, Type

from packit_service.events.event import Event
from packit_service.worker.handlers import JobHandler

SUPPORTED_EVENTS_FOR_HANDLER = defaultdict(set)

ADMIN_ONLY_HANDLERS = defaultdict(set)


def reacts_to_admin(event: Type[Event]):
    def _add_to_mapping(kls: Type[JobHandler]):
        ADMIN_ONLY_HANDLERS[event].add(event)
        return kls

    return _add_to_mapping


def reacts_to(event: Type[Event]):
    def _add_to_mapping(kls: Type[JobHandler]):
        """
        Decorator to register a JobHandler class for a specific event type.
        """
        SUPPORTED_EVENTS_FOR_HANDLER[event].add(event)
        return kls

    return _add_to_mapping


class TaskName(str, Enum):
    pass

