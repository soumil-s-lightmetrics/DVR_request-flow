"""
One-off test harness: drives the live DVR chat websocket (ws://localhost:8080/chat)
through a fixed list of example queries, one per fresh thread, auto-answering the
interrupts we know how to answer (date-range prompt, DVR confirmation), and records
a pass/fail verdict + short evidence line for each query into a JSON results file.

Not part of the application - a throwaway script for docs/EXAMPLE_QUERIES.md.
"""
import json
import time
import uuid
import requests
import websocket

WS_URL = "ws://localhost:8080/chat"
HTTP_BASE = "http://localhost:8080"
FLEET_ID = "acmetransport"

fleet_data_raw = requests.get(f"{HTTP_BASE}/{FLEET_ID}/load-data", timeout=30).json()
FLEET_DATA = {
    "fleet_id": FLEET_ID,
    "drivers": fleet_data_raw["drivers"],
    "asset_ids": fleet_data_raw["asset_ids"],
    "trip_ids": fleet_data_raw["trip_ids"],
    "events": fleet_data_raw["events"],
}

SECTIONS = {
    "fetch_by_date": [
        "Fetch trips for driver Daniel Taylor on 26th June",
        "Ask trip for Joseph White on 6th July",
        "Fetch all the trips on date 28th June",
        "Fetch trips for Michael Brown between 27th June and 29th June",
        "Show trips for asset003 between 20th June and 22nd June",
        "Get trips for driver Robert Lee on 25th June",
        "Show trips between 11th July and 13th July",
        "Fetch trips for asset007 on 8th July",
        "Give me trips for James Miller between 25th June and 27th June",
        "Show trips for driver Matthew Davis on 12th July",
        "Fetch trips for asset001 between 5th July and 7th July",
        "Get all trips from yesterday",
        "Show trips for driver David Wilson between 1st July and 3rd July",
        "Fetch trips for asset010 on 6th July",
        "Give trips for driver William Clark on 2nd July",
        "Show trips for driver John Smith on 27th June",
        "Fetch trips for asset002 between 26th June and 28th June",
        "Give trips for driver Michael Johnson on 30th June",
        "Show trips for asset006 on 8th July",
        "Fetch trips for driver Robert Lee between 6th July and 8th July",
        "Get trips for asset004 on 27th June",
        "Show trips for driver Joseph White between 10th July and 12th July",
        "Fetch trips on 5th July",
        "Give trips for driver Daniel Taylor between 24th June and 26th June",
        "Show trips for asset008 on 27th June",
        "Fetch trips for driver James Miller on 12th July",
    ],
    "fetch_without_date": [
        "Show trips for driver Robert Lee",
        "Give trips for driver Joseph White",
        "Fetch trips for asset004",
        "Show me trips for driver Michael Johnson",
        "Get trips for driver Daniel Taylor",
        "Fetch trips for asset006",
        "Show trips for driver Michael Brown",
        "Give me trips for asset008",
        "Show trips for driver James Miller",
        "Fetch trips for driver David Wilson",
        "Give trips for asset001",
        "Show trips for driver Matthew Davis",
        "Fetch trips for driver William Clark",
        "Give me trips for asset010",
        "Show trips for driver John Smith",
        "Fetch trips for asset002",
        "Give trips for driver Michael Johnson",
        "Show trips for asset007",
        "Fetch trips for driver Joseph White",
        "Give me trips for asset003",
        "Show trips for driver Robert Lee",
        "Fetch trips for asset009",
        "Give trips for driver James Miller",
        "Show trips for driver Daniel Taylor",
        "Fetch trips for asset005",
    ],
    "request_dvr": [
        ("Fetch trips for driver Robert Lee on 27th June", "Give me a DVR clip for the trip with the most events"),
        ("Fetch trips for driver Joseph White on 27th June", "Create a timelapse request for the trip with the fewest events"),
        ("Fetch trips for driver James Miller on 27th June", "Create DVR request between 13:38 and 13:40"),
        ("Fetch trips for driver Michael Brown on 27th June", "Give me a DVR clip at 14:20"),
        ("Fetch trips for driver David Wilson on 27th June", "Give me a timelapse from 9:00 to 9:15"),
        ("Fetch trips for driver Matthew Davis on 27th June", "DVR clip of trip by driver Matthew Davis 13:20"),
        ("Fetch trips for asset010 on 27th June", "Get me a clip for asset010 around 10:05"),
        ("Fetch trips for driver Daniel Taylor on 27th June", "Request a timelapse for the trip with the most events"),
        ("Fetch trips for driver William Clark on 27th June", "Give me a DVR clip for the trip with the fewest events"),
        ("Fetch trips for driver John Smith on 27th June", "Create a 2-minute clip at 15:04"),
        ("Fetch trips for driver Michael Johnson on 27th June", "Give a DVR clip from 15:20 to 15:22"),
        ("Fetch trips for driver Robert Lee on 26th June", "DVR clip of trip by driver Robert Lee 13:26"),
        ("Fetch trips for driver Joseph White on 26th June", "Create timelapse request for the trip with the most events"),
        ("Fetch trips for driver James Miller on 26th June", "Give me a DVR clip for the trip with the fewest events"),
        ("Fetch trips for driver Michael Brown on 26th June", "Request a DVR clip at 11:21"),
        ("Fetch trips for asset007 on 27th June", "Give me a timelapse for asset007 around 13:20"),
        ("Fetch trips for driver David Wilson on 26th June", "Create a DVR clip from 10:00 to 10:03"),
        ("Fetch trips for driver Matthew Davis on 26th June", "DVR clip of trip by driver Matthew Davis 13:20"),
        ("Fetch trips for driver Daniel Taylor on 26th June", "Give me a timelapse for the trip with the most events"),
        ("Fetch trips for driver William Clark on 26th June", "Request a DVR clip for the trip with the fewest events"),
        ("Fetch trips for driver John Smith on 26th June", "Create timelapse request between 15:04 and 15:10"),
        ("Fetch trips for driver Michael Johnson on 26th June", "Give a DVR clip at 15:20"),
        ("Fetch trips for asset001 on 27th June", "Give me a DVR clip for asset001 around 11:21"),
        ("Fetch trips for driver Robert Lee on 25th June", "Create a timelapse for the trip with the most events"),
        ("Fetch trips for driver Joseph White on 25th June", "Request a DVR clip for the trip with the fewest events"),
    ],
    "events_filter": [
        "Fetch trips for maximum events",
        "Give trips for cornering events",
        "Show me the trips where we have cornering and stop sign violations",
        "Fetch trips where there are more than 5 events",
        "Show trips with the fewest events",
        "Give me trips with harsh braking events",
        "Fetch trips where we have distracted driving events",
        "Show trips with at least 3 violations",
        "Give trips for driver Robert Lee with cornering events",
        "Fetch trips for asset004 with harsh braking and tailgating events",
        "Show trips with the most violations",
        "Give me trips with traffic speed violated events",
        "Fetch trips with more than 2 events",
        "Show trips for driver Joseph White with the most events",
        "Give trips with lane drift events",
        "Fetch trips where we have seatbelt violation events",
        "Show trips with distracted driving and cellphone events",
        "Give me the trip with the least events",
        "Fetch trips for asset007 with the most events",
        "Show trips with forward collision warning events",
        "Give trips with more than 8 events",
        "Fetch trips for driver James Miller with harsh acceleration events",
        "Show trips with tailgating events",
        "Give me trips with roll over detected events",
        "Fetch trips with drowsy driving events",
    ],
    "expected_errors": [
        "Show trips between 20th July and 22nd July",
        "Fetch trips for driver Joseph White on 30th July",
        "Give trips for asset003 tomorrow",
        "Show trips for driver Robert Lee next week",
        "Fetch trips between 1st July and 20th July",
        "Show trips for driver John Doe",
        "Fetch trips for asset999",
        "Show trips",
        "Give trips for the trip",
        "Give DVR clip",
        "Create timelapse request",
        ("Fetch trips for driver Michael Brown on 27th June", "Request a DVR clip for driver Michael Brown"),
        ("Fetch trips for driver Robert Lee on 27th June", "Give me a 10-minute DVR clip at 13:26"),
        ("Fetch trips for driver Robert Lee on 27th June", "Create a 90-minute timelapse at 13:26"),
        ("Fetch trips for driver Robert Lee on 27th June", "Give DVR clip between 23:50 and 23:55"),
        ("Fetch trips for driver Robert Lee on 27th June", "Create timelapse at 3:00 AM"),
        "Fetch trips for driver Nonexistent Person",
        "Show trips for asset123456",
        "Give trips between 25th July and 27th July",
        "Fetch trips for driver Joseph White on 29th July",
        "Show trips for asset003 on 21st July",
        "Fetch trips between 15th July and 17th July",
        "Give trips for driver Robert Lee next month",
        "Show trips for asset001 in two weeks",
        "Fetch trips for driver James Miller on 31st July",
    ],
}


def recv_json(ws, timeout=40):
    ws.settimeout(timeout)
    raw = ws.recv()
    return json.loads(raw)


def run_query(section, idx, query_spec):
    thread_id = f"test_{section}_{idx}_{uuid.uuid4().hex[:8]}"
    setup_query = None
    if isinstance(query_spec, tuple):
        setup_query, query_text = query_spec
    else:
        query_text = query_spec

    ws = websocket.create_connection(WS_URL, timeout=15)
    events = []
    verdict = "UNKNOWN"
    evidence = ""
    try:
        ws.send(json.dumps({"type": "load_data", "thread_id": thread_id, "fleet_data": FLEET_DATA}))
        recv_json(ws, timeout=20)  # load_complete

        def send_query(text):
            ws.send(json.dumps({
                "type": "only_query", "query": text,
                "thread_id": thread_id, "fleet_id": FLEET_ID
            }))

        if setup_query:
            send_query(setup_query)
            for _ in range(6):
                msg = recv_json(ws)
                events.append(msg)
                payload = msg.get("payload", {}) if isinstance(msg.get("payload"), dict) else {}
                if msg.get("type") == "interrupt" and payload.get("message") == "please provide timestamp":
                    ws.send(json.dumps({
                        "type": "resume_graph", "thread_id": thread_id,
                        "resume_value": {"start_time": "2026-06-27T00:00:00", "end_time": "2026-06-27T23:59:59"}
                    }))
                    continue
                if msg.get("type") == "interrupt" and payload.get("message") == "show_results":
                    break
                if msg.get("type") == "error":
                    return {"section": section, "query": f"[setup] {setup_query}", "verdict": "ERROR",
                            "evidence": msg.get("message", "")[:200]}
            last_payload = events[-1].get("payload", {}) if events else {}
            trips = last_payload.get("trips", [])
            if trips:
                tid = trips[0]["tripId"]
                query_text = f"[Trip: {tid}] {query_text}"

        send_query(query_text)

        for _ in range(8):
            msg = recv_json(ws)
            events.append(msg)
            mtype = msg.get("type")
            payload = msg.get("payload", {}) if isinstance(msg.get("payload"), dict) else {}
            response = msg.get("response", {}) if isinstance(msg.get("response"), dict) else {}

            if mtype == "error":
                verdict, evidence = "ERROR", msg.get("message", "")[:200]
                break

            if mtype == "interrupt":
                imsg = payload.get("message")
                if imsg == "please provide timestamp":
                    verdict, evidence = "PASS", "Prompted for date range as expected"
                    ws.send(json.dumps({
                        "type": "resume_graph", "thread_id": thread_id,
                        "resume_value": {"start_time": "2026-06-27T00:00:00", "end_time": "2026-06-27T23:59:59"}
                    }))
                    continue
                if imsg == "show_results":
                    trips = payload.get("trips", [])
                    verdict = "PASS"
                    evidence = f"{len(trips)} trip(s) shown; summary={payload.get('summary','')[:120]!r}"
                    break
                if imsg == "confirm_dvr":
                    ws.send(json.dumps({
                        "type": "resume_graph", "thread_id": thread_id,
                        "resume_value": {"confirmed": True, "videoFormat": "road", "videoResolution": "320x180"}
                    }))
                    continue
                verdict = "PASS"
                evidence = f"interrupt={imsg} payload_keys={list(payload.keys())}"
                break

            if mtype == "chat_response":
                if response.get("uploadRequestId"):
                    verdict = "PASS"
                    evidence = f"DVR submitted, uploadRequestId={response['uploadRequestId']}"
                else:
                    text = response.get("chat_response", "")
                    verdict = "PASS"
                    evidence = f"chat_response={text[:150]!r}"
                if not msg.get("more"):
                    break

        if verdict == "UNKNOWN":
            evidence = f"No terminal state reached; last events: {[e.get('type') for e in events[-3:]]}"

    except Exception as e:
        verdict, evidence = "EXCEPTION", f"{type(e).__name__}: {e}"
    finally:
        try:
            ws.close()
        except Exception:
            pass

    return {"section": section, "query": query_text, "verdict": verdict, "evidence": evidence}


def main():
    results = []
    total = sum(len(v) for v in SECTIONS.values())
    done = 0
    for section, queries in SECTIONS.items():
        for idx, q in enumerate(queries):
            r = run_query(section, idx, q)
            results.append(r)
            done += 1
            print(f"[{done}/{total}] {section} :: {r['verdict']} :: {r['query'][:60]}")
            with open("/tmp/dvr_test_results.json", "w") as f:
                json.dump(results, f, indent=2)
            time.sleep(0.5)

    print("DONE")


if __name__ == "__main__":
    main()
