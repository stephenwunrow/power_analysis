import os
import json
import numpy as np
import stravalib
from stravalib.client import Client
import time
import datetime
from stravalib.exc import RateLimitExceeded
import pytz
from dateutil import parser
from rich import print
from dotenv import load_dotenv
load_dotenv()

USER_TZ = pytz.timezone("US/Central")

# =============== CONFIG ===============

CLIENT_ID = os.getenv("CLIENT_ID")
CLIENT_SECRET = os.getenv("CLIENT_SECRET")
REDIRECT_URI = 'http://localhost'  # Your OAuth redirect URL

DATA_DIR = 'activities/strava_activities'
os.makedirs(DATA_DIR, exist_ok=True)

# =============== AUTHENTICATION ===============

def authenticate():
    if not os.path.exists("tokens.json"):
        raise RuntimeError(
            "tokens.json not found. Run one-time OAuth locally first."
        )

    client = Client()

    with open("tokens.json", "r") as f:
        tokens = json.load(f)

    client.access_token = tokens["access_token"]
    client.refresh_token = tokens["refresh_token"]
    client.expires_at = tokens["expires_at"]

    if client.expires_at < time.time():
        refresh_response = client.refresh_access_token(
            client_id=CLIENT_ID,
            client_secret=CLIENT_SECRET,
            refresh_token=client.refresh_token
        )
        client.access_token = refresh_response["access_token"]
        client.refresh_token = refresh_response["refresh_token"]
        client.expires_at = refresh_response["expires_at"]

        with open("tokens.json", "w") as f:
            json.dump(refresh_response, f)

    return client


# =============== DATA DOWNLOAD ===============

def download_and_save_power_streams(client, max_activities=1000):

    latest_date = datetime.datetime.min.replace(tzinfo=datetime.timezone.utc)

    # Step 1: Find the most recent start_date from saved JSON files
    downloaded_ids = set()
    for filename in os.listdir(DATA_DIR):
        if filename.endswith('.json'):
            try:
                with open(os.path.join(DATA_DIR, filename), 'r') as f:
                    data = json.load(f)
                    activity_id = int(filename.rstrip('.json'))
                    downloaded_ids.add(activity_id)

                    # Parse the saved start_date
                    start_date = parser.isoparse(data.get('start_date', ''))
                    if start_date.tzinfo is None:
                        start_date = start_date.replace(tzinfo=datetime.timezone.utc)
                    if start_date > latest_date:
                        latest_date = start_date
            except (ValueError, KeyError, json.JSONDecodeError):
                continue  # Skip unreadable files or missing fields

    print(f"📅 Latest downloaded activity date: {latest_date.isoformat()}")

    try:
        # Step 2: Query for newer activities only
        activities = client.get_activities(after=latest_date, limit=max_activities)

        count = 0
        for activity in activities:
            if activity.id in downloaded_ids:
                print(f"Skipping already downloaded activity {activity.id} ({activity.name})")
                continue

            count += 1
            print(f"Downloading activity {count}: {activity.name} ({activity.id})")

            # Get available streams
            all_streams = client.get_activity_streams(activity.id)
            available = all_streams.keys()
            if 'watts' not in available or 'time' not in available:
                print(f"⚠️ No power/time stream for activity {activity.id}")
                continue

            # Get actual data
            streams = client.get_activity_streams(activity.id, types=['watts', 'time'])
            power = streams.get('watts')
            time = streams.get('time')

            if power and time and power.data and time.data:
                data = {
                    'name': activity.name,
                    'id': activity.id,
                    'start_date': str(activity.start_date),
                    'power': power.data,
                    'time': time.data
                }
                filename = os.path.join(DATA_DIR, f"{activity.id}.json")
                with open(filename, 'w') as f:
                    json.dump(data, f)
                print(f"✅ Saved activity {activity.id}")
            else:
                print(f"⚠️ Data missing for activity {activity.id}")

    except RateLimitExceeded:
        print("🚫 Strava API rate limit exceeded. Stopping downloads.")
        return
    except Exception as e:
        print(f"❌ Unexpected error: {e}")


# =============== ANALYSIS CODE ===============

def load_power_streams(folder):
    data = []
    for filename in os.listdir(folder):
        if filename.endswith('.json'):
            with open(os.path.join(folder, filename), 'r') as f:
                activity = json.load(f)
                power_raw = activity.get('power')
                power = clean_power_data(power_raw)
                time_data = activity.get('time')
                start_date = activity.get('start_date')
                name = activity.get('name', filename)
                if power and time_data and len(power) >= 10:
                    data.append({
                        'power': power,
                        'time': time_data,
                        'date': start_date,
                        'name': name
                    })
    return data

def get_max_average_power(power_stream, interval_seconds):
    arr = np.array(power_stream)
    if len(arr) < interval_seconds:
        return None
    window = np.ones(interval_seconds) / interval_seconds
    averaged = np.convolve(arr, window, mode='valid')
    return np.max(averaged)

def analyze_power(folder, interval_seconds, top_n=5):
    activities = load_power_streams(folder)
    results = []

    for act in activities:
        max_avg_power = get_max_average_power(act['power'], interval_seconds)
        if max_avg_power:
            results.append({
                'name': act['name'],
                'date': act['date'],
                'max_power': round(max_avg_power, 1)
            })

    results.sort(key=lambda x: x['max_power'], reverse=True)
    return results[:top_n]

def chunk_message(text, max_len=1900):
    """
    Split text into chunks safe for Discord messages.
    """
    chunks = []
    while text:
        if len(text) <= max_len:
            chunks.append(text)
            break
        split_at = text.rfind("\n", 0, max_len)
        if split_at == -1:
            split_at = max_len
        chunks.append(text[:split_at])
        text = text[split_at:].lstrip()
    return chunks

def get_activities_for_date(folder, target_date):
    """
    Return a list of activity dicts for a given date.
    """
    activities = []

    for filename in os.listdir(folder):
        if not filename.endswith(".json"):
            continue

        path = os.path.join(folder, filename)
        try:
            with open(path) as f:
                act = json.load(f)

            dt = datetime.datetime.fromisoformat(act["start_date"])
            if dt.date() != target_date:
                continue

            activities.append({
                "filename": filename,
                "name": act.get("name", filename),
                "time": dt.strftime("%H:%M:%S"),
                "power": act.get("power", [])
            })
        except Exception:
            continue

    return activities

def get_activity_max_power(activity, interval_seconds):
    """
    Return rounded max average power for one activity, or None.
    """
    power = clean_power_data(activity.get("power", []))
    val = get_max_average_power(power, interval_seconds)

    if val is None:
        return None

    return round(val, 1)


# =============== MAIN ===============

def list_files_by_date(folder, target_date):
    """Return list of file info dicts with matching date in their data."""
    matching_files = []
    for filename in os.listdir(folder):
        if not filename.endswith('.json'):
            continue
        filepath = os.path.join(folder, filename)
        with open(filepath, 'r') as f:
            try:
                data = json.load(f)
                file_date = data.get('start_date', '')
                # Parse just the date portion (YYYY-MM-DD)
                date_only = file_date.split(" ")[0] if file_date else ''
                if date_only == target_date:
                    matching_files.append({
                        'filename': filename,
                        'name': data.get('name', 'Unknown'),
                        'start_date': file_date
                    })
            except Exception:
                continue
    return matching_files

def clean_power_data(power_list):
    # Replace None (null) with 0
    return [p if p is not None else 0 for p in power_list]