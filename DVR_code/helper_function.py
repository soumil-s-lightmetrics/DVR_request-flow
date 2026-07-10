import requests
import os
from datetime import datetime, timezone
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from DVR_code.state import AgentState
from pydantic import BaseModel
from DVR_code.state import Timestamp
from DVR_code.prompt import merge_query
from logger import debug_logger
from typing import Literal
from utils.auth import auth_manager
from langsmith import traceable


debug_logger = debug_logger()


llm_for_advance_reasoning = ChatOpenAI(
    model='gpt-5.4',
    api_key=os.getenv('OPENAI_API_KEY')
)


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
        parts.append(
            f"date range: {state.chosen_timestamp.start_time} "
            f"to {state.chosen_timestamp.end_time}"
        )
    return "; ".join(parts) if parts else "none"


def to_aware_utc(val):
    dt = datetime.fromisoformat(val.replace('Z', '+00:00'))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def filter_enriched_trips(
        trips,
        driver_ids=None,
        asset_id=None,
        event_list=None,
        date_start=None,
        date_end=None):

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

@traceable(run_type='tool')
def resolve_driver_matches(fleet_id, driver_names: list):
    """
    Searches for driver names in the Fleet's drivers list, 
    returns a list of dictionary [{'driverId' : driverId, 'driverName' : driverName}]
    """
    if not driver_names:
        return []

    driver_matches = []

    # endpoint allows only one driver_name at a time for search
    for driver in driver_names:
        params = {'search': driver, 'limit': 50}
        debug_logger.info(params)
        
        data = auth_manager.make_api_request(
            client_id=os.getenv('CLIENT_ID'),
            endpoint=f"/v2/fleets/{fleet_id}/drivers/list",
            params=params
        )
        driver_matches.extend([
            {'driverId': d['driverId'], 'driverName': d['driverName']}
            for d in data.get('rows', [])
        ])

    return driver_matches


class ExtractedFilters1(BaseModel):
    driver_name: list[str] | None = []
    event_type: list[str] | None = []
    trip_id: str | None = None
    asset_id: list[str] | None = []
    start_time: str | None = None
    end_time: str | None = None
    limit_to_latest: int | None = None
    events: Literal['max', 'min'] | int | None = None

@traceable(run_type='tool')
def merge_filters_from_text(state: AgentState):
    try:
        active_filters_desc = describe_active_filters(state)

        structured_llm = llm_for_advance_reasoning.with_structured_output(
            ExtractedFilters1
        )
        extracted = structured_llm.invoke([
            HumanMessage(content=state.user_response or ''),
            SystemMessage(content=f"current time and date : {datetime.now()}"),
            SystemMessage(content=f"Filters already active {active_filters_desc}"),
            SystemMessage(content=merge_query)
        ])

        debug_logger.info(
            f'Filters already active : {active_filters_desc}\n'
            f'narrow-down extraction: {extracted}'
        )

        chosen_driver = resolve_driver_matches(state.fleet_id, extracted.driver_name)
        chosen_asset_id = extracted.asset_id
        chosen_event = list(set(extracted.event_type)) if extracted.event_type else []
        
        if extracted.start_time and extracted.end_time: 
            chosen_timestamp = Timestamp(
                start_time=extracted.start_time,
                end_time=extracted.end_time)
        
        else: 
            chosen_timestamp = state.chosen_timestamp

        driver_ids = [d['driverId'] for d in (chosen_driver or [])]

        filtered = filter_enriched_trips(
            state.all_trips or [],
            driver_ids=driver_ids or None,
            asset_id=chosen_asset_id,
            event_list=chosen_event or None,
            date_start=chosen_timestamp.start_time if chosen_timestamp else None,
            date_end=chosen_timestamp.end_time if chosen_timestamp else None
        )

        base_updates = {
            'chosen_driver': chosen_driver,
            'chosen_asset_id': chosen_asset_id,
            'chosen_event': chosen_event,
            'chosen_timestamp': chosen_timestamp,
            'selected_trip_hint': None,
            'dvr_request_params': None,
            'limit_to_latest': extracted.limit_to_latest
        }

        if filtered:
            event_filter = extracted.events
            debug_logger.info(f"Event Count {event_filter}")

            if event_filter == 'max':
                max_count = max(trip['totalEvents'] for trip in filtered)
                filtered = [t for t in filtered if t['totalEvents'] == max_count]
            elif event_filter == 'min':
                min_count = min(trip['totalEvents'] for trip in filtered)
                filtered = [t for t in filtered if t['totalEvents'] == min_count]
            elif isinstance(event_filter, int):
                filtered = [t for t in filtered if t['totalEvents'] == event_filter]

            base_updates.update({
                'filter_trips': filtered,
                'results_shown': False,
                'needs_refetch': False
            })
        else:
            base_updates['needs_refetch'] = True
            base_updates['results_shown'] = False

        debug_logger.info(state.chosen_driver)
        debug_logger.info(base_updates['chosen_driver'])

        return base_updates

    except Exception as e:
        debug_logger.error(f'failed in merge_filters_from_text : {e}', exc_info=True)
        return {'error': str(e)}

