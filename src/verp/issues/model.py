from dataclasses import dataclass, field
from enum import StrEnum


class IssueStatus(StrEnum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    DROPPED = "dropped"

    @property
    def closed(self) -> bool:
        return self in (IssueStatus.DONE, IssueStatus.DROPPED)


class ActorKind(StrEnum):
    CLI = "cli"  # the human operator, running verp directly
    AGENT = "agent"  # an interactive agent (verp claude / verp pi)
    WORKER = "worker"  # an autonomous verp worker


@dataclass(frozen=True)
class Actor:
    kind: ActorKind
    project: str | None

    @property
    def is_agent(self) -> bool:
        return self.kind != ActorKind.CLI

    def __str__(self) -> str:
        return f"{self.kind} {self.project}" if self.project else str(self.kind)


class EventKind(StrEnum):
    CREATED = "created"
    EDITED = "edited"
    LOG = "log"
    STATUS = "status"
    CLAIMED = "claimed"
    RELEASED = "released"
    ASSIGNED = "assigned"


@dataclass
class Issue:
    id: int
    title: str
    body: str
    status: IssueStatus
    project: str | None
    outcome: str | None
    created_at: int
    updated_at: int
    closed_at: int | None
    depends_on: list[int] = field(default_factory=list)


@dataclass
class Event:
    id: int
    issue_id: int
    at: int
    actor: Actor
    kind: EventKind
    message: str | None
