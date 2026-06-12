from abc import ABC, abstractmethod
from typing import Optional


class Controller(ABC):
    @abstractmethod
    def compute(self, measurements: dict) -> dict:
        pass

    @abstractmethod
    def get_name(self) -> str:
        pass


class LocalController(Controller):
    def get_name(self) -> str:
        return "local"

    def compute(self, measurements: dict) -> dict:
        return {"setpoints": {}}


class RemoteController(Controller):
    def __init__(self, name: str = "remote"):
        self._name = name
        self._pending_setpoints: Optional[dict] = None
        self.connected = False

    def get_name(self) -> str:
        return self._name

    def set_pending_setpoints(self, setpoints: dict) -> None:
        self._pending_setpoints = setpoints

    def compute(self, measurements: dict) -> dict:
        setpoints = self._pending_setpoints or {}
        self._pending_setpoints = None
        return {"setpoints": setpoints}

    def connect(self) -> None:
        self.connected = True

    def disconnect(self) -> None:
        self.connected = False
        self._pending_setpoints = None
