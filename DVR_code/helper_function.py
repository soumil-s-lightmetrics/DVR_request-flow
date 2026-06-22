import os 
import requests
from datetime import datetime

from datetime import timezone  # add this import alongside datetime, timedelta

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
        if asset_id and t.get('assetId') != asset_id:
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

def resolve_driver_matches(fleet_id, driver_name):
    if not driver_name:
        return []
    url = os.getenv('LM_API_URL')
    auth_token = os.getenv('LM_ACCESS_TOKEN')
    id_token = os.getenv('LM_ID_TOKEN')
    headers = {'Authorization': f"Bearer {auth_token}", 'id-token': id_token, 'x-lm-desired-account': 'lmpresales'}
    request = requests.get(url=f"{url}/fleets/{fleet_id}/drivers/list?search={str(driver_name).lower()}", headers=headers)
    data = request.json()
    return [{'driverName': d['driverName'], 'driverId': d['driverId']} for d in data.get('rows', [])]

