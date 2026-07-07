import os
import requests
from pydantic import BaseModel
from dotenv import load_dotenv

from logger import debug_logger
from utils.auth import auth_manager

load_dotenv()
debug_logger = debug_logger()


def fetch_all_trips(
        url: str,
        base_params: dict,
        skip: int,
        control_number: int):

    all_trips = []
    limit = 50

    while skip < control_number:

        trip_params = {
            **base_params,
            'key': "startTimeUTC",
            'sort': 'desc',
            'limit': limit,
            'skip': skip
        }

        try:
            data = auth_manager.make_api_request(
                client_id = os.getenv('CLIENT_ID'),
                endpoint = url,
                params = trip_params
            )
        except Exception as e:
            debug_logger.error(f"Failed to parse JSON response: {e}")
            break

        trips = data.get('rows', [])

        if not trips:
            debug_logger.info(
                "No more rows returned from API. Ending pagination loop."
            )
            break

        all_trips.extend(trips)

        if len(trips) < limit:
            debug_logger.info(
                f"Received {len(trips)} rows, less than limit {limit}."
                " Ending pagination loop."
            )
            break

        skip += limit

    debug_logger.info(f"Total trips fetched: {len(all_trips)}")
    return all_trips


def fetch_all_drivers(
        url: str,
        base_params: dict,
        limit: int = 50,
        skip: int = 0):

    all_drivers = []

    while True:
        trip_params = {
            **base_params,
            'limit': limit,
            'skip': skip
        }

        debug_logger.info(f"Fetching drivers with skip={skip}, limit={limit}")

        try:
            data = auth_manager.make_api_request(
                client_id = os.getenv('CLIENT_ID'),
                endpoint = url,
                params = trip_params
            )
        except Exception as e:
            debug_logger.error(f"Failed to parse JSON driver response: {e}")
            break

        rows = data.get('rows', [])

        if not rows:
            debug_logger.info(
                "No more driver records returned. Ending pagination loop."
            )
            break

        for match_driver in rows:
            all_drivers.append({
                'driverName': match_driver.get('driverName', 'UNASSIGNED'),
                'driverId': match_driver.get('driverId', 'UNASSIGNED')
            })

        if len(rows) < limit:
            debug_logger.info(
                f"Received {len(rows)} drivers, less than limit {limit}."
                " Loop terminated."
            )
            break

        skip += limit

        if skip > 3 * limit:
            debug_logger.warning("Pagination hit depth guardrail safety breaker.")
            break

    debug_logger.info(
        f"Total structured drivers matched and fetched: {len(all_drivers)}"
    )
    return all_drivers
