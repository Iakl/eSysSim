import math
import openpyxl
from datetime import datetime, timedelta


def create_profiles_xlsx(output_path: str) -> None:
    wb = openpyxl.Workbook()

    ws_load = wb.active
    ws_load.title = "Load1"
    ws_load.append(["date", "p_mw", "q_mvar"])

    start = datetime(2017, 1, 1, 0, 0)
    for i in range(192):
        dt = start + timedelta(minutes=15 * i)
        hour = dt.hour + dt.minute / 60.0
        day_factor = 1.0 if dt.day == 1 else 0.95
        base = 0.5
        daily_cycle = 0.3 * math.sin(2 * math.pi * (hour - 6) / 24)
        noise = 0.02 * math.sin(2 * math.pi * hour * 4 + i * 0.1)
        p_mw = round((base + daily_cycle + noise) * day_factor, 4)
        q_mvar = round(p_mw * 0.6, 4)
        ws_load.append([dt.strftime("%d-%b-%Y %H:%M"), p_mw, q_mvar])

    ws_grid = wb.create_sheet("grid0")
    ws_grid.append(["date", "purchase_price", "sell_price"])

    for i in range(192):
        dt = start + timedelta(minutes=15 * i)
        hour = dt.hour + dt.minute / 60.0
        base_price = 40.0
        peak = 25.0 * math.exp(-0.5 * ((hour - 14) / 3) ** 2)
        valley = -10.0 * math.exp(-0.5 * ((hour - 3) / 2) ** 2)
        daily_variation = 15.0 * math.sin(2 * math.pi * (hour - 8) / 24)
        day_shift = 5.0 if dt.day == 2 else 0.0
        purchase_price = round(base_price + peak + valley + daily_variation + day_shift, 2)
        sell_price = round(purchase_price * 0.35, 2)
        ws_grid.append([dt.strftime("%d-%b-%Y %H:%M"), purchase_price, sell_price])

    wb.save(output_path)


if __name__ == "__main__":
    import os
    script_dir = os.path.dirname(os.path.abspath(__file__))
    output = os.path.join(script_dir, "profiles.xlsx")
    create_profiles_xlsx(output)
    print(f"Profiles saved to {output}")
