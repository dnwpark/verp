from typing import Protocol, runtime_checkable


@runtime_checkable
class TerminalFocuser(Protocol):
    def available(self) -> bool:
        """Return True if this focuser's dependencies are present."""
        ...

    def focus(self, tty: str) -> bool:
        """Focus the terminal window hosting tty. Returns True on success."""
        ...
