import requests
import json
import os
import time
from dotenv import load_dotenv
load_dotenv()

# Import components from your custom logger.py
from logger import websocket_logger, debug_logger, access_logger, log_request
from flask import Flask, send_from_directory, jsonify, request
from flask_cors import CORS
from flask_sock import Sock
from langgraph.types import Command
from langgraph.errors import GraphInterrupt
from DVR_code.state import drivers_list as DriverModel
from DVR_code.Graph_code import create_graph
from langgraph.checkpoint.sqlite import SqliteSaver
from DVR_code.exceptions import DVRException


# Initialize distinct loggers for different scopes
ws_logger = websocket_logger()
d_logger = debug_logger()
a_logger = access_logger()


app = Flask(__name__, static_folder="images", static_url_path="/images")

CORS(app)

sock = Sock(app)

url = os.getenv('LM_API_URL', 'https://api.lightmetrics.co/v1')


@app.after_request
def log_http_request(response):
    """Automatically pipes every completed HTTP endpoint transaction into access.log."""
    log_request(response, a_logger)
    return response


# Built React app (frontend/dist). Falls back to the legacy single-file
# DVR_frontend.html when the bundle hasn't been built yet (`cd frontend && npm run build`).
FRONTEND_DIST = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'frontend', 'dist')

@app.route('/debug-interrupt-test')
def debug_interrupt_test():
    from DVR_code.Graph_code import create_graph
    graph = create_graph()
    config = {"configurable": {"thread_id": "debug-test-state-1"}}
    result = graph.invoke(
        {"query": "fetch trips in the last month", "fleet_id": "acmetransport", "drivers": [], "query_type": "simple_query"},
        config=config
    )
    state = graph.get_state(config)
    return jsonify({
        "keys": list(result.keys()),
        "has_interrupt_in_result": "__interrupt__" in result,
        "state_next": list(state.next) if state else None,
        "state_tasks": [
            {"name": t.name, "interrupts": [str(i) for i in t.interrupts]}
            for t in state.tasks
        ] if state else None
    })

@app.route('/')
def get_ui():
    # if os.path.exists(os.path.join(FRONTEND_DIST, 'index.html')):
    #     return send_from_directory(FRONTEND_DIST, 'index.html')
    return send_from_directory('.', 'DVR_frontend.html')


@app.route('/assets/<path:filename>')
def frontend_assets(filename):
    """Serve Vite's hashed JS/CSS bundles referenced by the built index.html."""
    return send_from_directory(os.path.join(FRONTEND_DIST, 'assets'), filename)


@app.route('/<fleet_id>/load-data')
def fetch_driver_data(fleet_id):
    # Retrieve fresh auth tokens via management layers
    from utils.auth import get_headers
    headers = get_headers()

    drivers_response = requests.get(
        url=f'{url}/fleets/{fleet_id}/drivers/list',
        headers=headers
    )

    if drivers_response.status_code != 200:
        d_logger.error(f"Failed to fetch drivers for fleet {fleet_id}: {drivers_response.status_code}")
        return jsonify({'error': 'Failed to fetch drivers'}), drivers_response.status_code

    drivers_data = drivers_response.json()
    drivers = drivers_data.get('rows', [])
    driver_list = [
        {
            'driverId': d.get('driverId') or 'UNASSIGNED',
            'driverName': d.get('driverName') or 'UNASSIGNED'
        }
        for d in drivers
    ]

    fleet_response = requests.get(
        url=f'{url}/fleets/{fleet_id}/trips',
        headers=headers
    )

    if fleet_response.status_code != 200:
        d_logger.error(f"Failed to fetch trips for fleet {fleet_id}: {fleet_response.status_code}")
        return jsonify({'error': 'Failed to fetch trips'}), fleet_response.status_code

    fleet_trips = fleet_response.json().get('rows', [])

    asset_set = set()
    trip_ids = []
    fleet_events = set()
    for trip in fleet_trips:
        asset_set.add(trip['asset']['assetId'])
        trip_ids.append(trip['tripId'])
        for key in trip.get('eventCount', {}):
            fleet_events.add(key.lower().strip())

    return jsonify({
        'drivers': driver_list,
        'events': list(fleet_events),
        'asset_ids': list(asset_set),
        'trip_ids': trip_ids
    })


@app.route('/health')
def health():
    return jsonify({'status': 'running'})


def _invoke_graph(ws, graph, state_or_command, config):
    """
    Single wrapper for all graph.invoke() calls.
    Handles DVRException, GraphInterrupt, and generic exceptions
    in one place so we don't repeat the try/except three times.
    """
    try:
        result = graph.invoke(state_or_command, config=config)
        _handle_result(ws, result)

    except DVRException as e:
        ws_logger.error(f'DVR error [{e.status_code}]: {e.message}')
        ws.send(json.dumps(e.to_dict()))

    except GraphInterrupt:
        ws_logger.error(
            "GraphInterrupt leaked out of graph.invoke(). "
            "Check that your checkpointer is initialised correctly."
        )
        ws.send(json.dumps({
            "type": "error",
            "message": "Something interrupted the process unexpectedly. Please try again."
        }))

    except Exception as e:
        ws_logger.error(f'Unexpected error: {e}', exc_info=True)
        ws.send(json.dumps({
            "type": "error",
            "message": "Something went wrong on our end. Please try again."
        }))


@sock.route('/chat')
def chat_socket(ws):
    fleet_data = {
        "drivers": [],
        "asset_ids": [],
        "trip_ids": [],
        "events": [],
        "fleet_id": None
    }

    ws_logger.info("Client connected")
    graph = create_graph()
    try:
        while True:
            raw = ws.receive()
            if raw is None:
                ws_logger.info("Client sent empty frame — closing.")
                break

            try:
                payload = json.loads(raw)
            except json.JSONDecodeError as e:
                ws_logger.warning(f"Malformed JSON from client: {e}")
                ws.send(json.dumps({
                    "type": "error",
                    "message": "Invalid JSON payload."
                }))
                continue

            msg_type = payload.get("type")
            thread_id = payload.get("thread_id")
            config = {"configurable": {"thread_id": thread_id}}
            ws_logger.info(f"ws message: {msg_type} | thread: {thread_id}")
            # DEBUG: log the full incoming payload (minus noisy fleet_data) so we
            # can see exactly what shape the frontend actually sent, not just the
            # message type - needed to diagnose client/backend contract mismatches.
            _debug_payload = {k: v for k, v in payload.items() if k != "fleet_data"}
            ws_logger.info(f"DEBUG raw payload: {json.dumps(_debug_payload, default=str)}")

            try:
                if msg_type == "load_data":
                    raw_fleet = payload.get("fleet_data", {})
                    fleet_data = {
                        "fleet_id":   raw_fleet.get("fleet_id"),
                        "drivers":    raw_fleet.get("drivers", []),
                        "asset_ids":  raw_fleet.get("asset_ids", []),
                        "trip_ids":   raw_fleet.get("trip_ids", []),
                        "events":     raw_fleet.get("events", []),
                        "driver_objects": [
                            DriverModel(
                                driverId=d["driverId"],
                                driverName=d["driverName"]
                            )
                            for d in raw_fleet.get("drivers", [])
                        ]
                    }
                    ws_logger.info(
                        f"Fleet loaded: {fleet_data['fleet_id']} | "
                        f"{len(fleet_data['drivers'])} drivers | "
                        f"{len(fleet_data['asset_ids'])} assets"
                    )
                    ws.send(json.dumps({"type": "load_complete"}))

                elif msg_type == "autocomplete":
                    option = payload.get("option")
                    search = payload.get("search", "").lower()
                    suggestions = []

                    if option == "Drivers":
                        suggestions = [
                            d for d in fleet_data["drivers"]
                            if d["driverName"].lower().startswith(search)
                        ]
                    elif option == "Assets":
                        suggestions = [
                            a for a in fleet_data["asset_ids"]
                            if a.lower().startswith(search)
                        ]
                    elif option == "Trips":
                        suggestions = [
                            t for t in fleet_data["trip_ids"]
                            if t.lower().startswith(search)
                        ]
                    elif option == "Event Types":
                        suggestions = [
                            e for e in fleet_data["events"]
                            if e.lower().startswith(search)
                        ]
                    else:
                        ws_logger.warning(f"Unknown autocomplete option: {option}")

                    ws.send(json.dumps({
                        "type": "autocomplete_results",
                        "suggestions": suggestions[:10]
                    }))

                elif msg_type == "autocomplete_result":
                    option = payload.get("option")
                    item = payload.get("selectedItem")

                    if not item:
                        ws.send(json.dumps({
                            "type": "error",
                            "message": "No item selected."
                        }))
                        continue

                    query_text = payload.get("query", "")
                    state = {
                        "query":         query_text,
                        # extract_dvr_intent reads user_response, not query. On
                        # a fresh invoke that lands on the start_check shortcut
                        # (straight to Extract_DVR_Intent, bypassing
                        # Show_Results where user_response is normally set),
                        # user_response would otherwise stay stale from a
                        # previous turn.
                        "user_response": query_text,
                        "drivers":       fleet_data.get("driver_objects", []),
                        "query_type":    "directed"
                    }
                    # Only set fleet_id if we actually have one for this connection -
                    # omitting it (rather than passing None) avoids clobbering a
                    # fleet_id already saved in the checkpoint from before a reconnect.
                    if fleet_data.get("fleet_id"):
                        state["fleet_id"] = fleet_data["fleet_id"]

                    if option == "Drivers":
                        state["chosen_driver"] = [{
                            "driverId":   item["driverId"],
                            "driverName": item["driverName"]
                        }]
                    elif option == "Assets":
                        raw_asset = item if isinstance(item, str) else item.get("assetId")
                        # Defensive: chosen_asset_id must be list[str] | None. If the
                        # client ever echoes the backend's own chosen_asset_id (already
                        # a list) back as assetId instead of a single string, flatten it
                        # instead of double-wrapping - a stray list-of-a-list here
                        # permanently breaks this thread (every future graph.invoke()
                        # re-validates the full checkpoint against AgentState before any
                        # node runs, so one bad write here bricks the thread for good).
                        if isinstance(raw_asset, list):
                            state["chosen_asset_id"] = [a for a in raw_asset if isinstance(a, str)]
                        elif isinstance(raw_asset, str):
                            state["chosen_asset_id"] = [raw_asset]
                        else:
                            state["chosen_asset_id"] = []
                    elif option == "Trips":
                        state["chosen_trip_id"] = (
                            item if isinstance(item, str)
                            else item.get("tripId")
                        )
                    elif option == "Event Types":
                        raw_event = item if isinstance(item, str) else item.get("event_type")
                        if isinstance(raw_event, list):
                            state["chosen_event"] = [e for e in raw_event if isinstance(e, str)]
                        elif isinstance(raw_event, str):
                            state["chosen_event"] = [raw_event]
                        else:
                            state["chosen_event"] = []
                    else:
                        ws_logger.warning(
                            f"Unknown autocomplete_result option: {option}"
                        )

                    # uses the wrapper — no try/except needed here
                    _invoke_graph(ws, graph, state, config)

                elif msg_type == "resume_graph":
                    resume_value = payload.get("resume_value")
                    if resume_value is None:
                        ws.send(json.dumps({
                            "type": "error",
                            "message": "Missing resume_value."
                        }))
                        continue

                    _invoke_graph(ws, graph, Command(resume=resume_value), config)

                elif msg_type == "only_query":
                    query = payload.get("query", "").strip()
                    if not query:
                        ws.send(json.dumps({
                            "type": "error",
                            "message": "Empty query."
                        }))
                        continue

                    state = {
                        "query":         query,
                        # See the same note in the autocomplete_result handler
                        # above - extract_dvr_intent reads user_response, and
                        # this is a fresh invoke that can bypass Show_Results
                        # (where user_response is otherwise set) via the
                        # start_check shortcut at START.
                        "user_response": query,
                        "drivers":       fleet_data.get("driver_objects", []),
                        "query_type":    "simple_query"
                    }
                    # Only set fleet_id if we actually have one for this connection -
                    # omitting it (rather than passing None) avoids clobbering a
                    # fleet_id already saved in the checkpoint from before a reconnect
                    # (e.g. a query arriving before load_data on a fresh socket).
                    if fleet_data.get("fleet_id"):
                        state["fleet_id"] = fleet_data["fleet_id"]

                    _invoke_graph(ws, graph, state, config)

                else:
                    ws_logger.warning(f"Unknown message type: {msg_type}")
                    ws.send(json.dumps({
                        "type": "error",
                        "message": f"Unknown message type: {msg_type}"
                    }))

            except Exception as e:
                ws_logger.error(
                    f"Error handling '{msg_type}' message: {e}",
                    exc_info=True
                )
                ws.send(json.dumps({
                    "type": "error",
                    "message": "Something went wrong. Please try again."
                }))

    except Exception as e:
        ws_logger.error(f"WebSocket connection error: {e}", exc_info=True)

    finally:
        ws_logger.info("Client disconnected.")


def _handle_result(ws, result: dict):
    """Send graph result back to the frontend over the WebSocket."""
    ws_logger.info(f"graph result keys: {list(result.keys()) if isinstance(result, dict) else type(result)}")
    ws_logger.info(f"graph result (full): {result}")
    if not isinstance(result, dict):
        ws_logger.error(f"Unexpected graph result type: {type(result)}")
        ws.send(json.dumps({
            "type": "error",
            "message": "Unexpected response from graph."
        }))
        return

    if "__interrupt__" in result:
        # Send any partial chat response before showing the interrupt UI
        if result.get("chat_response"):
            ws.send(json.dumps({
                "type": "chat_response",
                "response": {"chat_response": result["chat_response"]},
                "more": True
            }))

        interrupt_data = result["__interrupt__"][0].value
        ws.send(json.dumps({
            "type": "interrupt",
            "payload": interrupt_data
        }))
        return

    response = {}

    if result.get("uploadRequestId"):
        response["uploadRequestId"] = result["uploadRequestId"]
        response["dvr_summary"]     = result.get("dvr_summary")
    elif result.get("chat_response"):
        response["chat_response"] = result["chat_response"]
    else:
        response["chat_response"] = "Request processed."

    ws.send(json.dumps({
        "type": "chat_response",
        "response": response,
        "more": False
    }))

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    debug = os.environ.get("FLASK_DEBUG", "false").lower() == "true"
    app.run(host="0.0.0.0", port=port, debug=debug)