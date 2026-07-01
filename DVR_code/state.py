from pydantic import BaseModel
from typing import Optional, Literal
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage, BaseMessage

class timestamp(BaseModel):
    start_time : str | None = None
    end_time : str | None = None

class location(BaseModel):
    firstLocation : dict | None = None
    lastLocation : dict | None = None

class drivers_list(BaseModel):
    driverName : str | None = None
    driverId : str| None = None

class AgentState(BaseModel):
    history : list[BaseMessage] | None = None

    fleet_id : str | None = None
    query : str | None= None
    query_type : Literal['directed', 'simple_query'] | None= None

    filter_or_not : bool | None= None
    
    drivers : list[drivers_list] | None = None
    asset_ids : list[str] | None = None
    trip_ids : list[str] | None = None
    events : list[str] | None = None

    fleet_id : str | None = None 
 
    autocomplete_search : str | None = None

    chosen_driver : list[dict] | None = None
    chosen_asset_id : list[str] | None = None
    chosen_trip_id : str | None = None
    chosen_event : list[str] | None = None
    chosen_events_count : Literal['max', 'min'] | int | None = None

    chosen_events_index : int | None = None

    chosen_timestamp : timestamp | None = None

    trip_dict : dict | None = None

    general_query_response : str | None = None
    wants_dvr : bool | None = None  

    uploadRequestId : str |None = None
    trip_results: list | None = None
    dvr_request_params: dict | None = None
    chat_response: str | None = None
    uploadRequestId: str | None = None
    dvr_raw_text: str | None = None
    results_shown: bool = False
    dvr_confirmed: bool | None = None

    is_dvr_request: bool | None = None
    selected_trip_hint: Optional[str] = None

    is_filter_request: bool = False
    filter_event_types: list[str] | None = None
    dvr_type: Literal['clip', 'timelapse'] | None = None
    trip_id: str | None = None
    start_time: str | None = None
    end_time: str | None = None
    needs_refetch: bool | None = False

    first_query : bool | None = False
    all_trips : list | None = []
    filter_trips : list | None = []
    limit_to_latest : int | None = None
    Node_Type : str | None = None