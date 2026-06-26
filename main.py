from fastapi import FastAPI, WebSocket, responses
from fastapi.websockets import WebSocketDisconnect
from langgraph.types import Command
from DVR_code.state import drivers_list as DriverModel
import logging
from DVR_code.Graph_code import create_graph
from fastapi.staticfiles import StaticFiles
import requests
import json
import os
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)-8s | %(funcName)s | %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger('DVR_Backend')

app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="image")

@app.get("/", response_class=responses.HTMLResponse)
async def get_ui():
    with open("index.html", "r") as f:
        return f.read()


@app.get('/{fleet_id}/load-data')
async def fetch_driver_data(fleet_id: str):
    url = os.getenv('LM_API_URL')
    auth_token = os.getenv('LM_ACCESS_TOKEN')
    id_token = os.getenv('LM_ID_TOKEN')
    headers = {
        'Authorization': f"Bearer {auth_token}",
        'id-token': id_token,
        'x-lm-desired-account': 'lmpresales'
    }

    drivers_response = json.loads(
        requests.get(url=f'{url}/fleets/{fleet_id}/drivers/list', headers=headers).text
    )
    drivers = drivers_response.get('rows', [])
    driver_list = []
    for driver in drivers:
        driver_list.append({
            'driverId': driver.get('driverId') or 'UNASSIGNED',
            'driverName': driver.get('driverName') or 'UNASSIGNED'
        })

    fleet_response = json.loads(
        requests.get(url=f"{url}/fleets/{fleet_id}/trips", headers=headers).text
    )
    fleet_trips = fleet_response.get('rows', [])

    asset_set = set()
    trip_ids = []
    fleet_events = set()
    for trip in fleet_trips:
        asset_set.add(trip['asset']['assetId'])
        trip_ids.append(trip['tripId'])
        for key in trip.get('eventCount', {}):
            fleet_events.add(key.lower().strip())

    return {
        'drivers': driver_list,
        'events': list(fleet_events),
        'asset_ids': list(asset_set),
        'trip_ids': trip_ids
    }

@app.websocket("/chat")
async def chat_socket(websocket: WebSocket):
    await websocket.accept()

    fleet_data = {
        "drivers": [], "asset_ids": [], "trip_ids": [],
        "events": [], "fleet_id": None
    }

    graph = create_graph()

    try:
        while True:
            payload = await websocket.receive_json()
            thread_id = payload.get("thread_id")
            config = {"configurable": {"thread_id": thread_id}}
            msg_type = payload.get("type")
            logger.info(f'ws message: {msg_type}')

            if msg_type == "load_data":
                fleet_data = payload["fleet_data"]
                fleet_data["driver_objects"] = [
                    DriverModel(driverId=d["driverId"], driverName=d["driverName"])
                    for d in fleet_data.get("drivers", [])
                ]
                await websocket.send_json({"type": "load_complete"})

            elif msg_type == "autocomplete":
                option = payload.get("option")
                search = payload.get("search", "").lower()
                suggestions = []
                if option == "Drivers":
                    suggestions = [d for d in fleet_data["drivers"] if d["driverName"].lower().startswith(search)]
                elif option == "Assets":
                    suggestions = [a for a in fleet_data["asset_ids"] if a.lower().startswith(search)]
                elif option == "Trips":
                    suggestions = [t for t in fleet_data["trip_ids"] if t.lower().startswith(search)]
                elif option == "Event Types":
                    suggestions = [e for e in fleet_data["events"] if e.lower().startswith(search)]
                await websocket.send_json({"type": "autocomplete_results", "suggestions": suggestions[:10]})

            elif msg_type == "autocomplete_result":
                state = {
                    "query": payload["query"],
                    "fleet_id": fleet_data.get("fleet_id"),
                    "drivers": fleet_data.get("driver_objects", []),
                    "query_type": "directed"
                }
                option = payload.get("option")
                item = payload.get("selectedItem")
                if option == "Drivers":
                    state["chosen_driver"] = [{"driverId": item["driverId"], "driverName": item["driverName"]}]
                elif option == "Assets":
                    state["chosen_asset_id"] = item["assetId"]
                elif option == "Trips":
                    state["chosen_trip_id"] = item["tripId"]
                elif option == "Event Types":
                    state["chosen_event"] = item["event_type"]

                result = graph.invoke(state, config=config)
                await _handle_result(websocket, result)

            elif msg_type == "resume_graph":
                result = graph.invoke(
                    Command(resume=payload["resume_value"]),
                    config=config
                )
                await _handle_result(websocket, result)

            elif msg_type == "only_query":
                state = {
                    "query": payload["query"],
                    "fleet_id": fleet_data.get("fleet_id"),
                    "drivers": fleet_data.get("driver_objects", []),
                    "query_type": "simple_query"
                }
                result = graph.invoke(state, config=config)
                await _handle_result(websocket, result)

    except WebSocketDisconnect:
        logger.info("Client disconnected.")
    except Exception as e:
        logger.error(f"Unexpected error: {e}", exc_info=True)


async def _handle_result(websocket: WebSocket, result: dict):
    if "__interrupt__" in result:
        # If a chat_response exists alongside an interrupt (e.g. general-answer loop-back,
        # or DVR-cancelled loop-back), send it first so it appears before the next prompt.
        if result.get("chat_response"):
            await websocket.send_json({
                "type": "chat_response",
                "response": {"chat_response": result["chat_response"]},
                "more": True
            })

        interrupt_data = result["__interrupt__"][0].value
        await websocket.send_json({
            "type": "interrupt",
            "payload": interrupt_data
        })
    else:
        # Graph has reached END — this is the final message in this turn
        response = {}
        if result.get("uploadRequestId"):
            response["uploadRequestId"] = result["uploadRequestId"]
            response["dvr_summary"] = result.get("dvr_summary")
        elif result.get("chat_response"):
            response["chat_response"] = result["chat_response"]
        else:
            response["chat_response"] = "Request processed."

        await websocket.send_json({
            "type": "chat_response",
            "response": response,
            "more": False
        })