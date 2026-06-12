from dataclasses import dataclass, field


@dataclass
class Bus:
    index: int
    alias: str
    vn_kv: float = 6.0
    v_pu_min: float = 0.9
    v_pu_max: float = 1.1

    _v_pu: float = field(default=1.0, init=False, repr=False)

    def update(self, profile_row: dict, dt: str) -> None:
        pass

    def get_measures(self) -> dict:
        return {
            "alias": self.alias,
            "vn_kv": self.vn_kv,
            "v_pu": self._v_pu,
            "v_pu_min": self.v_pu_min,
            "v_pu_max": self.v_pu_max,
        }

    def set_points(self, v_pu: float | None = None) -> None:
        if v_pu is not None:
            self._v_pu = max(self.v_pu_min, min(v_pu, self.v_pu_max))
