from collections.abc import Callable

from verp.db import SCHEMA_VERSION, ProjectInfo, set_project_version

_MIGRATIONS: dict[int, Callable[[ProjectInfo], None]] = {}


def upgrade_project(project_info: ProjectInfo) -> None:
    for version in range(project_info.version + 1, SCHEMA_VERSION + 1):
        if version in _MIGRATIONS:
            _MIGRATIONS[version](project_info)
        set_project_version(project_info.name, version)
