# eSysSim Remote Controller Protocol

## Connection

Connect via WebSocket to:

```
ws://<host>:<port>/ws/controller/{sim_id}
wss://esyssim.collideworks.com/ws/controller/{sim_id}
```

Where `{sim_id}` is the simulation ID returned when creating a simulation via `POST /api/simulations`.

## Protocol

All messages are JSON. The connection is bidirectional:

### Server → Client

**`measurements`** — Sent each simulation step after power flow converges:

```json
{
  "type": "measurements",
  "data": {
    "time": "2025-01-01T05:30:00",
    "sim_elapsed": 10.5,
    "buses": {
      "Bus1": {"vm_pu": 1.0, "va_degree": 0.0},
      "Bus2": {"vm_pu": 0.998, "va_degree": -0.5}
    },
    "loads": {
      "Racks1": {"p_mw": -0.165, "q_mvar": 0.0},
      "Racks2": {"p_mw": -0.180, "q_mvar": 0.0}
    },
    "grids": {
      "PCC": {
        "p_mw": 0.658,
        "q_mvar": 0.0,
        "purchase_price": 42.5,
        "sell_price": 14.875
      }
    },
    "lines": {
      "line1": {"p_from_mw": 0.658, "p_to_mw": 0.652}
    },
    "batteries": {
      "BESS": {"p_mw": 0.0, "soc_pu": 0.5}
    },
    "generators": {
      "PV": {"p_mw": 0.5, "q_mvar": 0.0},
      "Genset1": {"p_mw": 0.0, "q_mvar": 0.0}
    }
  }
}
```

**`pong`** — Response to client ping:

```json
{"type": "pong"}
```

### Client → Server

**`setpoints`** — Control actions for the next simulation step:

```json
{
  "type": "setpoints",
  "setpoints": {
    "batteries": {
      "BESS": {"p_mw": -0.5}
    },
    "generators": {
      "Genset1": {"p_mw": 1.0}
    }
  }
}
```

**`ping`** — Keepalive:

```json
{"type": "ping"}
```

## Sign Convention

| Element | Positive `p_mw` | Negative `p_mw` |
|---|---|---|
| **Load** | — | Consuming power |
| **Generator** | Injecting power | — |
| **Battery** | Discharging (supplying) | Charging (consuming) |
| **Grid** | Injecting to network | Drawing from network |

## Controllable Elements

### Batteries

| Field | Type | Description |
|---|---|---|
| `p_mw` | float | Active power setpoint (MW). Negative = charge, positive = discharge. Clamped to `[p_min_mw, p_max_mw]`. |

### Generators

| Field | Type | Description |
|---|---|---|
| `p_mw` | float | Active power setpoint (MW). Clamped to `[0, max_p_mw]`. |

## Example: Python Client

```python
import asyncio
import websockets

async def controller():
    uri = "wss://esyssim.collideworks.com/ws/controller/{sim_id}"
    async with websockets.connect(uri) as ws:
        while True:
            msg = await ws.recv()
            data = json.loads(msg)

            if data["type"] == "measurements":
                m = data["data"]

                # Read measurements
                price = m["grids"]["PCC"]["purchase_price"]
                soc = m["batteries"]["BESS"]["soc_pu"]
                pv = m["generators"]["PV"]["p_mw"]
                load = sum(v["p_mw"] for v in m["loads"].values())

                # Compute control action
                setpoints = {"batteries": {}, "generators": {}}

                if price < 40 and soc < 0.8:
                    setpoints["batteries"]["BESS"] = {"p_mw": -0.5}  # charge
                elif price > 50 and soc > 0.2:
                    setpoints["batteries"]["BESS"] = {"p_mw": 0.5}   # discharge
                else:
                    setpoints["batteries"]["BESS"] = {"p_mw": 0.0}   # idle

                # Send setpoints
                await ws.send(json.dumps({
                    "type": "setpoints",
                    "setpoints": setpoints,
                }))

asyncio.run(controller())
```

## Error Codes

| Code | Reason |
|---|---|
| 4003 | Simulation not running |
| 4004 | Simulation not found |

## Architecture

```
┌─────────────────┐         WebSocket          ┌──────────────────┐
│  Remote         │  ── measurements ──►        │  eSysSim Server  │
│  Controller     │  ◄── setpoints ────         │  (FastAPI)       │
│  (your code)    │                             │                  │
└─────────────────┘                             │  ┌────────────┐  │
                                                │  │Simulation  │  │
                                                │  │  State     │  │
                                                │  └─────┬──────┘  │
                                                │        │         │
                                                │  ┌─────▼──────┐  │
                                                │  │Controller  │  │
                                                │  │ Interface  │  │
                                                │  └─────┬──────┘  │
                                                │        │         │
                                                │  ┌─────▼──────┐  │
                                                │  │Local Ctrl  │  │
                                                │  │(default)   │  │
                                                │  └────────────┘  │
                                                └──────────────────┘
```

When a remote controller connects, it replaces the local controller. When it disconnects, the local controller resumes automatically.
