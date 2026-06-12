from dataclasses import dataclass, field


@dataclass
class Line:
    index: int
    alias: str
    from_bus: int
    to_bus: int
    is_active: bool = True
    p_mw_max: float = 1.0
    r_pu: float = 0.001
    x_pu: float = 0.01
    ang_grad_min: float = -1.0
    ang_grad_max: float = 1.0
    overload_pu: float = 1.0

    _p_mw: float = field(default=0.0, init=False, repr=False)
    _ang_grad: float = field(default=0.0, init=False, repr=False)

    def update(self, profile_row: dict, dt: str) -> None:
        pass

    def get_measures(self) -> dict:
        return {
            "alias": self.alias,
            "from_bus": self.from_bus,
            "to_bus": self.to_bus,
            "p_mw": self._p_mw,
            "ang_grad": self._ang_grad,
            "is_active": self.is_active,
            "overload_pu": self.overload_pu,
        }

    def set_points(self, p_mw: float | None = None, ang_grad: float | None = None) -> None:
        if p_mw is not None:
            max_flow = self.p_mw_max * self.overload_pu
            self._p_mw = max(-max_flow, min(p_mw, max_flow))
        if ang_grad is not None:
            self._ang_grad = max(self.ang_grad_min, min(ang_grad, self.ang_grad_max))
