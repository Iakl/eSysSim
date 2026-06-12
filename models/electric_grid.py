from dataclasses import dataclass, field


@dataclass
class ElectricGrid:
    index: int
    alias: str
    connection1: int
    is_active: bool = True
    v_set_pu: float = 1.0
    p_min_mw: float = 0.0
    p_max_mw: float = 1.0
    q_min_mvar: float = -1.0
    q_max_mvar: float = 1.0

    _purchase_price: float = field(default=0.0, init=False, repr=False)
    _sell_price: float = field(default=0.0, init=False, repr=False)
    _p_mw: float = field(default=0.0, init=False, repr=False)
    _q_mvar: float = field(default=0.0, init=False, repr=False)

    def update(self, profile_row: dict, dt: str) -> None:
        if not self.is_active:
            return
        self._purchase_price = profile_row.get("purchase_price", self._purchase_price)
        self._sell_price = profile_row.get("sell_price", self._sell_price)

    def get_measures(self) -> dict:
        return {
            "alias": self.alias,
            "p_mw": self._p_mw,
            "q_mvar": self._q_mvar,
            "purchase_price": self._purchase_price,
            "sell_price": self._sell_price,
            "v_pu": self.v_set_pu,
            "is_active": self.is_active,
        }

    def set_points(self, p_mw: float | None = None, q_mvar: float | None = None) -> None:
        if p_mw is not None:
            self._p_mw = max(self.p_min_mw, min(p_mw, self.p_max_mw))
        if q_mvar is not None:
            self._q_mvar = max(self.q_min_mvar, min(q_mvar, self.q_max_mvar))
