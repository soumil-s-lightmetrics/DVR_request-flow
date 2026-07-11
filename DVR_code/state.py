from pydantic import BaseModel
from typing import Optional, Literal


class Timestamp(BaseModel):
    start_time: str | None = None
    end_time: str | None = None


class drivers_list(BaseModel):
    driverName: str | None = None
    driverId: str | None = None


class DvrSummary(BaseModel):
    type: Literal['clip', 'timelapse'] | None = None
    videoFormat: str | None = None
    videoResolution: str | None = None
    clipStart: str | None = None
    clipEnd: str | None = None


class AgentState(BaseModel):
    fleet_id: str | None = None
    query: str | None = None
    query_type: Literal['directed', 'simple_query'] | None = None

    drivers: list[drivers_list] | None = None
    asset_ids: list[str] | None = None
    trip_ids: list[str] | None = None
    events: list[str] | None = None

    chosen_driver: list[dict] | None = None
    chosen_asset_id: list[str] | None = None
    chosen_trip_id: str | None = None
    chosen_event: list[str] | None = None
    chosen_events_count: Literal['max', 'min'] | int | None = None
    chosen_timestamp: Timestamp | None = None

    uploadRequestId: str | None = None
    dvr_request_params: dict | None = None
    dvr_summary: DvrSummary | None = None
    chat_response: str | None = None
    error: str | None = None
    user_response: str | None = None
    results_shown: bool = False
    dvr_confirmed: bool | None = None

    selected_trip_hint: Optional[str] = None
    needs_refetch: bool | None = False

    first_query: bool | None = False
    all_trips: list | None = []
    filter_trips: list | None = []
    limit_to_latest: int | None = None

    pagination_value: int | None = 0
