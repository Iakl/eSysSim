import os
import sys
import time
import uuid
import threading
import warnings as warn_module
from datetime import datetime, timedelta
from collections import deque
from typing import Optional

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from plant import (
    _read_xlsx,
    _parse_profiles,
    _build_net,
    _interp,
    _update_net,
)
from models.battery import Battery
from models.controller import Controller, LocalController, RemoteController

import pandapower as pp
import warnings

warnings.filterwarnings("ignore", message=".*numba.*")

KNOWN_CLASSES = {"Bus", "ElectricGrid", "ElectricLoad", "Line", "Battery", "Generator"}


def _validate_scenario_classes(params: dict) -> list[str]:
    missing = []
    for sheet_name in params:
        if sheet_name in KNOWN_CLASSES:
            continue
        if sheet_name.startswith("Hoja") or sheet_name.startswith("Sheet"):
            continue
        if not params[sheet_name]:
            continue
        missing.append(sheet_name)
    return missing


class SimulationState:
    def __init__(self, sim_id: str, scenario_dir: str, resolution_freq: float, duration_sec: float, time_scale: float = 1.0):
        self.id = sim_id
        self.scenario_dir = scenario_dir
        self.resolution_freq = resolution_freq
        self.duration_sec = duration_sec
        self.time_scale = time_scale
        self.status = "idle"
        self.sim_time: Optional[datetime] = None
        self.sim_elapsed = 0.0
        self.history_window = 300
        self.history: deque = deque(maxlen=self.history_window)
        self.error: Optional[str] = None
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._last_index = 0
        self._new_data_event = threading.Event()

        params = _read_xlsx(os.path.join(scenario_dir, "params.xlsx"))
        profiles = _read_xlsx(os.path.join(scenario_dir, "profiles.xlsx"))
        load_dates, load_values, grid_dates, grid_values = _parse_profiles(profiles)

        gen_profiles = {}
        gen_dates = {}
        for sheet_name in profiles:
            if sheet_name in ("Load1", "grid0"):
                continue
            sheet_data = profiles[sheet_name]
            dates = []
            values = []
            for row in sheet_data:
                dt_str = str(row.get("date", ""))
                try:
                    dt = datetime.strptime(dt_str, "%d-%b-%Y %H:%M")
                except ValueError:
                    continue
                dates.append(dt)
                values.append({
                    "p_max_mw": float(row.get("p_max_mw", row.get("p_mw", 0))),
                    "q_max_mvar": float(row.get("q_max_mvar", row.get("q_mvar", 0))),
                })
            gen_profiles[sheet_name] = values
            gen_dates[sheet_name] = dates

        if not load_dates and grid_dates:
            load_dates = grid_dates
            load_values = [{"p_mw": 0, "q_mvar": 0}] * len(grid_dates)

        net, ref = _build_net(params)

        self.params = params
        self.load_dates = load_dates
        self.load_values = load_values
        self.grid_dates = grid_dates
        self.grid_values = grid_values
        self.gen_profiles = gen_profiles
        self.gen_dates = gen_dates
        self.net = net
        self.ref = ref
        self.sim_end: Optional[datetime] = None

        self.warnings = _validate_scenario_classes(params)
        for w in self.warnings:
            warn_module.warn(f"Scenario '{os.path.basename(scenario_dir)}' uses class '{w}' which is not implemented in eSysSim. Data will be ignored.")

        self.controller: Controller = LocalController()
        self.remote_controller: Optional[RemoteController] = None

    def start(self):
        if self.status == "running":
            return
        self.status = "running"
        self.sim_elapsed = 0.0
        self.sim_time = self.load_dates[0]
        self.sim_end = self.sim_time + timedelta(seconds=self.duration_sec * self.time_scale)
        self.history.clear()
        self.error = None
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)
        if self.remote_controller:
            self.remote_controller.disconnect()
            self.controller = LocalController()
        self.status = "stopped"

    def connect_remote_controller(self) -> RemoteController:
        self.remote_controller = RemoteController()
        self.controller = self.remote_controller
        self.remote_controller.connect()
        return self.remote_controller

    def disconnect_remote_controller(self) -> None:
        if self.remote_controller:
            self.remote_controller.disconnect()
            self.remote_controller = None
            self.controller = LocalController()

    def build_measurements(self, load_vals: dict, grid_vals: dict, gen_vals: dict, load_profile_vals: dict) -> dict:
        measurements = {
            "time": self.sim_time.isoformat() if self.sim_time else "",
            "sim_elapsed": round(self.sim_elapsed, 1),
            "buses": {},
            "loads": {},
            "grids": {},
            "lines": {},
            "batteries": {},
            "generators": {},
        }

        for b_idx, pp_idx in self.ref["bus_map"].items():
            name = self.net.bus.at[pp_idx, "name"]
            measurements["buses"][name] = {
                "vm_pu": float(self.net.res_bus.at[pp_idx, "vm_pu"]),
                "va_degree": float(self.net.res_bus.at[pp_idx, "va_degree"]),
            }

        for idx, load_res in self.net.res_load.iterrows():
            name = self.net.load.at[idx, "name"]
            measurements["loads"][name] = {
                "p_mw": -float(load_res["p_mw"]),
                "q_mvar": -float(load_res["q_mvar"]),
            }

        for idx, eg in self.net.res_ext_grid.iterrows():
            name = self.net.ext_grid.at[idx, "name"]
            measurements["grids"][name] = {
                "p_mw": float(eg["p_mw"]),
                "q_mvar": float(eg["q_mvar"]),
                "purchase_price": float(grid_vals.get("purchase_price", 0)),
                "sell_price": float(grid_vals.get("sell_price", 0)),
            }

        for idx, imp_res in self.net.res_impedance.iterrows():
            name = self.net.impedance.at[idx, "name"]
            measurements["lines"][name] = {
                "p_from_mw": float(imp_res.get("p_from_mw", imp_res.get("pft_mw", 0))),
                "p_to_mw": float(imp_res.get("p_to_mw", imp_res.get("ptf_mw", 0))),
            }

        for bat in self.ref.get("batteries", []):
            measurements["batteries"][bat.alias] = {
                "p_mw": float(bat._p_mw),
                "soc_pu": float(bat._soc_pu),
            }

        for idx, gen_res in self.net.res_gen.iterrows():
            name = self.net.gen.at[idx, "name"]
            measurements["generators"][name] = {
                "p_mw": float(gen_res["p_mw"]),
                "q_mvar": float(gen_res["q_mvar"]),
            }

        return measurements

    def apply_setpoints(self, setpoints: dict) -> None:
        for alias, sp in setpoints.get("batteries", {}).items():
            for bat in self.ref.get("batteries", []):
                if bat.alias == alias:
                    if "p_mw" in sp:
                        bat._p_mw = max(bat.p_min_mw, min(sp["p_mw"], bat.p_max_mw))

        for alias, sp in setpoints.get("generators", {}).items():
            idx_list = self.net.gen[self.net.gen.name == alias].index
            if len(idx_list) > 0 and "p_mw" in sp:
                idx = idx_list[0]
                max_p = self.net.gen.at[idx, "max_p_mw"]
                self.net.gen.at[idx, "p_mw"] = max(0, min(sp["p_mw"], max_p))

    def _run(self):
        pf_accum = 0.0
        last_time = time.perf_counter()

        try:
            while not self._stop_event.is_set() and self.sim_elapsed < self.duration_sec:
                time.sleep(0.1)
                now = time.perf_counter()
                delta = now - last_time
                self.sim_elapsed += delta
                pf_accum += delta
                last_time = now

                if pf_accum >= self.resolution_freq:
                    self.sim_time += timedelta(seconds=pf_accum * self.time_scale)
                    if self.sim_time > self.sim_end:
                        self.sim_time = self.sim_end

                    load_vals = _interp(self.load_dates, self.load_values, self.sim_time)
                    grid_vals = _interp(self.grid_dates, self.grid_values, self.sim_time)

                    gen_vals = {}
                    for gen in self.ref.get("generators", []):
                        alias = gen["alias"]
                        if alias in self.gen_dates and alias in self.gen_profiles:
                            gen_vals[alias] = _interp(self.gen_dates[alias], self.gen_profiles[alias], self.sim_time)

                    load_profile_vals = {}
                    for load in self.ref.get("loads", []):
                        alias = load["alias"]
                        if alias in self.gen_dates and alias in self.gen_profiles:
                            load_profile_vals[alias] = _interp(self.gen_dates[alias], self.gen_profiles[alias], self.sim_time)

                    dt_hours = pf_accum * self.time_scale / 3600.0
                    purchase_price = grid_vals.get("purchase_price", 0) if grid_vals else 0

                    for bat in self.ref.get("batteries", []):
                        bat.update({}, str(self.sim_time), purchase_price=purchase_price, dt_hours=dt_hours)
                        idx_list = self.net.storage[self.net.storage.name == bat.alias].index
                        if len(idx_list) > 0:
                            idx = idx_list[0]
                            self.net.storage.at[idx, "p_mw"] = -bat._p_mw
                            self.net.storage.at[idx, "soc_percent"] = bat._soc_pu * 100

                    _update_net(self.net, self.ref, load_vals, grid_vals, gen_vals, load_profile_vals)

                    try:
                        pp.runpp(self.net)
                    except Exception as e:
                        self.error = str(e)
                        pf_accum = 0
                        continue

                    if not self.net.converged:
                        self.error = "Power flow did not converge"
                        pf_accum = 0
                        continue

                    self.error = None

                    measurements = self.build_measurements(load_vals, grid_vals, gen_vals, load_profile_vals)
                    result = self.controller.compute(measurements)
                    self.apply_setpoints(result.get("setpoints", {}))
                    snapshot = self._collect_snapshot(load_vals, grid_vals)
                    with self._lock:
                        self.history.append(snapshot)
                        self._last_index = len(self.history)
                        self._new_data_event.set()
                    pf_accum = 0

            self.status = "completed"
        except Exception as e:
            self.status = "error"
            self.error = str(e)

    def _collect_snapshot(self, load_vals: dict, grid_vals: dict) -> dict:
        snapshot = {
            "time": self.sim_time.isoformat(),
            "sim_elapsed": round(self.sim_elapsed, 1),
            "buses": {},
            "loads": {},
            "grids": {},
            "lines": {},
            "batteries": {},
            "generators": {},
        }

        for b_idx, pp_idx in self.ref["bus_map"].items():
            name = self.net.bus.at[pp_idx, "name"]
            snapshot["buses"][name] = {
                "vm_pu": float(self.net.res_bus.at[pp_idx, "vm_pu"]),
            }

        for idx, load_res in self.net.res_load.iterrows():
            name = self.net.load.at[idx, "name"]
            snapshot["loads"][name] = {
                "p_mw": -float(load_res["p_mw"]),
                "q_mvar": -float(load_res["q_mvar"]),
            }

        for idx, eg in self.net.res_ext_grid.iterrows():
            name = self.net.ext_grid.at[idx, "name"]
            snapshot["grids"][name] = {
                "p_mw": float(eg["p_mw"]),
                "purchase_price": float(grid_vals.get("purchase_price", 0)),
                "sell_price": float(grid_vals.get("sell_price", 0)),
            }

        for idx, imp_res in self.net.res_impedance.iterrows():
            name = self.net.impedance.at[idx, "name"]
            p_from = float(imp_res.get("p_from_mw", imp_res.get("pft_mw", 0)))
            snapshot["lines"][name] = {
                "p_from_mw": p_from,
                "loading_percent": float(imp_res.get("loading_percent", 0)),
            }

        for bat in self.ref.get("batteries", []):
            snapshot["batteries"][bat.alias] = {
                "p_mw": float(bat._p_mw),
                "soc_pu": float(bat._soc_pu),
            }

        for idx, gen_res in self.net.res_gen.iterrows():
            name = self.net.gen.at[idx, "name"]
            snapshot["generators"][name] = {
                "p_mw": float(gen_res["p_mw"]),
                "q_mvar": float(gen_res["q_mvar"]),
            }

        return snapshot

    def wait_for_new_data(self, timeout: float = 2.0) -> bool:
        self._new_data_event.wait(timeout)
        self._new_data_event.clear()
        return True

    def get_new_snapshots(self) -> list:
        with self._lock:
            if self._last_index > len(self.history):
                self._last_index = len(self.history)
            new = list(self.history)[self._last_index:]
            self._last_index = len(self.history)
            return new

    def get_history(self, window_sec: float = 300) -> list:
        with self._lock:
            cutoff = self.sim_elapsed - window_sec
            return [s for s in self.history if s["sim_elapsed"] >= cutoff]

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "status": self.status,
            "scenario": os.path.basename(self.scenario_dir),
            "resolution_freq": self.resolution_freq,
            "duration_sec": self.duration_sec,
            "time_scale": self.time_scale,
            "sim_elapsed": round(self.sim_elapsed, 1),
            "sim_time": self.sim_time.isoformat() if self.sim_time else None,
            "error": self.error,
            "warnings": self.warnings,
            "controller": self.controller.get_name(),
            "remote_connected": self.remote_controller is not None and self.remote_controller.connected,
        }


class PlantRunner:
    def __init__(self):
        self.simulations: dict[str, SimulationState] = {}
        self._lock = threading.Lock()

    def create(self, scenario_dir: str, resolution_freq: float = 10.0, duration_sec: float = 3600.0, time_scale: float = 1.0) -> SimulationState:
        sim_id = str(uuid.uuid4())[:8]
        sim = SimulationState(sim_id, scenario_dir, resolution_freq, duration_sec, time_scale)
        with self._lock:
            self.simulations[sim_id] = sim
        return sim

    def get(self, sim_id: str) -> Optional[SimulationState]:
        return self.simulations.get(sim_id)

    def list_all(self) -> list[dict]:
        with self._lock:
            return [s.to_dict() for s in self.simulations.values()]

    def stop(self, sim_id: str):
        sim = self.simulations.get(sim_id)
        if sim:
            sim.stop()

    def delete(self, sim_id: str):
        sim = self.simulations.get(sim_id)
        if sim:
            sim.stop()
            with self._lock:
                del self.simulations[sim_id]
