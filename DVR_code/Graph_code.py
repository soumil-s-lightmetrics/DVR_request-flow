import os
import json
import requests
from datetime import datetime, timedelta
from typing import Literal

from dotenv import load_dotenv
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.errors import GraphInterrupt
from langgraph.graph import StateGraph, START, END
from langgraph.types import interrupt
from pydantic import BaseModel
from langgraph.checkpoint.postgres import PostgresSaver
from psycopg_pool import ConnectionPool

from DVR_code.fetch_data import fetch_all_trips, fetch_trips_by_asset
from DVR_code.helper_function import (
    resolve_driver_matches,
    describe_active_filters,
    merge_filters_from_text
)
from DVR_code.exceptions import DVRException
from DVR_code.prompt import merge_query, general_query, intent_query
from DVR_code.state import AgentState, Timestamp
from logger import debug_logger
from utils.auth import get_headers
import random
load_dotenv()

# Railway's Postgres plugin auto-injects this when the DB is attached to the service.
DB_URI = os.environ["DATABASE_URL"]
pg_pool = ConnectionPool(conninfo=DB_URI, max_size=20, kwargs={"autocommit": True, "prepare_threshold": 0})
memory = PostgresSaver(pg_pool)
memory.setup()

llm_for_chat = ChatOpenAI(
    model='gpt-5.4-mini',
    api_key=os.getenv('OPENAI_API_KEY')
)
llm_for_advance_reasoning = ChatOpenAI(
    model='gpt-5.4',
    api_key=os.getenv('OPENAI_API_KEY')
)
d_logger = debug_logger()


# conditional edge checks if there any trips in the state if it
# has already fetched trips before then directly go to the show trips interrup
def start_check(state: AgentState):
    trips = state.all_trips
    if len(trips) > 0:
        return 'EXTRACT_DVR'
    return 'EXTRACT_FILTERS'


# Extracting parameters from the user query to filter the trips
class ExtractedFilters(BaseModel):
    driver_name: list[str] | None = []
    event_type: list[str] | None = []  # events mentioned in the query
    trip_id: str | None = None
    asset_id: list[str] | None = None  # assets mentioned in the query
    start_time: str | None = None
    end_time: str | None = None
    limit_to_latest: int | None = None  # if user wants last few trips, not on the basis of dates
    events: Literal['max', 'min'] | int | None = None  # for queries like : 'trips with maximum/minimum/most events'


def extract_filters(state: AgentState):
    d_logger.info('Entering node: Extract_Filters')
    try:
        structured_llm = llm_for_advance_reasoning.with_structured_output(
            ExtractedFilters
        )
        llm_response = structured_llm.invoke([
            HumanMessage(content=state.query),
            SystemMessage(
                content=merge_query.replace(
                    "{current_datetime}", datetime.now().isoformat()
                )
            )
        ])

        d_logger.info(f"Extracted info from query : {llm_response}")

        if llm_response.driver_name:
            # Matching extracted driver_names from user_query to the fleet's list of drivers
            matching_drivers_list = resolve_driver_matches(
                state.fleet_id, llm_response.driver_name)
        else:
            matching_drivers_list = []

        return {
            'chosen_driver': matching_drivers_list,
            'chosen_asset_id': llm_response.asset_id,
            'chosen_trip_id': llm_response.trip_id,
            'chosen_event': llm_response.event_type,
            'chosen_timestamp': Timestamp(
                start_time=llm_response.start_time,
                end_time=llm_response.end_time
            ) if llm_response.start_time and llm_response.end_time else None,
            'chosen_events_count': llm_response.events, # for queries like : 'trips with maximum/minimum/most events'
            'limit_to_latest': llm_response.limit_to_latest
        }
    except DVRException:
        raise

    except Exception as e:
        d_logger.error(f'failed in extract_filters: {e}', exc_info=True)
        raise DVRException(
            "We had trouble understanding your query. "
            "Could you rephrase it and try again?"
        )


def check_timestamp(state: AgentState):
    """
    conditional edge for checking if user had mentioned timestamp
    """
    ts = state.chosen_timestamp

    if state.limit_to_latest:
        return "fetch_trips"

    if not ts or not ts.start_time or not ts.end_time:
        return 'ask_timestamp'
    return 'fetch_trips'

# ask for timestamp if missing in the query

def ask_timestamp(state: AgentState):
    d_logger.info('Entering node: Ask_Timestamp')
    print()
    "if user hasn't mentioned a timeframe or hasn't asked for last/recent trips"

    d_logger.info('asking for search date range')
    ts_response = interrupt({'message': 'please provide timestamp'})
    try:
        return {
            'chosen_timestamp': Timestamp(
                start_time=ts_response['start_time'],
                end_time=ts_response['end_time']
            )
        }
    except GraphInterrupt:
        raise

    except DVRException:
        raise
    except Exception as e:
        d_logger.error(f'failed in ask_timestamp : {e}', exc_info=True)
        raise DVRException(
            "We couldn't process the date range you provided. "
            "Please try selecting it again."
        )


# Calling LM API's to fetch trip data
def fetch_trips_with_expiry(state: AgentState):
    d_logger.info('Entering node: Fetch_Trips')
    d_logger.info('Calling the API')

    try:
        # limit_to_last --> if user asks for last 10 trips else according to timestamp
        if not state.limit_to_latest:
            start_date = datetime.fromisoformat(
                state.chosen_timestamp.start_time.replace("Z", "+00:00")
            ).date()
            end_date = datetime.fromisoformat(
                state.chosen_timestamp.end_time.replace("Z", "+00:00")
            ).date()

            # api_before : specifically when user searches trips in a particular day
            # **difference between start date and end date, date needs to >= 1 day
            api_before = end_date + timedelta(days=1)

            params = {
                'before': api_before,
                'after': start_date
            }

            d_logger.info('Control number is not there')
            d_logger.info(start_date)
            control_number = 100 + state.pagination_value

        else:
            params = {}
            d_logger.info('control number is there')
            control_number = state.limit_to_latest

        driver_id_list = []

        if state.chosen_driver:
            request_url = f"/v2/fleets/{state.fleet_id}/trips"
            for driver in state.chosen_driver:
                driver_id_list.append(driver.get('driverId', ''))
            params = {**params, 'driverId': ','.join(driver_id_list)}

            response = fetch_all_trips(
                url=request_url, base_params=params,
                skip=state.pagination_value, control_number=control_number)

            all_trips = response

        elif state.chosen_asset_id:
            request_url = f"/v2/fleets/{state.fleet_id}/latest-trips-by-asset-id"

            response = fetch_trips_by_asset(
                url=request_url, assets=state.chosen_asset_id
            )

            all_trips = response

        else:
            request_url = f"/v2/fleets/{state.fleet_id}/trips"
            response = fetch_all_trips(
                url=request_url, base_params=params,
                skip=state.pagination_value, control_number=control_number)
            
            all_trips = response

        d_logger.info(all_trips[:1])

# Filtering on the basis of the asset ids, since endpoint doesn't allow filtering on the base of the asset
        if state.chosen_asset_id:
            all_trips = [
                t for t in all_trips
                if t.get('asset', {}).get('assetId') in state.chosen_asset_id
            ]

        if driver_id_list:
            all_trips = [
                t for t in all_trips
                if t.get('driverId', '') in driver_id_list
            ]

        now = datetime.now().astimezone()
        enriched = []

        for trip in all_trips:
            ts = trip.get('startTimeUTC', '')
            te = trip.get('endTimeUTC', '')

            dvr_status, dvr_until, dvr_days = 'unknown', None, None
  
            ## finding expiry of the trip
            if ts:
                trip_start = datetime.fromisoformat(ts.replace('Z', '+00:00'))
                expiry = trip_start + timedelta(days=17.5)
                remaining = (expiry - now).total_seconds() / 86400
                dvr_status = (
                    'available' if remaining > 3
                    else ('expiring' if remaining > 0 else 'expired')
                )
                dvr_until = expiry.strftime('%d %b %Y %H:%M')
                dvr_days = round(remaining, 1)

            events = [] ## event_name + event_count
            events_without_count = [] ## event_name list

# Filtering for events if specifically asked for in the user query
            for evt_type, count in trip.get('eventCount', {}).items():
                if evt_type != 'total' and count > 0:
                    events.append({'type': evt_type, 'count': count})
                    events_without_count.append(str(evt_type.lower()))

            chosen_event_list = [e.lower() for e in (state.chosen_event or [])]
            if chosen_event_list:
                result = set(chosen_event_list).issubset(set(events_without_count))
                if not result:
                    continue

            enriched.append({
                'tripId': trip.get('tripId', None),
                'driverId': trip.get('driverId', None),
                'driverName': trip.get('driverName', trip.get('driverId', '—')),
                'assetId': trip.get('asset', {}).get('assetId', '—'),
                'startTimeUTC': ts,
                'endTimeUTC': te,
                'lastPinged': te if te else trip.get('lastPingedAt', ts),
                'lastPingedLabel': 'Ended' if te else 'Ongoing',
                'dvr_status': dvr_status,
                'dvr_until': dvr_until,
                'dvr_days': dvr_days,
                'events': events,
                'totalEvents': len(events)
            })

# Filtering on the base of the number of the events if specifically mentioned in the user's query
        if enriched:
            event_filter = state.chosen_events_count
            d_logger.info(f"Event Count {event_filter}")

            if event_filter == 'max':
                max_count = max(trip['totalEvents'] for trip in enriched)
                enriched = [t for t in enriched if t['totalEvents'] == max_count]
            elif event_filter == 'min':
                min_count = min(trip['totalEvents'] for trip in enriched)
                enriched = [t for t in enriched if t['totalEvents'] == min_count]
            elif isinstance(event_filter, int):
                enriched = [t for t in enriched if t['totalEvents'] == event_filter]

        d_logger.info(f'API Fetched trips : {enriched[:1]}')

        enriched.sort(key=lambda t: t.get('startTimeUTC', ''), reverse=True)
        
        if state.pagination_value > 0:
            trips = state.all_trips
            enriched = trips + enriched

        return {'all_trips': enriched, 'first_query': True, 'pagination_value' :0}

    except GraphInterrupt:
        raise
    except DVRException:
        raise
    except requests.exceptions.Timeout:
        d_logger.error('Timeout fetching trips', exc_info=True)
        raise DVRException.from_timeout()
    except requests.exceptions.ConnectionError:
        d_logger.error('Connection error fetching trips', exc_info=True)
        raise DVRException.from_connection_error()
    except Exception as e:
        d_logger.error(f'failed in fetch_trips_with_expiry: {e}', exc_info=True)
        raise DVRException(
            "We ran into an issue fetching your trips. "
            "Please try again in a moment."
        )
    
def check_trips(state: AgentState):
    d_logger.info('Checking trip length')
    if len(state.filter_trips or []) == 0:
        return "fetch_again"
    return "show results"

# Dummy node to introduce a conditional edge that would revert the Graph back to fetching trips/requesting a 
# new DVR if the previous DVR request doesn't match user requirements

def check_trips_node(state: AgentState):
    d_logger.info('Entering node: Check_Trips')
    return {'first_query': False}

# Showing all the thus filtered trips

def show_results(state: AgentState):
    d_logger.info('Entering node: Show_Results')
    
    d_logger.info(state.chosen_timestamp)

    # Checking if we are to show the full trip list or the filtered list
    if state.first_query:
        trips = state.all_trips
    else:
        trips = state.filter_trips or []

    first_time = not state.results_shown

    if not state.error:
        interrupt_payload = {
            'message': 'show_results',
            'trips': trips[:state.limit_to_latest] if state.limit_to_latest else trips,
            'summary': "" if len(trips) > 0 else state.chat_response,
            'first': first_time
        }

        interrupt_payload['filters'] = {
                'driver': {
                    'driverId': state.chosen_driver[0]['driverId'],
                    'driverName': state.chosen_driver[0]['driverName']
                } if state.chosen_driver else None,
                'asset': state.chosen_asset_id,
                'events': state.chosen_event,
                'date_range': {
                    'start': state.chosen_timestamp.start_time,
                    'end': state.chosen_timestamp.end_time
                } if state.chosen_timestamp else None
            }

# Graph Interrupt if there was any error
    else: 
        interrupt_payload = {
            'message': 'Unexpected error occured, please try again',
            'trips': trips[:state.limit_to_latest] if state.limit_to_latest else trips,
            'summary': 'Unexpected error occured, please try again',
            'first': first_time
        }

    msg = interrupt(interrupt_payload)

    if isinstance(msg, dict):
        text = msg.get('text')
        trip_hint = msg.get('tripId')
        active_filters = msg.get('activeFilters')
        pagination_value = msg.get('pagination', None)  ## will give the skip value for the trips
        
    else:
        text = msg
        trip_hint = None
        active_filters = None
        pagination_value = None

    updates = {'user_response': text, 'needs_refetch': False, 'error' : None}

    if trip_hint:
        updates['selected_trip_hint'] = trip_hint

    if active_filters is not None:
        updates['chosen_driver'] = (
            [active_filters['driver']] if active_filters.get('driver') else []
        )
        updates['chosen_asset_id'] = active_filters.get('asset')
        updates['chosen_event'] = active_filters.get('events')
        updates['chosen_timestamp'] = Timestamp(
            start_time=active_filters['date_range']['start'],
            end_time=active_filters['date_range']['end']
        ) if active_filters.get('date_range') else None

    updates['pagination_value'] = pagination_value
    updates['results_shown'] = True

    return updates

# Check if user has asked to load more trips
def check_pagination(state: AgentState):
    if state.pagination_value:
        return 'FETCH'
    return 'CONTINUE'


class ExtractedFilters1(BaseModel):
    driver_name: list[str] | None = []
    event_type: list[str] | None = []
    trip_id: str | None = None
    asset_id: list[str] | None = []
    start_time: str | None = None
    end_time: str | None = None
    limit_to_latest: int | None = None
    events: Literal['max', 'min'] | int | None = None


class DvrIntent(BaseModel):
    intent: Literal['general_question', 'show_trips', 'dvr_request']
    dvr_type: Literal['clip', 'timelapse'] | None = None
    trip_id: str | None = None
    start_time: str | None = None
    end_time: str | None = None


def extract_dvr_intent(state: AgentState):
    d_logger.info('Entering node: Extract_DVR_Intent')
    d_logger.info('extracting DVR intent from message')
    text = state.user_response or ''

    trips = state.all_trips if state.first_query else state.filter_trips

    try:
        if trips and len(trips) > 0:
            d_logger.info(json.dumps(trips[0]))
            trip_summary = json.dumps([
                {
                    'tripId': t['tripId'],
                    'startTimeUTC': t['startTimeUTC'],
                    'endTimeUTC': t['endTimeUTC'],
                    'driverId': t['driverId'],
                    'assetId': t['assetId'],
                    'events': t.get('eventCount', {})
                }
                for t in trips
            ])
        else:
            trip_summary = 'No trips yet'

        active_filters_desc = describe_active_filters(state)

        system = (
            f"Trips currently shown to the user: {trip_summary}\n"
            f"Filters currently active: {active_filters_desc}\n\n"
            f"{intent_query}"
        )

        structured_llm = llm_for_advance_reasoning.with_structured_output(DvrIntent)
        response = structured_llm.invoke([
            HumanMessage(content=text),
            SystemMessage(content=system)
        ])
        d_logger.info(f'intent: {response}')

        # The parameters from the second query will be merged with the new parameters
        if response.intent == 'show_trips':
            d_logger.info('>>> BEFORE merge_filters_from_text call')
            updated_state = merge_filters_from_text(state)

            d_logger.info(f"Showing timestamp for the merged state : {updated_state['chosen_timestamp']}")

            d_logger.info(
                '>>> AFTER merge_filters_from_text call, about to log state'
            )
            try:
                d_logger.info(
                    f'>>> state.chosen_driver = {state.chosen_driver}'
                )
            except Exception as log_err:
                d_logger.error(
                    f'>>> logging state.chosen_driver FAILED: {log_err!r}'
                )
            try:
                d_logger.info(f'>>> updated_state = {updated_state}')
            except Exception as log_err:
                d_logger.error(
                    f'>>> logging updated_state FAILED: {log_err!r}'
                )
            d_logger.info('>>> RETURNING updated_state now')
            return updated_state

        # In case the user asks a general question about the trips
        if response.intent == 'general_question':
            prompt = f"trip_results: {trip_summary}\nuser_question: {text}"
            answer = llm_for_chat.invoke([
                HumanMessage(content=prompt),
                SystemMessage(content=general_query),
                SystemMessage(content=f'current date and time {datetime.now()}')
            ]).content
            return {
                'chat_response': answer,
                'dvr_request_params': None,
                'selected_trip_hint': None
            }

        # intent == 'dvr_request'
        trip = None
        # selected_trip_hint and chosen_trip_id are both explicit, unambiguous
        # selections (user clicked "Use trip" on a specific row - via a
        # resume_graph reply while an interrupt is active, or via an
        # autocomplete_result "Trips" selection on a fresh invoke once the
        # graph has already reached END). Prefer either of these deterministic
        # ids over the LLM-parsed response.trip_id fallback below, and look
        # them up against the full trip set rather than the currently active
        # filter scope, since a newly picked trip may fall outside filters
        # left over from a previous turn in this thread.
        explicit_trip_id = state.selected_trip_hint or state.chosen_trip_id
        if explicit_trip_id:
            trip = next(
                (t for t in (state.all_trips or [])
                 if t['tripId'] == explicit_trip_id),
                None
            )
        if not trip and response.trip_id:
            # Same reasoning as selected_trip_hint above: an explicit trip id
            # parsed out of the message (e.g. a "[Trip: ...]" tag) is
            # unambiguous and shouldn't be constrained to the currently
            # active filter scope.
            trip = next(
                (t for t in (state.all_trips or [])
                 if t['tripId'] == response.trip_id
                 or response.trip_id in t['tripId']),
                None
            )
        if not trip and len(trips) == 1:
            trip = trips[0]
        if not trip:
            return {
                'chat_response': (
                    'Please click "Use trip" on a row, or pick one by typing @.'
                )
            }

        trip_start = datetime.fromisoformat(
            trip['startTimeUTC'].replace('Z', '+00:00')
        )
        trip_end = (
            datetime.fromisoformat(trip['endTimeUTC'].replace('Z', '+00:00'))
            if trip['endTimeUTC'] else trip_start
        )

        def parse_time(val, fallback):
            if not val:
                return fallback
            try:
                if 'T' in val or (len(val) >= 8 and '-' in val[:8]):
                    parsed = datetime.fromisoformat(val.replace('Z', '+00:00'))
                    return trip_start.replace(
                        hour=parsed.hour,
                        minute=parsed.minute,
                        second=parsed.second
                    )
                hh, mm = val.split(':')[:2]
                return trip_start.replace(hour=int(hh), minute=int(mm))
            except Exception:
                return fallback

        max_minutes = 3 if (response.dvr_type or 'clip') == 'clip' else 60
        clip_start_raw = parse_time(response.start_time, trip_start)
        clip_end_raw = parse_time(
            response.end_time,
            clip_start_raw + timedelta(minutes=max_minutes)
        )

        if clip_start_raw > trip_end or clip_end_raw < trip_start:
            return {
                'chat_response': (
                    f"That time is outside this trip's range "
                    f"({trip_start.strftime('%H:%M')}–{trip_end.strftime('%H:%M')}). "
                    f"Please give a time within the trip, or pick a different trip."
                ),
                'dvr_request_params': None,
                'selected_trip_hint': None,
                'chosen_trip_id': None
            }

        clip_start = max(trip_start, min(clip_start_raw, trip_end))
        clip_end = max(trip_start, min(clip_end_raw, trip_end))

        if clip_end <= clip_start:
            clip_start, clip_end = trip_start, trip_end

        requested_minutes = (clip_end - clip_start).total_seconds() / 60
        if requested_minutes > max_minutes:
            kind = (
                'DVR clip' if (response.dvr_type or 'clip') == 'clip'
                else 'timelapse'
            )
            return {
                'chat_response': (
                    f"That's about {round(requested_minutes)} minutes — "
                    f"a {kind} can be at most {max_minutes} minutes. "
                    f"Please give a shorter time window."
                ),
                'dvr_request_params': None,
                'selected_trip_hint': None,
                'chosen_trip_id': None
            }

        params = {
            'tripId': trip['tripId'],
            'driverId': trip['driverId'],
            'assetId': trip['assetId'],
            'type': response.dvr_type or 'clip',
            'clipStart': clip_start.isoformat(),
            'clipEnd': clip_end.isoformat()
        }
        d_logger.info(f'assembled DVR params: {params}')
        return {
            'dvr_request_params': params,
            'selected_trip_hint': None,
            'chosen_trip_id': None,
            # Clear any leftover result from a previously completed request in
            # this thread so the UI doesn't show trip A's summary/upload id
            # while trip B's request is still pending confirmation.
            'dvr_summary': None,
            'uploadRequestId': None,
            'dvr_confirmed': None
        }

    except GraphInterrupt:
        raise
    except DVRException:
        raise
    except Exception as e:
        d_logger.error(f'failed in extract_dvr_intent: {e}', exc_info=True)
        raise DVRException(
            "We had trouble processing your request. "
            "Could you try rephrasing it?"
        )

def route_after_intent(state: AgentState):
    if state.dvr_request_params:
        return 'confirm'
    if state.needs_refetch:
        return 'refetch'
    return 'loop'


# Graph Node - Confirming the gathered parameters to the user before hitting the endpoint
def confirm_dvr(state: AgentState):
    d_logger.info('Entering node: Confirm_DVR')
    d_logger.info('Confirming DVR parameters')
    params = state.dvr_request_params
    if not params:
        return {'chat_response': 'No DVR parameters to confirm.'}

    max_minutes = 3 if params.get('type') == 'clip' else 60

    response = interrupt({
        'message': 'confirm_dvr',
        'params': params,
        'maxDurationMinutes': max_minutes,
        'videoFormatOptions': [
            {'label': 'Road', 'value': 'road'},
            {'label': 'Driver', 'value': 'driver'},
            {'label': 'Side-by-side', 'value': 'sideBySide'},
            {'label': 'Picture-in-picture', 'value': 'pictureInPicture'},
            {'label': 'Road + Driver', 'value': 'separate'}
        ],
        'resolutionOptions': ['320x180', '640x360', '1280x720', '1920x1080']
    })

    if not response or not response.get('confirmed'):
        return {
            'dvr_confirmed': False,
            'chat_response': 'DVR request cancelled.',
            'dvr_request_params': None
        }

    updated = dict(params)
    updated['videoFormat'] = response.get('videoFormat', 'road')
    updated['videoResolution'] = response.get('videoResolution', '640x360')
    if response.get('durationMinutes'):
        start_dt = datetime.fromisoformat(params['clipStart'])
        updated['clipEnd'] = (
            start_dt + timedelta(minutes=response['durationMinutes'])
        ).isoformat()

    return {'dvr_confirmed': True, 'dvr_request_params': updated}


def route_confirm(state: AgentState):
    return 'submit' if state.dvr_confirmed else 'loop'


# Graph Node - To hit the DVR request endpoint after confirming the parameters
def submit_dvr_request(state: AgentState):
    d_logger.info('Entering node: Submit_DVR')
    d_logger.info('submitting DVR request')
    try:
        params = state.dvr_request_params
        if not params:
            raise DVRException(
                "We couldn't submit your video request. "
                "Please try selecting the trip again."
            )

        url = os.getenv('INTERNAL_API_BASE_URL')

        api_params = {
            'fleetId': state.fleet_id,
            'driverId': params.get('driverId'),
            'assetId': params.get('assetId'),
            'tripId': params.get('tripId'),
            'startTimeUTC': params.get('clipStart'),
            'endTimeUTC': params.get('clipEnd'),
            'dvrVideoType': params.get('videoFormat', 'road'),
            'videoResolution': params.get('videoResolution', '640x360')
        }

        api_url = f"{url}/fleets/{state.fleet_id}/dvr/create-upload-request"
        d_logger.info(
            f'DVR submit: type={params.get("type")}, params={api_params}'
        )

        dvr_summary = {
            'type': params.get('type'),
            'videoFormat': params.get('videoFormat'),
            'videoResolution': params.get('videoResolution'),
            'clipStart': params.get('clipStart'),
            'clipEnd': params.get('clipEnd')
        }
        mock_on_failure = os.getenv('DVR_MOCK_ON_FAILURE', 'false').lower() == 'true'

        try:
            dvr_response = requests.post(
                url=api_url, params=api_params, headers=get_headers()
            )
            d_logger.info(f'DVR response: {dvr_response.status_code}')

            if dvr_response.status_code != 200:
                raise DVRException.from_status_code(
                    dvr_response.status_code,
                    detail="DVR submission"
                )

            result = json.loads(dvr_response.text)
            return {
                'uploadRequestId': result.get('uploadRequestId'),
                'dvr_summary': dvr_summary
            }

        except (DVRException, requests.exceptions.RequestException) as e:
            if not mock_on_failure:
                if isinstance(e, requests.exceptions.Timeout):
                    d_logger.error('Timeout submitting DVR', exc_info=True)
                    raise DVRException.from_timeout()
                if isinstance(e, requests.exceptions.ConnectionError):
                    d_logger.error('Connection error submitting DVR', exc_info=True)
                    raise DVRException.from_connection_error()
                raise
            d_logger.warning(
                f'DVR submission failed ({e}); DVR_MOCK_ON_FAILURE is set, '
                'returning a fake approval instead.'
            )
            fake_id = f"DVR-DEMO-{random.randint(10000, 99999)}"
            return {
                'uploadRequestId': fake_id,
                'dvr_summary': dvr_summary
            }

    except GraphInterrupt:
        raise
    except DVRException:
        raise
    except Exception as e:
        d_logger.error(f'failed in submit_dvr_request: {e}', exc_info=True)
        raise DVRException(
            "Your video request couldn't be processed right now. "
            "Please try again in a moment."
        )
    

def create_graph():
    g = StateGraph(AgentState)

    g.add_node("Extract_Filters", extract_filters)
    g.add_node("Ask_Timestamp", ask_timestamp)
    g.add_node("Fetch_Trips", fetch_trips_with_expiry)
    g.add_node("Check_Trips", check_trips_node)
    g.add_node("Show_Results", show_results)
    g.add_node("Extract_DVR_Intent", extract_dvr_intent)
    g.add_node("Confirm_DVR", confirm_dvr)
    g.add_node("Submit_DVR", submit_dvr_request)

    g.add_conditional_edges(START, start_check, {
        'EXTRACT_DVR': "Extract_DVR_Intent",
        'EXTRACT_FILTERS': "Extract_Filters"
    })

    g.add_conditional_edges("Extract_Filters", check_timestamp, {
        'ask_timestamp': "Ask_Timestamp",
        'fetch_trips': "Fetch_Trips"
    })
    g.add_edge("Ask_Timestamp", "Fetch_Trips")
    g.add_edge("Fetch_Trips", "Show_Results")

    g.add_conditional_edges("Check_Trips", check_trips, {
        "fetch_again": 'Fetch_Trips',
        "show results": 'Show_Results'
    }) 
    
    g.add_conditional_edges("Show_Results", check_pagination, {
        'CONTINUE': "Extract_DVR_Intent",
        'FETCH': 'Fetch_Trips'
    })

    g.add_conditional_edges("Confirm_DVR", route_confirm, {
        'submit': "Submit_DVR",
        'loop': "Check_Trips"
    })
    g.add_conditional_edges("Extract_DVR_Intent", route_after_intent, {
        'confirm': "Confirm_DVR",
        'refetch': "Fetch_Trips",
        'loop': "Check_Trips"
    })

    g.add_edge("Submit_DVR", END)

    try:
        return g.compile(checkpointer=memory, name='DVR Video Request')

    except Exception as e:
        d_logger.error(e)
        raise
        
