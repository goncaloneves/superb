"""
Fetch strike price from Polymarket 5-min crypto markets via Gamma API templateVariables.
"""
import requests
import json
from datetime import datetime, timezone

GAMMA_API = "https://gamma-api.polymarket.com"

# Step 1: Find active 5-min BTC markets
print("=" * 60)
print("Searching for active 5-min BTC markets on Gamma API...")
print("=" * 60)

# Search for active 5-minute Bitcoin markets
params = {
    "active": "true",
    "closed": "false",
    "limit": 10,
    "order": "startDate",
    "ascending": "false",
    "tag": "crypto",
}

# Try events endpoint first
print("\n[1] Fetching events with tag=crypto...")
resp = requests.get(f"{GAMMA_API}/events", params=params)
print(f"    Status: {resp.status_code}")

events = resp.json() if resp.status_code == 200 else []
btc_5min_events = []

for event in events:
    title = event.get("title", "")
    if "5" in title and ("minute" in title.lower() or "min" in title.lower()) and ("btc" in title.lower() or "bitcoin" in title.lower()):
        btc_5min_events.append(event)

print(f"    Found {len(btc_5min_events)} BTC 5-min events out of {len(events)} total crypto events")

# If no results with tag filter, try broader search
if not btc_5min_events:
    print("\n[2] Trying broader search with 'Bitcoin 5-minute'...")
    params2 = {
        "active": "true",
        "closed": "false",
        "limit": 20,
    }
    resp2 = requests.get(f"{GAMMA_API}/events", params=params2)
    print(f"    Status: {resp2.status_code}")
    events2 = resp2.json() if resp2.status_code == 200 else []

    for event in events2:
        title = event.get("title", "")
        if ("5" in title or "five" in title.lower()) and ("min" in title.lower()):
            btc_5min_events.append(event)

    print(f"    Found {len(btc_5min_events)} 5-min events out of {len(events2)} total events")

# Also try searching markets directly
print("\n[3] Searching markets directly for '5-minute Bitcoin'...")
market_params = {
    "active": "true",
    "closed": "false",
    "limit": 20,
}
resp3 = requests.get(f"{GAMMA_API}/markets", params=market_params)
print(f"    Status: {resp3.status_code}")
markets = resp3.json() if resp3.status_code == 200 else []

btc_5min_markets = []
for market in markets:
    q = market.get("question", "")
    desc = market.get("description", "")
    combined = f"{q} {desc}".lower()
    if "5" in combined and "min" in combined and ("btc" in combined or "bitcoin" in combined):
        btc_5min_markets.append(market)

print(f"    Found {len(btc_5min_markets)} BTC 5-min markets out of {len(markets)} total markets")

# Try text search
print("\n[4] Trying text search for 'Bitcoin 5-minute'...")
search_params = {
    "active": "true",
    "closed": "false",
    "limit": 10,
    "title": "Bitcoin 5-minute",
}
resp4 = requests.get(f"{GAMMA_API}/events", params=search_params)
print(f"    Status: {resp4.status_code}")
events4 = resp4.json() if resp4.status_code == 200 else []
print(f"    Found {len(events4)} events")

for event in events4:
    btc_5min_events.append(event)

# Deduplicate events
seen_ids = set()
unique_events = []
for e in btc_5min_events:
    eid = e.get("id")
    if eid not in seen_ids:
        seen_ids.add(eid)
        unique_events.append(e)
btc_5min_events = unique_events

# Step 2: Dump full details including templateVariables
print("\n" + "=" * 60)
print("RESULTS")
print("=" * 60)

if btc_5min_events:
    for event in btc_5min_events[:5]:
        print(f"\n--- Event: {event.get('title', 'N/A')} ---")
        print(f"  ID: {event.get('id')}")
        print(f"  Start: {event.get('startDate')}")
        print(f"  End: {event.get('endDate')}")

        # Check for templateVariables
        tv = event.get("templateVariables")
        if tv:
            print(f"  *** templateVariables: {json.dumps(tv, indent=4)}")
        else:
            print(f"  templateVariables: NOT PRESENT")

        # Check all fields for anything price-related
        for key, val in event.items():
            if key in ("title", "id", "startDate", "endDate", "templateVariables"):
                continue
            val_str = str(val).lower()
            if any(kw in val_str for kw in ["price", "strike", "beat", "open", "start"]):
                print(f"  ** {key}: {val}")

        # Check associated markets
        event_markets = event.get("markets", [])
        if event_markets:
            for m in event_markets[:3]:
                print(f"\n  Market: {m.get('question', 'N/A')}")
                print(f"    Market ID: {m.get('id')}")
                mtv = m.get("templateVariables")
                if mtv:
                    print(f"    *** Market templateVariables: {json.dumps(mtv, indent=4)}")
                # Dump description
                desc = m.get("description", "")
                if desc:
                    print(f"    Description: {desc[:300]}")
                # Check all market fields for price info
                for key, val in m.items():
                    if key in ("question", "id", "templateVariables", "description"):
                        continue
                    val_str = str(val).lower()
                    if any(kw in val_str for kw in ["price", "strike", "beat", "open", "start"]):
                        print(f"    ** {key}: {val}")

if btc_5min_markets:
    print("\n\n--- Direct Market Results ---")
    for m in btc_5min_markets[:5]:
        print(f"\n  Market: {m.get('question', 'N/A')}")
        print(f"    Market ID: {m.get('id')}")
        mtv = m.get("templateVariables")
        if mtv:
            print(f"    *** templateVariables: {json.dumps(mtv, indent=4)}")
        desc = m.get("description", "")
        if desc:
            print(f"    Description: {desc[:300]}")
        # Dump ALL fields to see what's available
        print(f"    All keys: {list(m.keys())}")

if not btc_5min_events and not btc_5min_markets:
    print("\nNo active 5-min BTC markets found. Dumping first 3 events for inspection:")
    all_events = events or events2 if 'events2' in dir() else events
    for event in (all_events or [])[:3]:
        print(f"\n  Title: {event.get('title')}")
        print(f"  Keys: {list(event.keys())}")
        tv = event.get("templateVariables")
        if tv:
            print(f"  templateVariables: {json.dumps(tv, indent=4)}")
        event_markets = event.get("markets", [])
        for m in event_markets[:2]:
            print(f"    Market Q: {m.get('question')}")
            print(f"    Market keys: {list(m.keys())}")
            mtv = m.get("templateVariables")
            if mtv:
                print(f"    Market templateVariables: {json.dumps(mtv, indent=4)}")

# Step 3: Also try fetching a specific known slug pattern
print("\n" + "=" * 60)
print("Trying known slug patterns...")
print("=" * 60)

slugs_to_try = [
    "bitcoin-5-minute",
    "btc-5-minute",
    "will-bitcoin-go-up-5-minute",
    "bitcoin-5-min",
]

for slug in slugs_to_try:
    resp = requests.get(f"{GAMMA_API}/events?slug={slug}")
    data = resp.json() if resp.status_code == 200 else []
    if data:
        print(f"\n  Slug '{slug}' returned {len(data)} events!")
        for event in data[:2]:
            print(f"    Title: {event.get('title')}")
            print(f"    Full event JSON (first 1000 chars):")
            print(f"    {json.dumps(event, indent=2)[:1000]}")
    else:
        print(f"  Slug '{slug}': no results")

print("\n\nDone.")
