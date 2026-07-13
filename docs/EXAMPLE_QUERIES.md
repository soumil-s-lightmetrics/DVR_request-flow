# DVR Chat — Example Queries

Guidelines to follow:
- Keep the time frame to search for trips to **not more than 3 days**.
  Example: "show trips between 11th July - 13th July", to avoid overloading
  trips in memory.
- The flow is working properly with `fleet_id: 'acmetransport'`, so it will
  be loaded by default.

## To fetch trips by date

- Fetch trips for driver Daniel Taylor on 26th June
- Ask trip for Joseph White on 6th July
- Fetch all the trips on date 28th June
- Fetch trips for Michael Brown between 27th June and today
- Show trips for asset003 between 20th June and 24th June
- Get trips for driver Robert Lee on 13th July
- Show trips between 11th July and 13th July
- Fetch trips for asset007 on 8th July
- Give me trips for James Miller between 25th June and 27th June
- Show trips for driver Matthew Davis on 12th July
- Fetch trips for asset001 between 5th July and 7th July
- Get all trips from yesterday
- Show trips for driver David Wilson between 1st July and 3rd July
- Fetch trips for asset010 on 6th July
- Give trips for driver William Clark on 2nd July

## To fetch trips without date

- Show trips for driver Robert Lee
- Give trips for driver Joseph White
- Fetch trips for asset004
- Show me trips for driver Michael Johnson
- Get trips for driver Daniel Taylor
- Fetch trips for asset006
- Show trips for driver Michael Brown
- Give me trips for asset008

## Request DVR

- Give DVR clip for @trip (select trip)
- Create timelapse request for @trip (select trip)
- Create DVR request for @trip between 2:14 - 3:01
- Request a DVR clip for @trip at 14:20
- Give me a timelapse of @trip from 9:00 to 9:15
- DVR clip of trip by driver James Miller 13:38 - 13:40
- Get me a clip for asset010 around 10:05
- Request a timelapse for the trip with the most events
- Give me a DVR clip for the trip with the fewest events
- Create a 2-minute clip of @trip

## Events based filter

- Fetch trips for maximum events
- Give trips for cornering events
- Show me the trips where we have cornering and stop sign violations
- Fetch trips where there are more than 5 events
- Show trips with the fewest events
- Give me trips with harsh braking events
- Fetch trips where we have distracted driving events
- Show trips with at least 3 violations
- Give trips for driver Robert Lee with cornering events
- Fetch trips for asset004 with harsh braking and tailgating events

---

# Queries that are expected to error

These intentionally hit a guardrail already built into the flow, so the
assistant should respond with a clear error message instead of silently
returning wrong/empty results.

## Future date range (trips haven't happened yet)

- Show trips between 20th July and 22nd July *(dates ahead of today)*
- Fetch trips for driver Joseph White on 30th July
- Give trips for asset003 tomorrow
- Show trips for driver Robert Lee next week

## No trip selected before requesting DVR footage

- Give DVR clip *(sent with no trip selected via "Use trip" or @trip, and no
  driver/asset/time named in the message to disambiguate)*
- Create timelapse request *(no trip context at all)*
- Request a DVR clip for driver Michael Brown *(matches more than one trip,
  no time given to disambiguate)*

## DVR duration exceeds the allowed maximum

- Give me a 10-minute DVR clip of @trip *(clips are capped at 3 minutes)*
- Create a 90-minute timelapse of @trip *(timelapses are capped at 60 minutes)*
- Request a clip for @trip from 9:00 to 9:45

## Requested clip/time falls outside the trip's actual window

- Give DVR clip for @trip between 23:50 - 23:55 *(trip ended before this time)*
- Create timelapse for @trip at 3:00 AM *(trip started well after this time)*

## Driver/asset not recognized in the fleet

- Show trips for driver John Doe *(not a driver in acmetransport)*
- Fetch trips for asset999 *(not an asset in acmetransport)*

## Ambiguous request with no fleet context

- Show trips *(no driver, asset, date, or event filter given at all)*
- Give trips for the trip *(no identifying detail whatsoever)*
