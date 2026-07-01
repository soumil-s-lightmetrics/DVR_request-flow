import os 
import requests
import logging
from datetime import datetime
from DVR_code.state import AgentState
from datetime import timezone  # add this import alongside datetime, timedelta
import logging
from utils.auth import auth_manager
import os

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)-8s | %(funcName)s | %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger('Helper Functions')
from utils.auth_util import AuthManager  # adjust import path to wherever you place this file

# logger = debug_logger()

url = "https://api.lightmetrics.co/v1"
auth_token, id_token = auth_manager._get_access_token()
logging.info(f"auth_token : {auth_token}")
headers = {
            'Authorization': f"Bearer {auth_token}",
            'id-token': id_token,
            'x-lm-desired-account': 'lmpresales'}

def describe_active_filters(state: AgentState) -> str:
    parts = []
    if state.chosen_driver:
        names = ", ".join(d['driverName'] for d in state.chosen_driver)
        parts.append(f"driver: {names}")
    if state.chosen_asset_id:
        parts.append(f"asset: {state.chosen_asset_id}")
    if state.chosen_event:
        parts.append(f"event types: {', '.join(state.chosen_event)}")
    if state.chosen_timestamp:
        parts.append(f"date range: {state.chosen_timestamp.start_time} to {state.chosen_timestamp.end_time}")
    return "; ".join(parts) if parts else "none"


def to_aware_utc(val):
    dt = datetime.fromisoformat(val.replace('Z', '+00:00'))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt

def filter_enriched_trips(trips, driver_ids=None, asset_id=None, event_list=None, date_start=None, date_end=None):
    result = []
    parsed_start = to_aware_utc(date_start) if date_start else None
    parsed_end = to_aware_utc(date_end) if date_end else None

    for t in trips:
        if driver_ids and t.get('driverId') not in driver_ids:
            continue
        if asset_id and t.get('assetId') not in asset_id:
            continue
        if event_list:
            trip_events = set(ev['type'].lower() for ev in t.get('events', []))
            if not set(e.lower() for e in event_list).issubset(trip_events):
                continue
        if (parsed_start or parsed_end) and t.get('startTimeUTC'):
            trip_dt = to_aware_utc(t['startTimeUTC'])
            if parsed_start and trip_dt < parsed_start:
                continue
            if parsed_end and trip_dt > parsed_end:
                continue
        result.append(t)
    return result

def resolve_driver_matches(fleet_id, driver_names : list):
    if not driver_names:
        return []

    driver_matches = []
    for drivers in driver_names:
        Params={ 'search' : drivers, 'limit' : 50}
        logger.info(Params)
        request = requests.get(url=f"{url}/fleets/{fleet_id}/drivers/list", params=Params, headers=headers)
        logger.info(request.json())
        data = request.json()
        driver_matches.extend([{'driverId': d['driverId'], 'driverName': d['driverName']} for d in data.get('rows', [])])

    return driver_matches