#!/usr/bin/env python3
"""
Live frequency/volume monitor for an ESPHome web_server event stream.

The ESPHome web_server exposes a Server-Sent Events (SSE) endpoint at
http://<device>/events (not a WebSocket). This script connects to that
endpoint, authenticates with the configured web_server user/password, and
prints Current frequency / Current volume events in real time.
"""

import argparse
import csv
import json
import os
import signal
import sys
import time
from urllib.parse import urljoin

import requests


EVENTS_PATH = "/events"


def get_auth(base_url: str, username: str, password: str):
    """Probe whether the device wants Digest or Basic auth."""
    test_url = urljoin(base_url, "/sensor/Current%20frequency")
    for auth in (
        requests.auth.HTTPDigestAuth(username, password),
        requests.auth.HTTPBasicAuth(username, password),
    ):
        try:
            r = requests.get(test_url, auth=auth, timeout=10)
            if r.status_code == 200:
                return auth
        except requests.RequestException:
            continue
    raise RuntimeError(
        f"Could not authenticate to {test_url}. Check username/password and auth type."
    )


def parse_sse_line(line: str):
    """Return (field, value) for one SSE line, or (None, None)."""
    line = line.rstrip("\r\n")
    if not line or line.startswith(":"):
        return None, None
    if ":" not in line:
        return line, ""
    field, value = line.split(":", 1)
    return field, value.lstrip(" ")


def main():
    parser = argparse.ArgumentParser(
        description="Monitor ESPHome beep-detector SSE stream."
    )
    parser.add_argument("--host", default="http://192.168.1.135", help="Device base URL")
    parser.add_argument("--user", default=os.environ.get("ESPHOME_USER"), help="Username")
    parser.add_argument(
        "--password", default=os.environ.get("ESPHOME_PASS"), help="Password"
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=0,
        help="Stop after N seconds (0 = run until Ctrl-C)",
    )
    parser.add_argument(
        "--csv",
        default="beep_log.csv",
        help="Append timestamped samples to this CSV file (default: beep_log.csv)",
    )
    parser.add_argument(
        "--no-csv",
        dest="write_csv",
        action="store_false",
        default=True,
        help="Do not write a CSV file",
    )
    parser.add_argument(
        "--volume-threshold",
        type=float,
        default=0.0,
        help="Highlight rows where Current volume >= VALUE",
    )
    parser.add_argument(
        "--usv-threshold",
        type=float,
        default=0.0,
        help="Highlight rows where Current USV SNR >= VALUE",
    )
    parser.add_argument(
        "--freezer-threshold",
        type=float,
        default=0.0,
        help="Highlight rows where Current freezer SNR >= VALUE",
    )
    parser.add_argument(
        "--entities",
        default="sensor/Current frequency,sensor/Current volume,"
                "sensor/Current USV magnitude,sensor/Current USV SNR,"
                "sensor/Current freezer magnitude,sensor/Current freezer SNR,"
                "binary_sensor/Current USV beep,binary_sensor/Current freezer beep",
        help="Comma-separated list of entity IDs to watch",
    )
    args = parser.parse_args()

    if not args.user or not args.password:
        parser.error("--user and --password (or ESPHOME_USER/PASS env vars) are required")

    watched = {e.strip() for e in args.entities.split(",")}

    auth = get_auth(args.host, args.user, args.password)
    events_url = urljoin(args.host, EVENTS_PATH)

    csv_file = None
    csv_writer = None
    if args.write_csv:
        csv_file = open(args.csv, "a", newline="")
        csv_writer = csv.writer(csv_file)
        if os.path.getsize(args.csv) == 0:
            csv_writer.writerow([
                "timestamp", "entity_id", "volume", "frequency",
                "usv_magnitude", "usv_snr", "usv_beep",
                "freezer_magnitude", "freezer_snr", "freezer_beep",
            ])

    # Graceful Ctrl-C handling
    stop_event = {"stop": False}

    def on_signal(signum, frame):
        stop_event["stop"] = True

    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)

    print(f"Connecting to {events_url} ...", file=sys.stderr)
    start = time.time()
    latest = {}
    freq_count = 0
    vol_count = 0
    header_printed = False

    try:
        with requests.get(events_url, auth=auth, stream=True, timeout=30) as r:
            r.raise_for_status()
            print("Connected. Streaming events... (Ctrl-C to stop)", file=sys.stderr)

            for line in r.iter_lines(decode_unicode=True):
                if stop_event["stop"]:
                    break
                if args.duration and (time.time() - start) >= args.duration:
                    break
                if not line:
                    continue

                field, value = parse_sse_line(line)
                if field != "data":
                    continue

                try:
                    data = json.loads(value)
                except json.JSONDecodeError:
                    continue

                eid = data.get("id")
                if eid not in watched:
                    continue

                if eid == "sensor/Current frequency":
                    freq = float(data.get("value"))
                    now = time.time()
                    latest["freq"] = freq
                    freq_count += 1

                    if "vol" in latest:
                        vol = latest["vol"]
                        usv_mag = latest.get("sensor/Current USV magnitude", float('nan'))
                        usv_snr = latest.get("sensor/Current USV SNR", float('nan'))
                        freezer_mag = latest.get("sensor/Current freezer magnitude", float('nan'))
                        freezer_snr = latest.get("sensor/Current freezer SNR", float('nan'))
                        usv_beep = latest.get("binary_sensor/Current USV beep", float('nan'))
                        freezer_beep = latest.get("binary_sensor/Current freezer beep", float('nan'))

                        if not header_printed:
                            print("timestamp              volume   frequency       usv_mag/snr(b)    freezer_mag/snr(b)  markers")
                            header_printed = True

                        ts = time.strftime("%H:%M:%S.", time.localtime(now)) + f"{now % 1:.03f}"[2:]
                        marker = ""
                        if args.volume_threshold and vol >= args.volume_threshold:
                            marker += " *"
                        if args.usv_threshold and usv_snr >= args.usv_threshold:
                            marker += " USV!"
                        if args.freezer_threshold and freezer_snr >= args.freezer_threshold:
                            marker += " FREEZER!"
                        row = (
                            f"{ts}  volume={vol:8.4f}  frequency={freq:8.1f} Hz"
                            f"  usv={usv_mag:9.1f}/{usv_snr:7.2f}({usv_beep:.0f})"
                            f"  freezer={freezer_mag:9.1f}/{freezer_snr:7.2f}({freezer_beep:.0f}){marker}"
                        )
                        print(row)
                        if csv_writer:
                            csv_writer.writerow([
                                time.strftime("%Y-%m-%dT%H:%M:%S.", time.localtime(now)) + f"{now % 1:.06f}"[2:],
                                eid,
                                vol,
                                freq,
                                usv_mag,
                                usv_snr,
                                usv_beep,
                                freezer_mag,
                                freezer_snr,
                                freezer_beep,
                            ])
                            csv_file.flush()
                elif eid == "sensor/Current volume":
                    latest["vol"] = float(data.get("value"))
                    vol_count += 1
                else:
                    if eid.startswith("binary_sensor/"):
                        val = data.get("value", data.get("state"))
                        latest[eid] = 1.0 if val in (True, "ON", "on", "1", "true", "True") else 0.0
                    else:
                        try:
                            latest[eid] = float(data.get("value"))
                        except (ValueError, TypeError):
                            latest[eid] = data.get("value")

    except requests.RequestException as e:
        print(f"Connection error: {e}", file=sys.stderr)
        raise SystemExit(1)
    finally:
        if csv_file:
            csv_file.close()

    elapsed = time.time() - start
    print(
        f"\nDone. {freq_count} frequency events, {vol_count} volume events in {elapsed:.1f} s.",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
