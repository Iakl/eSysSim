import os
import time
import warnings
from datetime import datetime, timedelta

import numpy as np
import openpyxl
import pandapower as pp

warnings.filterwarnings("ignore", message=".*numba.*")


def _read_xlsx(path: str) -> dict[str, list[dict]]:
    wb = openpyxl.load_workbook(path, data_only=True)
    result = {}
    for sheet_name in wb.sheetnames:
        ws = wb[sheet_name]
        rows = list(ws.iter_rows(values_only=True))
        if not rows:
            continue
        headers = [h for h in rows[0]]
        data = []
        for row in rows[1:]:
            if all(v is None for v in row):
                continue
            data.append(dict(zip(headers, row)))
        result[sheet_name] = data
    return result


def _parse_profiles(profiles: dict) -> tuple[list[datetime], list[dict], list[dict]]:
    load_data = profiles.get("Load1", [])
    grid_data = profiles.get("grid0", [])

    load_dates = []
    load_values = []
    for row in load_data:
        dt = datetime.strptime(str(row["date"]), "%d-%b-%Y %H:%M")
        load_dates.append(dt)
        load_values.append({
            "p_mw": float(row.get("p_mw", 0.0)),
            "q_mvar": float(row.get("q_mvar", 0.0)),
        })

    grid_dates = []
    grid_values = []
    for row in grid_data:
        dt = datetime.strptime(str(row["date"]), "%d-%b-%Y %H:%M")
        grid_dates.append(dt)
        grid_values.append({
            "purchase_price": float(row.get("purchase_price", 0.0)),
            "sell_price": float(row.get("sell_price", 0.0)),
        })

    return load_dates, load_values, grid_dates, grid_values


def _interp(dates: list[datetime], values: list[dict], sim_time: datetime) -> dict:
    if sim_time <= dates[0]:
        return values[0]
    if sim_time >= dates[-1]:
        return values[-1]

    ts = [d.timestamp() for d in dates]
    t = sim_time.timestamp()
    idx = np.searchsorted(ts, t)

    t0, t1 = ts[idx - 1], ts[idx]
    frac = (t - t0) / (t1 - t0)

    result = {}
    for key in values[0]:
        v0 = values[idx - 1][key]
        v1 = values[idx][key]
        result[key] = v0 + frac * (v1 - v0)

    return result


from models.battery import Battery


def _build_net(params: dict) -> tuple[pp.pandapowerNet, dict]:
    net = pp.create_empty_network()

    bus_map = {}
    for b in params.get("Bus", []):
        idx = pp.create_bus(net, vn_kv=b["vn_kv"], name=b["alias"])
        bus_map[b["index"]] = idx

    for g in params.get("ElectricGrid", []):
        bus_idx = bus_map[g["connection1"]]
        pp.create_ext_grid(net, bus_idx, vm_pu=g.get("v_set_pu", 1.0), name=g["alias"])

    for load in params.get("ElectricLoad", []):
        bus_idx = bus_map[load["connection1"]]
        pp.create_load(net, bus_idx, p_mw=0.0, q_mvar=0.0, name=load["alias"])

    batteries = []
    for bat in params.get("Battery", []):
        bus_idx = bus_map[bat["connection1"]]
        bat_obj = Battery(
            index=bat["index"],
            alias=bat["alias"],
            connection1=bat["connection1"],
            is_active=bool(bat.get("is_active", True)),
            p_min_mw=bat.get("p_min_mw", -0.5),
            p_max_mw=bat.get("p_max_mw", 0.5),
            capacity_mwh=bat.get("capacity_mwh", 1.0),
            soc_ini_pu=bat.get("soc_ini_pu", 0.6),
            soc_max_pu=bat.get("soc_max_pu", 0.8),
            soc_min_pu=bat.get("soc_min_pu", 0.2),
            eff_ch=bat.get("eff_ch", 0.95),
            eff_dch=bat.get("eff_dch", 0.95),
            controllable=bool(bat.get("controllable", True)),
        )
        pp.create_storage(net, bus=bus_idx, p_mw=0.0, q_mvar=0.0,
                          max_e_mwh=bat_obj.capacity_mwh, soc_percent=bat_obj.soc_ini_pu * 100,
                          name=bat_obj.alias)
        batteries.append(bat_obj)

    for line in params.get("Line", []):
        from_idx = bus_map[line["from_bus"]]
        to_idx = bus_map[line["to_bus"]]
        r_pu = line.get("r_pu", 0.001)
        x_pu = line.get("x_pu", 0.01)
        pp.create_impedance(
            net,
            from_bus=from_idx,
            to_bus=to_idx,
            rft_pu=r_pu,
            xft_pu=x_pu,
            rtf_pu=r_pu,
            xtf_pu=x_pu,
            sn_mva=1.0,
            name=line["alias"],
        )

    generators = []
    for gen in params.get("Generator", []):
        bus_idx = bus_map[gen["connection1"]]
        v_set = gen.get("v_set_pu", 1.0)
        if v_set == 0:
            v_set = 1.0
        pp.create_gen(
            net,
            bus=bus_idx,
            p_mw=0.0,
            vm_pu=v_set,
            min_p_mw=gen.get("p_min_mw", 0),
            max_p_mw=gen.get("p_max_mw", 0),
            min_q_mvar=gen.get("q_min_mvar", -300),
            max_q_mvar=gen.get("q_max_mvar", 300),
            name=gen["alias"],
        )
        generators.append(gen)

    ref = {
        "bus_map": bus_map,
        "ext_grids": params.get("ElectricGrid", []),
        "loads": params.get("ElectricLoad", []),
        "lines": params.get("Line", []),
        "batteries": batteries,
        "generators": generators,
    }
    return net, ref


def _update_net(net: pp.pandapowerNet, ref: dict, load_vals: dict, grid_vals: dict, gen_vals: dict = None, load_profile_vals: dict = None) -> None:
    for load in ref["loads"]:
        idx_list = net.load[net.load.name == load["alias"]].index
        if len(idx_list) == 0:
            continue
        idx = idx_list[0]
        if load_profile_vals and load["alias"] in load_profile_vals:
            lpv = load_profile_vals[load["alias"]]
            p_mw = lpv.get("p_mw", lpv.get("p_max_mw", 0))
            q_mvar = lpv.get("q_mvar", lpv.get("q_max_mvar", 0))
            net.load.at[idx, "p_mw"] = -p_mw
            net.load.at[idx, "q_mvar"] = -q_mvar
        else:
            net.load.at[idx, "p_mw"] = -load_vals.get("p_mw", 0)
            net.load.at[idx, "q_mvar"] = -load_vals.get("q_mvar", 0)

    if gen_vals:
        for gen in ref.get("generators", []):
            alias = gen["alias"]
            if alias in gen_vals:
                idx_list = net.gen[net.gen.name == alias].index
                if len(idx_list) > 0:
                    idx = idx_list[0]
                    p_max = gen_vals[alias].get("p_max_mw", gen.get("p_max_mw", 0))
                    net.gen.at[idx, "max_p_mw"] = abs(p_max)
                    net.gen.at[idx, "p_mw"] = abs(p_max)


def _print_step(sim_time: datetime, net: pp.pandapowerNet, ref: dict, grid_vals: dict) -> None:
    print(f"\n=== {sim_time.strftime('%d-%b-%Y %H:%M:%S')} ===")

    for b_idx, pp_idx in ref["bus_map"].items():
        vm = net.res_bus.at[pp_idx, "vm_pu"]
        name = net.bus.at[pp_idx, "name"]
        print(f"  Bus {name}: {vm:.4f} pu")

    for idx, load_res in net.res_load.iterrows():
        name = net.load.at[idx, "name"]
        print(f"  Load {name}: p={load_res['p_mw']:.4f} MW, q={load_res['q_mvar']:.4f} Mvar")

    for idx, eg in net.res_ext_grid.iterrows():
        name = net.ext_grid.at[idx, "name"]
        print(f"  Grid {name}: p={eg['p_mw']:.4f} MW (slack), buy=${grid_vals.get('purchase_price', 0):.2f}/MWh, sell=${grid_vals.get('sell_price', 0):.2f}/MWh")

    for idx, gen_res in net.res_gen.iterrows():
        name = net.gen.at[idx, "name"]
        print(f"  Gen {name}: p={gen_res['p_mw']:.4f} MW, q={gen_res['q_mvar']:.4f} Mvar")

    for idx, imp_res in net.res_impedance.iterrows():
        name = net.impedance.at[idx, "name"]
        p_from = imp_res.get("p_from_mw", imp_res.get("pft_mw", 0))
        print(f"  Line {name}: p_from={p_from:.4f} MW")


def run_simulation(scenario_dir: str, resolution_freq: float = 10.0, duration_sec: float = 3600.0) -> None:
    if resolution_freq < 1.0:
        raise ValueError("resolution_freq must be >= 1.0 seconds")

    params = _read_xlsx(os.path.join(scenario_dir, "params.xlsx"))
    profiles = _read_xlsx(os.path.join(scenario_dir, "profiles.xlsx"))

    load_dates, load_values, grid_dates, grid_values = _parse_profiles(profiles)
    net, ref = _build_net(params)

    sim_time = load_dates[0]
    sim_end = sim_time + timedelta(seconds=duration_sec)
    sim_elapsed = 0.0
    pf_accum = 0.0
    last_time = time.perf_counter()

    step_count = 0

    while sim_elapsed < duration_sec:
        time.sleep(0.1)
        now = time.perf_counter()
        delta = now - last_time
        sim_elapsed += delta
        pf_accum += delta
        last_time = now

        if pf_accum >= resolution_freq:
            sim_time += timedelta(seconds=pf_accum)
            if sim_time > sim_end:
                sim_time = sim_end

            load_vals = _interp(load_dates, load_values, sim_time)
            grid_vals = _interp(grid_dates, grid_values, sim_time)

            _update_net(net, ref, load_vals, grid_vals)

            try:
                pp.runpp(net)
            except Exception as e:
                print(f"  WARNING: Power flow failed at {sim_time}: {e}")
                continue

            if not net.converged:
                print(f"  WARNING: Power flow did not converge at {sim_time}")
                continue

            _print_step(sim_time, net, ref, grid_vals)
            step_count += 1
            pf_accum = 0

    print(f"\nSimulation complete: {step_count} steps in {sim_elapsed:.1f}s")


if __name__ == "__main__":
    scenario = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scenarios", "base_case")
    run_simulation(scenario, resolution_freq=10.0, duration_sec=60.0)
