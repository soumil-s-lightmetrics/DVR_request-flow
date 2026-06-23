from langgraph.graph import StateGraph, START, END
from langgraph.types import interrupt
from langgraph.errors import GraphInterrupt
from langgraph.checkpoint.memory import MemorySaver
from DVR_code.state import AgentState, timestamp, drivers_list
from langchain_openai import ChatOpenAI
from langchain.messages import HumanMessage, SystemMessage
from DVR_code.prompt import simple_query
from pydantic import BaseModel
from typing import Literal
from datetime import datetime, timedelta
from DVR_code.fetch_data import fetch_all_trips
from dotenv import load_dotenv
from DVR_code.helper_function import filter_enriched_trips, resolve_driver_matches, to_aware_utc
import requests
import json
import logging
load_dotenv()
import os

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)-8s | %(funcName)s | %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger('DVR_Graph')

memory = MemorySaver()
llm = ChatOpenAI(model='gpt-5.4-mini', api_key=os.getenv('OPENAI_API_KEY'))


class ExtractedFilters(BaseModel):
    driver_name: str | None = None
    event_type: list[str] | None = None
    trip_id: str | None = None
    asset_id: str | None = None
    start_time: str | None = None
    end_time: str | None = None

def extract_filters(state: AgentState):
    logger.info('extracting filters')
    try:
        structured_llm = llm.with_structured_output(ExtractedFilters)
        llm_response = structured_llm.invoke([
            HumanMessage(content=state.query),
            SystemMessage(content=simple_query)
        ])
        logger.info(f'LLM extracted: {llm_response}')

        if llm_response.driver_name:
            url = os.getenv('LM_API_URL')
            auth_token = os.getenv('LM_ACCESS_TOKEN')
            id_token = os.getenv('LM_ID_TOKEN')
            
            headers = {
            'Authorization': f"Bearer {auth_token}",
            'id-token': id_token,
            'x-lm-desired-account': 'lmpresales'}

            request = requests.get(url = f"{url}/fleets/{state.fleet_id}/drivers/list?search={str(llm_response.driver_name).lower()}", headers=headers)
            data = request.json()

            matching_drivers_list = []
            for match_driver in data.get('rows'):
                driverId = match_driver['driverId']
                driverName = match_driver['driverName']
                matching_drivers_list.append({'driverName' : driverName, 'driverId': driverId})
        else:
            matching_drivers_list=[]

        return {
            'chosen_driver': matching_drivers_list,
            'chosen_asset_id': llm_response.asset_id,
            'chosen_trip_id': llm_response.trip_id,
            'chosen_event': llm_response.event_type,
            'chosen_timestamp': timestamp(
                start_time=llm_response.start_time, end_time=llm_response.end_time
            ) if llm_response.start_time and llm_response.end_time else None
        }
    except Exception as e:
        logger.error('failed in extract_filters', exc_info=True)
        return {}
    

def check_timestamp(state: AgentState):
    ts = state.chosen_timestamp
    if not ts or not ts.start_time or not ts.end_time:
        return 'ask_timestamp'
    return 'fetch_trips'


def ask_timestamp(state: AgentState):
    logger.info('asking for search date range')
    ts_response = interrupt({'message': 'please provide timestamp'})
    try:
        return {
            'chosen_timestamp': timestamp(start_time=ts_response['start_time'], end_time=ts_response['end_time'])
        }
    except GraphInterrupt:
        raise
    except Exception as e:
        logger.error('failed in ask_timestamp', exc_info=True)
        return {}


def fetch_trips_with_expiry(state: AgentState):
    logger.info('fetching trips with expiry')
    try:
        url = os.getenv('LM_API_URL')
        auth_token = os.getenv('LM_ACCESS_TOKEN')
        id_token = os.getenv('LM_ID_TOKEN')
        headers = {
            'Authorization': f"Bearer {auth_token}",
            'id-token': id_token,
            'x-lm-desired-account': 'lmpresales'
        }

        ## Filtering using start and end time
        start_date = datetime.fromisoformat(state.chosen_timestamp.start_time.replace("Z", "+00:00")).date()
        end_date = datetime.fromisoformat(state.chosen_timestamp.end_time.replace("Z", "+00:00")).date()

        api_before = end_date + timedelta(days=1)

        request_url = f"{url}/fleets/{state.fleet_id}/trips?before={api_before}&after={start_date}"
        
        ## Filtering using a list of driver ids
        driver_id_list=[]
        params={}
        if state.chosen_driver:
            for driver in state.chosen_driver:
                driver_id_list.append(driver.get('driverId'))
            params={
                'driverId' : ','.join(driver_id_list),
                'limit' : 100
            }
            print(1)
            response = fetch_all_trips(url=request_url, base_params=params, headers=headers, limit=50, skip=0)
        else:
            print(2)
            params = {}
            response = fetch_all_trips(url=request_url, base_params=params, headers=headers, limit=50, skip=0)
        
        logger.info(f"{params}, {request_url}")
        
        logger.info(f'LM API: {response.status_code}, LM_response : {response.text}')

        ## Continuing only if status code 200
        if response.status_code != 200:
            return {'trip_results': [], 'chat_response': f'Failed to fetch trips (status {response.status_code}).'}
        
        ## rows contains all trips
        all_trips = response.text

        ## No query parameter for assets so manual filtering
        if state.chosen_asset_id:
            all_trips = [t for t in all_trips if t.get('asset_id') == state.chosen_asset_id]

        ## Local safety-net filter by driver — guarantees correctness even if the API's
        ## driverId param isn't actually being respected server-side
        if driver_id_list:
            all_trips = [t for t in all_trips if t.get('driverId') in driver_id_list]

        ## Calculating DVR expiry + filtering on the basis of the events by looping over trips 
        now = datetime.now().astimezone()
        enriched = []

        for trip in all_trips:
            ts = trip.get('startTimeUTC', '')
            te = trip.get('endTimeUTC', '')

            # if state.chosen_timestamp and ts:
            #     win_start = to_aware_utc(state.chosen_timestamp.start_time)
            #     win_end = to_aware_utc(state.chosen_timestamp.end_time)
            #     trip_start_dt = to_aware_utc(ts)
            #     trip_end_dt = to_aware_utc(te) if te else trip_start_dt
            #     if not (trip_start_dt <= win_end and trip_end_dt >= win_start):
            #         continue

            dvr_status, dvr_until, dvr_days = 'unknown', None, None

            if ts:
                trip_start = datetime.fromisoformat(ts.replace('Z', '+00:00'))
                expiry = trip_start + timedelta(days=17.5)
                remaining = (expiry - now).total_seconds() / 86400
                dvr_status = 'available' if remaining > 3 else (f"expiring" if remaining > 0 else 'expired')
                dvr_until = expiry.strftime('%d %b %Y %H:%M')
                dvr_days = round(remaining, 1)

            ## Filtering on the basis of events
            events = []
            events_without_count=[]
            for evt_type, count in trip.get('eventCount', {}).items():
                if evt_type!='total' and count>0:
                    events.append({'type': evt_type, 'count': count})
                    events_without_count.append(str(evt_type.lower()))

            chosen_event_list = [e.lower() for e in (state.chosen_event or [])]
            if chosen_event_list:
                result = set(chosen_event_list).issubset(set(events_without_count))
                if not result:
                    continue

            enriched.append({
                'tripId': trip.get('tripId'),
                'driverId': trip.get('driverId'),
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

        enriched.sort(key=lambda t: t.get('startTimeUTC', ''), reverse=True)
        logger.info(f'enriched {len(enriched)} trips')
        return {'trip_results': enriched}

    except GraphInterrupt:
        raise
    except Exception as e:
        logger.error('failed in fetch_trips_with_expiry', exc_info=True)
        return {'trip_results': []}


def show_results(state: AgentState):
    trips = state.trip_results or []
    first_time = not state.results_shown
    logger.info(f'show_results — first_time={first_time}')
    
    if len(trips)==0:
        return {'chat_reponse' : "No similar trips found, want to sea"}

    interrupt_payload = {
        'message': 'show_results',
        'trips': trips if first_time else [],
        'summary': f'Found {len(trips)} trip{"s" if len(trips) != 1 else ""}.' if first_time else '',
        'first': first_time
    }

    if first_time:
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
                } if state.chosen_timestamp else None}
        
    msg = interrupt(interrupt_payload)

    if isinstance(msg, dict):
        text = msg.get('text')
        trip_hint = msg.get('tripId')
        active_filters = msg.get('activeFilters')

    else:
        text = msg
        trip_hint = None
        active_filters = None

## dvr_raw_text is for llm to read the query whether a dvr request has been made by the user or not
    updates = {'dvr_raw_text': text, 'needs_refetch': False}
    
## trip_hint is the trip_id
    if trip_hint:
        updates['selected_trip_hint'] = trip_hint

    # Sync state to exactly what chips the frontend still shows — this is what
    # makes removed chips actually disappear from filtering, not just from the UI.
    if active_filters is not None:
        updates['chosen_driver'] = [active_filters['driver']] if active_filters.get('driver') else []
        updates['chosen_asset_id'] = active_filters.get('asset')
        updates['chosen_event'] = active_filters.get('events')
        updates['chosen_timestamp'] = timestamp(
            start_time=active_filters['date_range']['start'],
            end_time=active_filters['date_range']['end']
        ) if active_filters.get('date_range') else None

    if first_time:
        updates['results_shown'] = True
    return updates



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

class Merge_ExtractedFilters(BaseModel):
    driver_name: str | None = None
    event_type: list[str] | None = None
    trip_id: str | None = None
    asset_id: str | None = None
    start_time: str | None = None
    end_time: str | None = None
    filter_removed : dict | None = None


def merge_filters_from_text(state: AgentState):
    
    try:
        active_filters_desc = describe_active_filters(state)

        contextual_prompt = f"""
Filters already active on the current trip list: {active_filters_desc}

The user's message below may add a new filter, replace an existing one, or give a
partial detail that should be interpreted relative to what's already active — for
example, mentioning only a time of day when a date range is already active means
the time should be applied within that existing range, not treated as missing
information. Extract whatever new filtering detail is present, following the rules
below. Don't restate values that are already active unless the user is explicitly
changing them.

{simple_query}
"""
        structured_llm = llm.with_structured_output(ExtractedFilters)
        extracted = structured_llm.invoke([
            HumanMessage(content=state.dvr_raw_text or ''),
            SystemMessage(content=contextual_prompt),
            SystemMessage(content=simple_query)
        ])
        logger.info(f'narrow-down extraction: {extracted}')

        chosen_driver = state.chosen_driver
        if extracted.driver_name:
            chosen_driver = resolve_driver_matches(state.fleet_id, extracted.driver_name)  # replace

        chosen_asset_id = extracted.asset_id if extracted.asset_id else state.chosen_asset_id  # replace

        chosen_event = state.chosen_event or []
        if extracted.event_type:
            chosen_event = list(set((state.chosen_event or []) + extracted.event_type))  # union — cumulative narrowing

        chosen_timestamp = state.chosen_timestamp
        if extracted.start_time and extracted.end_time:
            chosen_timestamp = timestamp(start_time=extracted.start_time, end_time=extracted.end_time)  # replace

        driver_ids = [d['driverId'] for d in (chosen_driver or [])]
        filtered = filter_enriched_trips(
            state.trip_results or [],
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
            'dvr_request_params': None
        }

        if filtered:
            base_updates.update({
                'trip_results': filtered,
                'results_shown': False,
                'needs_refetch': False,
                'chat_response': f'Narrowed to {len(filtered)} trip{"s" if len(filtered) != 1 else ""}.'
            })
        else:
            # Nothing matches locally — the new filters reach outside what's already
            # fetched (e.g. a driver/date range never pulled from the API). Re-fetch.
            base_updates['needs_refetch'] = True
            base_updates['results_shown'] = False   

        return base_updates

    except Exception as e:
        logger.error('failed in merge_filters_from_text', exc_info=True)
        return {'chat_response': 'Could not understand the filter request.'}

class DvrIntent(BaseModel):
    intent: Literal['general_question', 'narrow_trips', 'dvr_request']
    dvr_type: Literal['clip', 'timelapse'] | None = None
    trip_id: str | None = None
    start_time: str | None = None
    end_time: str | None = None

def extract_dvr_intent(state: AgentState):
    logger.info('extracting DVR intent from message')
    text = state.dvr_raw_text or ''
    trips = state.trip_results or []

    try:
        logger.info(json.dumps(trips[0]))
        trip_summary = json.dumps([
            {'tripId': t['tripId'], 'startTimeUTC': t['startTimeUTC'], 'endTimeUTC': t['endTimeUTC'],
             'driverId': t['driverId'], 'assetId': t['assetId'],'events': t.get('eventCount', {})
} for t in trips
        ])
        active_filters_desc = describe_active_filters(state)

        system = f"""
Trips currently shown to the user: {trip_summary}
Filters currently active: {active_filters_desc}

Classify this message into exactly one of:

1. 'dvr_request' — the user wants DVR footage or a timelapse for a trip. Resolve trip_id,
   dvr_type, and start_time (end_time only if explicitly given — see time rules below).

2. 'narrow_trips' — the user is PROVIDING a detail that could be used to filter or narrow
   the trip list: a driver, asset, trip ID, event type, date, or time of day. This applies
   regardless of phrasing — a command ("show only...") and a statement ("an accident
   happened around 3 AM") are both narrow_trips if they supply new identifying or
   time/event information.

3. 'general_question' — the user is ASKING something ABOUT the trips already shown,
   without supplying any new filtering detail — e.g. "which trip had the most
   violations", "summarize the incidents", "how many trips are there".

Rule of thumb: informing something narrows; asking something questions.

Time extraction rules for dvr_request (apply only when intent is dvr_request):
- EXACT phrasing ("from X", "at X", "starting at X") → return X as start_time, with
  NO adjustment.
- APPROXIMATE/incident phrasing ("around X", "near X", "an accident occurred at X")
  → subtract 2 minutes 30 seconds from X and return that as start_time.
- Only set end_time if the user explicitly gives a second time ("from X to Y",
  "between X and Y"). Otherwise leave end_time unset — duration is chosen separately
  via a dropdown, not extracted here.
- Return times as HH:MM (24-hour) unless a specific date is mentioned, in which case
  return full ISO 8601.
"""
        structured_llm = llm.with_structured_output(DvrIntent)
        response = structured_llm.invoke([HumanMessage(content=text), SystemMessage(content=system)])
        logger.info(f'intent: {response}')


        if response.intent == 'narrow_trips':
            return merge_filters_from_text(state)

        if response.intent == 'general_question':
            prompt = f"trip_results: {trip_summary}\nuser_question: {text}"
            answer = llm.invoke([HumanMessage(content=prompt)]).content
            return {'chat_response': answer, 'dvr_request_params': None, 'selected_trip_hint': None}

        # intent == 'dvr_request' — unchanged from here down
        trip = None
        if state.selected_trip_hint:
            trip = next((t for t in trips if t['tripId'] == state.selected_trip_hint), None)
        if not trip and response.trip_id:
            trip = next((t for t in trips if t['tripId'] == response.trip_id or response.trip_id in t['tripId']), None)
        if not trip and len(trips) == 1:
            trip = trips[0]
        if not trip:
            return {'chat_response': 'Please click "Use trip" on a row, or pick one by typing @.'}

        trip_start = datetime.fromisoformat(trip['startTimeUTC'].replace('Z', '+00:00'))
        trip_end = datetime.fromisoformat(trip['endTimeUTC'].replace('Z', '+00:00')) if trip['endTimeUTC'] else trip_start

        def parse_time(val, fallback):
            if not val:
                return fallback
            try:
                if 'T' in val or (len(val) >= 8 and '-' in val[:8]):
                    parsed = datetime.fromisoformat(val.replace('Z', '+00:00'))
        
                    return trip_start.replace(hour=parsed.hour, minute=parsed.minute, second=parsed.second)
                hh, mm = val.split(':')[:2]  # guards against "09:30:00" (3 parts)
                return trip_start.replace(hour=int(hh), minute=int(mm))
            except Exception:
                return fallback
        max_minutes = 3 if (response.dvr_type or 'clip') == 'clip' else 60

        clip_start_raw = parse_time(response.start_time, trip_start)

        clip_end_raw = parse_time(response.end_time, clip_start_raw + timedelta(minutes=max_minutes))
        if clip_start_raw > trip_end or clip_end_raw < trip_start:
            return {
                'chat_response': (
                    f"That time is outside this trip's range "
                    f"({trip_start.strftime('%H:%M')}–{trip_end.strftime('%H:%M')}). "
                    f"Please give a time within the trip, or pick a different trip."),
                'dvr_request_params': None,
                'selected_trip_hint': None}


        # Clamp to trip bounds — clip can never exceed the trip's actual start/end
        clip_start = max(trip_start, min(clip_start_raw, trip_end))

        clip_end = max(trip_start, min(clip_end_raw, trip_end))

        if clip_end <= clip_start:
            clip_start, clip_end = trip_start, trip_end

        requested_minutes = (clip_end - clip_start).total_seconds() / 60
        if requested_minutes > max_minutes:
            kind = 'DVR clip' if (response.dvr_type or 'clip') == 'clip' else 'timelapse'
            return {
                'chat_response': f"That's about {round(requested_minutes)} minutes — a {kind} can be at most {max_minutes} minutes. Please give a shorter time window.",
                'dvr_request_params': None,
                'selected_trip_hint': None
            }
        
        params = {
            'tripId': trip['tripId'],
            'driverId': trip['driverId'],
            'assetId': trip['assetId'],
            'type': response.dvr_type or 'clip',
            'clipStart': clip_start.isoformat(),
            'clipEnd': clip_end.isoformat()
        }
        logger.info(f'assembled DVR params: {params}')
        return {'dvr_request_params': params, 'selected_trip_hint': None}

    except Exception as e:
        logger.error('failed in extract_dvr_intent', exc_info=True)
        return {'chat_response': 'Could not understand the request. Please try rephrasing.'}


def route_after_intent(state: AgentState):
    if state.dvr_request_params:
        return 'confirm'
    if state.needs_refetch:
        return 'refetch'
    return 'loop'


def confirm_dvr(state: AgentState):
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
        return {'dvr_confirmed': False, 'chat_response': 'DVR request cancelled.', 'dvr_request_params': None}

    updated = dict(params)
    updated['videoFormat'] = response.get('videoFormat', 'road')
    updated['videoResolution'] = response.get('videoResolution', '640x360')
    if response.get('durationMinutes'):
        start_dt = datetime.fromisoformat(params['clipStart'])
        updated['clipEnd'] = (start_dt + timedelta(minutes=response['durationMinutes'])).isoformat()

    return {'dvr_confirmed': True, 'dvr_request_params': updated}

def route_confirm(state: AgentState):
    return 'submit' if state.dvr_confirmed else 'loop'


# ── Node: Submit DVR request to LM API ──
def submit_dvr_request(state: AgentState):
    logger.info('submitting DVR request')
    try:
        params = state.dvr_request_params
        if not params:
            return {'chat_response': 'No DVR parameters provided.'}

        url = os.getenv('LM_API_URL')
        auth_token = os.getenv('LM_ACCESS_TOKEN')
        id_token = os.getenv('LM_ID_TOKEN')
        headers = {
            'Authorization': f"Bearer {auth_token}",
            'id-token': id_token,
            'x-lm-desired-account': 'lmpresales'
        }

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
        logger.info(f'DVR submit: type={params.get("type")}, params={api_params}')

        dvr_response = requests.post(url=api_url, params=api_params, headers=headers)
        logger.info(f'DVR response: {dvr_response.status_code}')

        if dvr_response.status_code == 200:
            result = json.loads(dvr_response.text)
            return {
                'uploadRequestId': result.get('uploadRequestId', 'unknown'),
                'dvr_summary': {
                    'type': params.get('type'),
                    'videoFormat': params.get('videoFormat'),
                    'videoResolution': params.get('videoResolution'),
                    'clipStart': params.get('clipStart'),
                    'clipEnd': params.get('clipEnd')
                }
            }
        else:
            return {'chat_response': f'DVR request failed (status {dvr_response.status_code}).'}

    except Exception as e:
        logger.error('failed in submit_dvr_request', exc_info=True)
        return {'chat_response': 'Something went wrong submitting the DVR request.'}
    
    

def create_graph():
    g = StateGraph(AgentState)

    g.add_node("Extract_Filters", extract_filters)
    g.add_node("Ask_Timestamp", ask_timestamp)
    g.add_node("Fetch_Trips", fetch_trips_with_expiry)
    g.add_node("Show_Results", show_results)
    g.add_node("Extract_DVR_Intent", extract_dvr_intent)
    g.add_node("Confirm_DVR", confirm_dvr)
    g.add_node("Submit_DVR", submit_dvr_request)

    g.add_edge(START, "Extract_Filters")
    g.add_conditional_edges("Extract_Filters", check_timestamp, {
        'ask_timestamp': "Ask_Timestamp",
        'fetch_trips': "Fetch_Trips"
    })
    g.add_edge("Ask_Timestamp", "Fetch_Trips")
    g.add_edge("Fetch_Trips", "Show_Results")
    g.add_edge("Show_Results", "Extract_DVR_Intent")
    
    g.add_conditional_edges("Confirm_DVR", route_confirm, {
        'submit': "Submit_DVR",
        'loop': "Show_Results"
    })
    g.add_conditional_edges("Extract_DVR_Intent", route_after_intent, {
    'confirm': "Confirm_DVR",
    'refetch': "Fetch_Trips",
    'loop': "Show_Results"})
    
    g.add_edge("Submit_DVR", END)

    return g.compile(checkpointer=memory, name='DVR Video Request')