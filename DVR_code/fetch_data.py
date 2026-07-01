import requests
import logging
from pydantic import BaseModel
import os
from dotenv import load_dotenv
load_dotenv()
import logging
from utils.auth import auth_manager

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)-8s | %(funcName)s | %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger('DVR_Graph')
class response_result(BaseModel):
    text : list | None = None
    status_code : int | None = None

    
def fetch_all_trips(url: str, base_params: dict, skip: int, control_number : int):
    auth_token, id_token = auth_manager._get_access_token()
    logging.info(f"auth_token : {auth_token}")
    headers = {
            'Authorization': f"Bearer {auth_token}",
            'id-token': id_token,
            'x-lm-desired-account': 'lmpresales'}
    
    all_trips = []
    limit = 40
    while skip< control_number:

        params1 = {
            **base_params,
            'key' : "startTimeUTC",
            'sort' : 'desc',
            'limit' : limit,
            'skip': skip
        }

        response = requests.get(url=url, params=params1, headers=headers)
        print(response.status_code)
        if response.status_code != 200:
            logger.error(f"API Error {response.status_code}: {response.text}")
            break
            
        try:
            data = response.json()
        except Exception as e:
            logger.error(f"Failed to parse JSON response: {e}")
            break

        rows = data.get('rows', [])
        
        if not rows:
            logger.info("No more rows returned from API. Ending pagination loop.")
            break
            
        # 2. Append the current batch of trips to our master list
        all_trips.extend(rows)
        
        # 3. Break if we received fewer rows than the requested limit
        if len(rows) < limit:
            logger.info(f"Received {len(rows)} rows, which is less than limit {limit}. Ending pagination loop.")
            break
            
        # 4. Increment skip to move to the next page
        skip += limit

    if len(all_trips)>0:
        status_code = 200
    else : 
        status_code = 400

    logger.info(f"Total trips fetched: {len(all_trips)}")
    return response_result(
        text=all_trips,
        status_code=status_code
    )


def fetch_all_drivers(url: str, base_params : dict, headers: dict, limit: int = 50, skip: int = 0):
    
    all_drivers = []
    
    while True:
        # Build params with lookups mirroring your trip query patterns
        params = {
            **base_params,
            'limit': limit,
            'skip': skip
        }

        logger.info(f"Fetching drivers with skip={skip}, limit={limit}'")
        try:
            response = requests.get(url=url, params=params, headers=headers)
        except Exception as e:
            logger.error(f"Network request to drivers endpoint failed: {e}")
            break
        
        if response.status_code != 200:
            logger.error(f"API Error {response.status_code}: {response.text}")
            break
            
        try:
            data = response.json()
        except Exception as e:
            logger.error(f"Failed to parse JSON driver response: {e}")
            break

        rows = data.get('rows', [])
        
        # ── CRITICAL FIX: Explicit termination checks ──
        # 1. Break if rows array is empty, None, or not structural
        if not rows:
            logger.info("No more driver records returned from API. Ending pagination loop.")
            break
            
        # 2. Extract and append structural elements 
        for match_driver in rows:
            all_drivers.append({
                'driverName': match_driver.get('driverName', 'UNASSIGNED'),
                'driverId': match_driver.get('driverId', 'UNASSIGNED')
            })
        
        # 3. Break if we received a partial tail page (fewer rows than limit requested)
        if len(rows) < limit:
            logger.info(f"Received {len(rows)} drivers, less than limit {limit}. Loop terminated.")
            break
            
        # 4. Advance offset page pointer
        skip += limit

        # Hard guardrail to match your 3*limit pagination boundary rule
        if skip > 3 * limit:
            logger.warning("Pagination hit depth guardrail safety breaker.")
            break

    if len(all_drivers) > 0:
        status_code = 200
    else:
        status_code = 400

    logger.info(f"Total structured drivers matched and fetched: {len(all_drivers)}")
    return response_result(
        text=all_drivers,
        status_code=status_code
    )