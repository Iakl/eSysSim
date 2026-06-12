import os
import json
import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from sse_starlette.sse import EventSourceResponse

from runner import PlantRunner

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.join(BASE_DIR, "..")
SCENARIOS_DIR = os.path.join(PROJECT_DIR, "scenarios")

runner = PlantRunner()


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield


app = FastAPI(title="eSysSim Monitor", lifespan=lifespan)

app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")


@app.get("/")
async def index():
    return FileResponse(os.path.join(BASE_DIR, "static", "index.html"))


@app.get("/api/simulations")
async def list_simulations():
    return runner.list_all()


@app.post("/api/simulations")
async def create_simulation(resolution_freq: float = 10.0, duration_sec: float = 3600.0, time_scale: float = 1.0, scenario: str = "base_case"):
    scenario_dir = os.path.join(SCENARIOS_DIR, scenario)
    if not os.path.isdir(scenario_dir):
        raise HTTPException(status_code=404, detail=f"Scenario '{scenario}' not found")
    sim = runner.create(scenario_dir, resolution_freq, duration_sec, time_scale)
    sim.start()
    return sim.to_dict()


@app.post("/api/simulations/{sim_id}/stop")
async def stop_simulation(sim_id: str):
    sim = runner.get(sim_id)
    if not sim:
        raise HTTPException(status_code=404, detail="Simulation not found")
    sim.stop()
    return sim.to_dict()


@app.delete("/api/simulations/{sim_id}")
async def delete_simulation(sim_id: str):
    sim = runner.get(sim_id)
    if not sim:
        raise HTTPException(status_code=404, detail="Simulation not found")
    runner.delete(sim_id)
    return {"status": "deleted", "id": sim_id}


@app.get("/api/simulations/{sim_id}")
async def get_simulation(sim_id: str):
    sim = runner.get(sim_id)
    if not sim:
        raise HTTPException(status_code=404, detail="Simulation not found")
    return sim.to_dict()


@app.get("/api/simulations/{sim_id}/history")
async def get_history(sim_id: str, window: float = 300):
    sim = runner.get(sim_id)
    if not sim:
        raise HTTPException(status_code=404, detail="Simulation not found")
    return sim.get_history(window)


@app.get("/api/simulations/{sim_id}/stream")
async def stream_simulation(sim_id: str):
    sim = runner.get(sim_id)
    if not sim:
        raise HTTPException(status_code=404, detail="Simulation not found")

    async def event_generator():
        with sim._lock:
            last_idx = len(sim.history)
        while True:
            await asyncio.sleep(0.5)
            with sim._lock:
                new_data = list(sim.history)[last_idx:]
                last_idx = len(sim.history)
                current_status = sim.status
            for snapshot in new_data:
                yield {"event": "snapshot", "data": json.dumps(snapshot)}
            if current_status in ("completed", "error", "stopped"):
                yield {"event": "status", "data": json.dumps({"status": current_status})}
                while True:
                    await asyncio.sleep(60)

    return EventSourceResponse(event_generator(), ping=30)


@app.websocket("/ws/controller/{sim_id}")
async def websocket_controller(websocket: WebSocket, sim_id: str):
    sim = runner.get(sim_id)
    if not sim:
        await websocket.close(code=4004, reason="Simulation not found")
        return

    if sim.status not in ("running", "idle"):
        await websocket.close(code=4003, reason=f"Simulation is {sim.status}")
        return

    await websocket.accept()

    rc = sim.connect_remote_controller()

    try:
        while True:
            measurements = None
            with sim._lock:
                if sim.history:
                    measurements = sim.history[-1]

            if measurements:
                await websocket.send_json({
                    "type": "measurements",
                    "data": measurements,
                })

            try:
                data = await asyncio.wait_for(websocket.receive_json(), timeout=2.0)
                if data.get("type") == "setpoints":
                    rc.set_pending_setpoints(data.get("setpoints", {}))
                elif data.get("type") == "ping":
                    await websocket.send_json({"type": "pong"})
            except asyncio.TimeoutError:
                pass
            except WebSocketDisconnect:
                break

    except WebSocketDisconnect:
        pass
    finally:
        sim.disconnect_remote_controller()
