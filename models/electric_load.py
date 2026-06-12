from dataclasses import dataclass, field


@dataclass
class ElectricLoad:
    index: int
    alias: str
    connection1: int
    load_type: str = "critical"
    is_active: bool = True
    controllable: bool = False
    priority: int = 0
    p_min_mw: float = 0.0
    p_max_mw: float = 0.0
    q_min_mvar: float = 0.0
    q_max_mvar: float = 0.0

    _p_mw: float = field(default=0.0, init=False, repr=False)
    _q_mvar: float = field(default=0.0, init=False, repr=False)

    def update(self, profile_row: dict, dt: str) -> None:
        if not self.is_active:
            return
        p_mw = profile_row.get("p_mw", self._p_mw)
        q_mvar = profile_row.get("q_mvar", self._q_mvar)
        if self.controllable:
            if self.p_max_mw != 0:
                p_mw = max(self.p_min_mw, min(p_mw, self.p_max_mw))
            if self.q_max_mvar != 0:
                q_mvar = max(self.q_min_mvar, min(q_mvar, self.q_max_mvar))
        self._p_mw = p_mw
        self._q_mvar = q_mvar

    def get_measures(self) -> dict:
        return {
            "alias": self.alias,
            "p_mw": self._p_mw,
            "q_mvar": self._q_mvar,
            "load_type": self.load_type,
            "is_active": self.is_active,
            "controllable": self.controllable,
            "priority": self.priority,
        }

    def set_points(self, p_mw: float | None = None, q_mvar: float | None = None) -> None:
        if not self.controllable:
            return
        if p_mw is not None:
            if self.p_max_mw != 0:
                self._p_mw = max(self.p_min_mw, min(p_mw, self.p_max_mw))
            else:
                self._p_mw = p_mw
        if q_mvar is not None:
            if self.q_max_mvar != 0:
                self._q_mvar = max(self.q_min_mvar, min(q_mvar, self.q_max_mvar))
            else:
                self._q_mvar = q_mvar
