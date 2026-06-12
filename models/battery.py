from dataclasses import dataclass, field


@dataclass
class Battery:
    index: int
    alias: str
    connection1: int
    is_active: bool = True
    p_mw: float = 0.0
    q_mvar: float = 0.0
    p_min_mw: float = -0.5
    p_max_mw: float = 0.5
    q_min_mvar: float = 0.0
    q_max_mvar: float = 0.0
    c_degr: float = 0.0
    capacity_mwh: float = 1.0
    soc_ini_pu: float = 0.6
    soc_max_pu: float = 0.8
    soc_min_pu: float = 0.2
    eff_ch: float = 0.95
    eff_dch: float = 0.95
    controllable: bool = True
    priority: int = 1

    _soc_pu: float = field(default=0.0, init=False, repr=False)
    _p_mw: float = field(default=0.0, init=False, repr=False)
    _q_mvar: float = field(default=0.0, init=False, repr=False)

    def __post_init__(self):
        self._soc_pu = self.soc_ini_pu

    def update(self, profile_row: dict, dt: str, purchase_price: float = 0.0, dt_hours: float = 0.0) -> None:
        if not self.is_active or dt_hours <= 0:
            return

        if self._p_mw != 0:
            if self._p_mw < 0:
                self._soc_pu += abs(self._p_mw) * self.eff_ch * dt_hours / self.capacity_mwh
            else:
                self._soc_pu -= self._p_mw * dt_hours / (self.eff_dch * self.capacity_mwh)
            self._soc_pu = max(self.soc_min_pu, min(self.soc_max_pu, self._soc_pu))

            if self._soc_pu >= self.soc_max_pu and self._p_mw < 0:
                self._p_mw = 0.0
            elif self._soc_pu <= self.soc_min_pu and self._p_mw > 0:
                self._p_mw = 0.0

    def get_measures(self) -> dict:
        return {
            "alias": self.alias,
            "p_mw": self._p_mw,
            "q_mvar": self._q_mvar,
            "soc_pu": self._soc_pu,
            "is_active": self.is_active,
            "controllable": self.controllable,
        }

    def set_points(self, p_mw: float | None = None, q_mvar: float | None = None) -> None:
        if not self.controllable:
            return
        if p_mw is not None:
            self._p_mw = max(self.p_min_mw, min(p_mw, self.p_max_mw))
        if q_mvar is not None:
            self._q_mvar = max(self.q_min_mvar, min(q_mvar, self.q_max_mvar))
