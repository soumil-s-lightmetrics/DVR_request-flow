from langgraph.graph import StateGraph, START, END
from langgraph.types import interrupt
from langgraph.errors import GraphInterrupt
from langgraph.checkpoint.memory import MemorySaver
from DVR_code.state import AgentState, timestamp, drivers_list
from langchain_openai import ChatOpenAI
from langchain.messages import HumanMessage, SystemMessage
from DVR_code.prompt import simple_query, merge_query, general_query, intent_query
from pydantic import BaseModel
from typing import Literal
from datetime import datetime, timedelta
from DVR_code.fetch_data import fetch_all_trips
from dotenv import load_dotenv
from DVR_code.helper_function import filter_enriched_trips, resolve_driver_matches, to_aware_utc, describe_active_filters
import requests
from utils.auth import auth_manager
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
llm_for_chat = ChatOpenAI(model='gpt-5.4-mini', api_key=os.getenv('OPENAI_API_KEY'))
llm_for_advance_reasoning = ChatOpenAI(model='gpt-5.4', api_key=os.getenv('OPENAI_API_KEY'))


# logger = debug_logger()

url = "https://api.lightmetrics.co/v2"
auth_token, id_token = auth_manager._get_access_token()
logging.info(f"auth_token : {auth_token}")
headers = {
            'Authorization': f"Bearer {auth_token}",
            'id-token': id_token,
            'x-lm-desired-account': 'lmpresales'}

class ExtractedFilters(BaseModel):
    driver_name: list[str] | None = []
    event_type: list[str] | None = []
    trip_id: str | None = None
    asset_id: list[str] | None = None
    start_time: str | None = None
    end_time: str | None = None
    limit_to_latest: int | None = None   # 1 for singular "latest trip", N for "last N trips", 30 for plain "latest/recent trips"
    events : Literal['max', 'min'] | int | None = None 


def extract_filters(state: AgentState):

    logger.info('extracting filters')
    try:
        structured_llm = llm_for_advance_reasoning.with_structured_output(ExtractedFilters)
        llm_response = structured_llm.invoke([
            HumanMessage(content=state.query),
            SystemMessage(content=simple_query.replace("{current_datetime}", datetime.now().isoformat()))
        ])
        logger.info(f'LLM extracted: {llm_response}')

        if llm_response.driver_name:
            url = "https://api.lightmetrics.co/v2"

            auth_token, id_token = auth_manager._get_access_token()
            
            headers = {
            'Authorization': f"Bearer {auth_token}",
            'id-token': id_token,
            'x-lm-desired-account': 'lmpresales'}

            request = requests.get(url = f"{url}/fleets/{state.fleet_id}/drivers/list?search={str(llm_response.driver_name).lower()}", headers=headers)
            data = request.json()
            logger.info(f"{state.fleet_id}, {llm_response.driver_name}")
            matching_drivers_list = resolve_driver_matches(state.fleet_id, llm_response.driver_name)
            
        else:
            matching_drivers_list=[]
        
        logger.info(f"driver info : {matching_drivers_list}")
        logger.info(llm_response.limit_to_latest)
        return {
            'chosen_driver': matching_drivers_list,
            'chosen_asset_id': llm_response.asset_id,
            'chosen_trip_id': llm_response.trip_id,
            'chosen_event': llm_response.event_type,
            'chosen_timestamp': timestamp(
                start_time=llm_response.start_time, end_time=llm_response.end_time
            ) if llm_response.start_time and llm_response.end_time else None,
            'limit_to_latest' : llm_response.limit_to_latest
        }
    except Exception as e:
        logger.error('failed in extract_filters', exc_info=True)
        return {}
    

def check_timestamp(state: AgentState):
    logger.info(state.limit_to_latest)
    ts = state.chosen_timestamp

    if state.limit_to_latest:
        return "fetch_trips"
    
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
        url = "https://api.lightmetrics.co/v2"

        auth_token, id_token = auth_manager._get_access_token()
        headers = {
            'Authorization': f"Bearer {auth_token}",
            'id-token': id_token,
            'x-lm-desired-account': 'lmpresales'
        }

        ## Filtering using start and end time
        if not state.limit_to_latest:
            start_date = datetime.fromisoformat(state.chosen_timestamp.start_time.replace("Z", "+00:00")).date()
            end_date = datetime.fromisoformat(state.chosen_timestamp.end_time.replace("Z", "+00:00")).date()

            api_before = end_date + timedelta(days=1)
            print(1)
            request_url = f"{url}/fleets/{state.fleet_id}/trips?before={api_before}&after={start_date}"
        else: 
            # when user types latest/last/recent we don't rely on dates but filtering by number
            # when user types latest trips we get the last 30, when latest trip only 1
            print(2)
            request_url = f"{url}/fleets/{state.fleet_id}/trips"

        ## Filtering using a list of driver ids
        driver_id_list=[]
        
        if state.limit_to_latest:
            logger.info('control number is there')
            control_number = state.limit_to_latest
        else: 
            logger.info('control number is not there')
            control_number = 120

        if state.chosen_driver:
            for driver in state.chosen_driver:
                driver_id_list.append(driver.get('driverId'))
            
            params={'driverId' : ','.join(driver_id_list)}
            
            response = fetch_all_trips(url=request_url, base_params=params, skip=0, control_number=control_number)
        
        else:
            params = {}
            
            response = fetch_all_trips(url=request_url, base_params=params, skip=0, control_number=control_number)
        
        
        ## Continuing only if status code 200
        if response.status_code != 200:
            return {'all_trips': [], 'chat_response': f'Failed to fetch trips (status {response.status_code}).'}
        
        ## rows contains all trips
        all_trips = response.text

        ## No query parameter for assets so manual filtering
        if state.chosen_asset_id:
            logger.info(state.chosen_asset_id)
            all_trips = [t for t in all_trips if t.get('asset').get('assetId') in state.chosen_asset_id]

        ## Local safety-net filter by driver — guarantees correctness even if the API's
        ## driverId param isn't actually being respected server-side
        if driver_id_list:
            all_trips = [t for t in all_trips if t.get('driverId') in driver_id_list]

        ## Calculating DVR expiry + filtering on the basis of the events by looping over trips 
        now = datetime.now().astimezone()
        enriched = []

        x=0
        logger.info(state.limit_to_latest)

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
        return {'all_trips': enriched, 'first_query': True }

    except GraphInterrupt:
        raise
    except Exception as e:
        logger.error('failed in fetch_trips_with_expiry', exc_info=True)
        return {'all_trips': [], 'chat_response' : {e}}

def check_trips(state : AgentState):
    logger.info('Checking trip length')
    if len(state.filter_trips or [])==0:
        return "fetch_again"
    
    return "show results" 


def check_trips_node(state : AgentState):
    return {'first_query' : False}


def show_results(state: AgentState):
    logger.info({'drivers' : state.chosen_driver, 'assets' : state.chosen_asset_id, 'timestamp' : state.chosen_timestamp})
    logger.info('Showing results')
 #Checking if we are to show the full trip list or the filtered list
    if state.first_query:
        trips = state.all_trips
    else:
        trips = state.filter_trips or []

    first_time = not state.results_shown

    interrupt_payload = {
        'message': 'show_results',
        'trips': trips[:state.limit_to_latest] if state.limit_to_latest else trips,
        'summary': f"" if len(trips)>0 else state.chat_response,
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

    # to display only the filter list of trips
    return updates

class ExtractedFilters1(BaseModel):
    driver_name: list[str] | None = []
    event_type: list[str] | None = []
    trip_id: str | None = None
    asset_id: list[str] | None = []
    start_time: str | None = None
    end_time: str | None = datetime.now()
    limit_to_latest: int | None = None   # 1 for singular "latest trip", N for "last N trips", 30 for plain "latest/recent trips"
    events : Literal['max', 'min'] | int | None = None  

def merge_filters_from_text(state: AgentState):
    try:
        active_filters_desc = describe_active_filters(state)

        structured_llm = llm_for_advance_reasoning.with_structured_output(ExtractedFilters1)

        extracted = structured_llm.invoke([
            HumanMessage(content=state.dvr_raw_text or ''),
            SystemMessage(content = f"Filters already active {active_filters_desc}"),
            SystemMessage(content=merge_query)
        ])

        logger.info(f'Filters already active : {active_filters_desc}\n narrow-down extraction: {extracted}')

        chosen_driver = state.chosen_driver
       
        chosen_driver = resolve_driver_matches(state.fleet_id, extracted.driver_name)
        
        chosen_asset_id = extracted.asset_id

        chosen_event = state.chosen_event or []
        
        if extracted.event_type:
            chosen_event = list(set(extracted.event_type))
        else: 
            chosen_event = []   

        chosen_timestamp = state.chosen_timestamp

        chosen_timestamp = timestamp(start_time=extracted.start_time, end_time=extracted.end_time)  # replace

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
            'limit_to_latest' : extracted.limit_to_latest
        }

        if filtered:
            event_filter = extracted.events
            logger.info(f"Event Count {event_filter}")
            
            if event_filter == 'max':
                max_count = max(trip['totalEvents'] for trip in filtered)
                filtered = [trip for trip in filtered if trip['totalEvents'] == max_count]
            
            elif event_filter == 'min':
                min_count = min(trip['totalEvents'] for trip in filtered)
                filtered = [trip for trip in filtered if trip['totalEvents'] == min_count]

            elif isinstance(event_filter, int):
                chosen_trip = []
                for trip in filtered:
                    if trip['totalEvents'] == event_filter:
                        chosen_trip.append(trip)
                filtered = chosen_trip

            base_updates.update({
                'filter_trips': filtered,
                'results_shown': False,
                'needs_refetch': False,
                'chat_response': f'Narrowed to {len(filtered)} trip{"s" if len(filtered) != 1 else ""}.'
            })

        else:
            # Nothing matches locally — the new filters reach outside what's already
            # fetched (e.g. a driver/date range never pulled from the API). Re-fetch.
            base_updates['needs_refetch'] = True
            base_updates['results_shown'] = False

        logger.info(state.chosen_driver)
        logger.info(base_updates['chosen_driver'])

        return base_updates

    except Exception as e:
        logger.error('failed in merge_filters_from_text', exc_info=True)
        return {'chat_response': 'Could not understand the filter request.'}


class DvrIntent(BaseModel):
    intent: Literal['general_question', 'show_trips', 'dvr_request']
    dvr_type: Literal['clip', 'timelapse'] | None = None
    trip_id: str | None = None
    start_time: str | None = None
    end_time: str | None = None


class refetch(BaseModel):
    refetch : bool | None = None


def extract_dvr_intent(state: AgentState):
    logger.info('extracting DVR intent from message')
    text = state.dvr_raw_text or ''

    if state.first_query:
        trips = state.all_trips or []
    else:
        trips = state.filter_trips

    try:
        if len(trips)>0:
            logger.info(json.dumps(trips[0]))
            trip_summary = json.dumps([
                {'tripId': t['tripId'], 'startTimeUTC': t['startTimeUTC'], 'endTimeUTC': t['endTimeUTC'],
                 'driverId': t['driverId'], 'assetId': t['assetId'],'events': t.get('eventCount', {})} for t in trips])
            
        else:
            trip_summary = 'No trips yet'

        active_filters_desc = describe_active_filters(state)

        system = f"""
        Trips currently shown to the user: {trip_summary}
        Filters currently active: {active_filters_desc}

        {intent_query}"""
        
        structured_llm = llm_for_advance_reasoning.with_structured_output(DvrIntent)
        response = structured_llm.invoke([HumanMessage(content=text), SystemMessage(content=system)])
        logger.info(f'intent: {response}')

### the parameters from the second query will be merged with the new parameters
        if response.intent == 'show_trips':
            logger.info('>>> BEFORE merge_filters_from_text call')
            updated_state = merge_filters_from_text(state)

            # updated_state_summary = describe_active_filters(updated_state)
            # current_state_summary = describe_active_filters(state)

            # state_summary = f"current_state : {current_state_summary}\n update_state : {updated_state_summary}" 

            # refetch_llm = llm_for_advance_reasoning.with_structured_output(refetch)
            # response = refetch_llm.invoke([HumanMessage(content=state_summary), SystemMessage(content=)])

            logger.info('>>> AFTER merge_filters_from_text call, about to log state')
            try:
                logger.info(f'>>> state.chosen_driver = {state.chosen_driver}')
            except Exception as log_err:
                logger.error(f'>>> logging state.chosen_driver FAILED: {log_err!r}')
            try:
                logger.info(f'>>> updated_state = {updated_state}')
            except Exception as log_err:
                logger.error(f'>>> logging updated_state FAILED: {log_err!r}')
            logger.info('>>> RETURNING updated_state now')
            return updated_state

### incase the user asks a general question about the trips
        if response.intent == 'general_question':
            prompt = f"trip_results: {trip_summary}\nuser_question: {text}"
            answer = llm_for_chat.invoke([HumanMessage(content=prompt), SystemMessage(content=general_query), SystemMessage(content=f'current date and time {datetime.now()}')]).content
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
        return {'chat_response': e}

def route_after_intent(state: AgentState):
    if state.dvr_request_params:
        return 'confirm'
    if state.needs_refetch:
        return 'refetch'
    return 'loop'

## Graph Node - Confirming the gathered parameters to the user before hitting the endpoint
def confirm_dvr(state: AgentState):
    logger.info('Confirming DVR parameters')
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


## Graph Node - To hit the DVR request endpoint after confirming the parameters from the user
def submit_dvr_request(state: AgentState):
    logger.info('submitting DVR request')
    try:
        params = state.dvr_request_params
        if not params:
            return {'chat_response': 'No DVR parameters provided.'}

        url = "https://api.lightmetrics.co/v2"

        auth_token, id_token = auth_manager._get_access_token()
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
    g.add_node("Check_Trips", check_trips_node)
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
    
    g.add_conditional_edges("Check_Trips", check_trips, {
        "fetch_again" : 'Fetch_Trips',
        "show results" : 'Show_Results'
    })

    g.add_edge("Show_Results", "Extract_DVR_Intent")
    
    g.add_conditional_edges("Confirm_DVR", route_confirm, {
        'submit': "Submit_DVR",
        'loop': "Check_Trips"
    })
    g.add_conditional_edges("Extract_DVR_Intent", route_after_intent, {
    'confirm': "Confirm_DVR",
    'refetch': "Fetch_Trips",
    'loop': "Check_Trips"})

    
    g.add_edge("Submit_DVR", END)

    return g.compile(checkpointer=memory, name='DVR Video Request')