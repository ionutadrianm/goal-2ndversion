from dotenv import load_dotenv
load_dotenv()

import requests
import time
import os
from datetime import datetime
import json
import logging
from logging.handlers import RotatingFileHandler
import csv

# =========================
# LOGGING
# =========================
logger = logging.getLogger()
logger.setLevel(logging.INFO)

file_handler = RotatingFileHandler("bot.log", maxBytes=5_000_000, backupCount=3)
console_handler = logging.StreamHandler()

formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")

file_handler.setFormatter(formatter)
console_handler.setFormatter(formatter)

logger.addHandler(file_handler)
logger.addHandler(console_handler)

logging.info("🔥 Bot started")

# =========================
# CONFIG
# =========================
API_KEY = os.getenv("API_FOOTBALL_KEY")
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

BASE_URL = "https://v3.football.api-sports.io"

HEADERS = {
    "x-apisports-key": API_KEY
}

seen_matches = {}
tracked_matches = {}
last_result_check = 0

SIGNALS_FILE = "signals.json"
TRACKED_FILE = "tracked.json"
RESULTS_CSV = "results.csv"

# =========================
# PERSISTENCE
# =========================
def save_signals():
    try:
        with open(SIGNALS_FILE, "w") as f:
            json.dump(seen_matches, f, default=str)
    except Exception as e:
        logging.error(f"Save signals error: {e}")

def load_signals():
    global seen_matches
    try:
        if os.path.exists(SIGNALS_FILE):
            with open(SIGNALS_FILE, "r") as f:
                data = json.load(f)

                for k, v in data.items():
                    v["time"] = datetime.fromisoformat(v["time"])

                seen_matches = data
                logging.info(f"📂 Loaded {len(seen_matches)} active signals")
    except Exception as e:
        logging.error(f"Load signals error: {e}")

def save_tracked():
    try:
        with open(TRACKED_FILE, "w") as f:
            json.dump(tracked_matches, f, default=str)
    except Exception as e:
        logging.error(f"Save tracked error: {e}")

def load_tracked():
    global tracked_matches
    try:
        if os.path.exists(TRACKED_FILE):
            with open(TRACKED_FILE, "r") as f:
                tracked_matches = json.load(f)
                logging.info(f"📂 Loaded {len(tracked_matches)} tracked matches")
    except Exception as e:
        logging.error(f"Load tracked error: {e}")

# =========================
# TELEGRAM
# =========================
def send_telegram(msg):
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        requests.post(url, data={"chat_id": CHAT_ID, "text": msg})
    except Exception as e:
        logging.error(f"Telegram error: {e}")

# =========================
# API
# =========================
def get_live_matches():
    try:
        r = requests.get(f"{BASE_URL}/fixtures?live=all", headers=HEADERS)
        data = r.json()
        return data.get("response", [])
    except Exception as e:
        logging.error(f"Live matches error: {e}")
        return []

# =========================
# SPLIT STATS
# =========================
def get_stats(fixture_id):
    try:
        r = requests.get(
            f"{BASE_URL}/fixtures/statistics?fixture={fixture_id}",
            headers=HEADERS
        )

        data = r.json().get("response", [])

        if len(data) < 2:
            return None

        stats = {
            "home_shots": 0,
            "away_shots": 0,

            "home_sot": 0,
            "away_sot": 0,

            "home_corners": 0,
            "away_corners": 0,
        }

        for idx, team in enumerate(data):

            side = "home" if idx == 0 else "away"

            for s in team.get("statistics", []):

                try:
                    val = int(s.get("value") or 0)
                except:
                    val = 0

                if s["type"] == "Total Shots":
                    stats[f"{side}_shots"] = val

                elif s["type"] == "Shots on Goal":
                    stats[f"{side}_sot"] = val

                elif s["type"] == "Corner Kicks":
                    stats[f"{side}_corners"] = val

        # TOTALS
        stats["shots"] = stats["home_shots"] + stats["away_shots"]
        stats["sot"] = stats["home_sot"] + stats["away_sot"]
        stats["corners"] = stats["home_corners"] + stats["away_corners"]

        # ACCURACY
        stats["home_accuracy"] = round(
            stats["home_sot"] / stats["home_shots"], 2
        ) if stats["home_shots"] > 0 else 0

        stats["away_accuracy"] = round(
            stats["away_sot"] / stats["away_shots"], 2
        ) if stats["away_shots"] > 0 else 0

        return stats

    except Exception as e:
        logging.error(f"Stats error: {e}")
        return None

# =========================
# LIVE ODDS
# =========================
def get_odds(fixture_id):
    try:
        r = requests.get(f"{BASE_URL}/odds?fixture={fixture_id}", headers=HEADERS)
        data = r.json().get("response", [])

        if not data:
            return None

        for book in data[0].get("bookmakers", []):
            for bet in book.get("bets", []):
                if bet["name"] == "Goals Over/Under":
                    return bet["values"]

        return None

    except Exception as e:
        logging.error(f"Odds error: {e}")
        return None

# =========================
# PREMATCH ODDS
# =========================
def get_prematch_odds(fixture_id):

    result = {
        "home_win_odds": None,
        "draw_odds": None,
        "away_win_odds": None,

        "prematch_over_1_5": None,
        "prematch_over_2_5": None,
        "prematch_over_3_5": None
    }

    try:
        r = requests.get(
            f"{BASE_URL}/odds?fixture={fixture_id}",
            headers=HEADERS
        )

        data = r.json().get("response", [])

        if not data:
            return result

        bookmakers = data[0].get("bookmakers", [])

        for book in bookmakers:

            for bet in book.get("bets", []):

                # 1X2
                if bet["name"] == "Match Winner":

                    for v in bet["values"]:

                        if v["value"] == "Home":
                            result["home_win_odds"] = float(v["odd"])

                        elif v["value"] == "Draw":
                            result["draw_odds"] = float(v["odd"])

                        elif v["value"] == "Away":
                            result["away_win_odds"] = float(v["odd"])

                # OVER/UNDER
                elif bet["name"] == "Goals Over/Under":

                    for v in bet["values"]:

                        try:
                            val = v["value"]

                            if val == "Over 1.5":
                                result["prematch_over_1_5"] = float(v["odd"])

                            elif val == "Over 2.5":
                                result["prematch_over_2_5"] = float(v["odd"])

                            elif val == "Over 3.5":
                                result["prematch_over_3_5"] = float(v["odd"])

                        except:
                            continue

        return result

    except Exception as e:
        logging.error(f"Prematch odds error: {e}")
        return result

# =========================
# LOGIC
# =========================
def classify(score):
    if score >= 85:
        return "🔥 ELITE"
    elif score >= 70:
        return "🔥 STRONG"
    else:
        return "⚡ MEDIUM"

def estimate_probability(stats, delta, minute):

    prob = 0.45

    if stats["shots"] >= 10:
        prob += 0.10

    if stats["sot"] >= 4:
        prob += 0.15

    if delta["shots"] >= 3:
        prob += 0.10

    if minute >= 60:
        prob += 0.05

    return min(prob, 0.85)

def prob_to_odds(prob):

    if prob == 0:
        return None

    return round(1 / prob, 2)

def get_target_odds(odds_data, total_goals):

    if not odds_data:
        return None

    target = float(total_goals) + 1.5

    for o in odds_data:

        try:
            val = o["value"].replace("Over ", "").strip()

            if abs(float(val) - target) < 0.01:
                return float(o["odd"])

        except:
            continue

    return None

def calculate_value(book_odds, fair_odds):

    try:
        return round(((book_odds / fair_odds) - 1) * 100, 2)

    except:
        return None

# =========================
# SAVE RESULTS
# =========================
def save_result_to_file(data):

    try:
        with open("results.json", "a") as f:
            f.write(json.dumps(data, default=str) + "\n")

    except Exception as e:
        logging.error(f"Save JSON error: {e}")

def save_result_to_csv(data):

    try:

        file_exists = os.path.isfile(RESULTS_CSV)

        with open(RESULTS_CSV, "a", newline="") as f:

            writer = csv.DictWriter(f, fieldnames=[

                "match",
                "result",

                "signal_tier",
                "signal_type",
                "signal_tags",

                "model_score",

                "book_odds",
                "fair_odds",
                "model_prob",
                "value",

                "home_win_odds",
                "draw_odds",
                "away_win_odds",

                "prematch_over_1_5",
                "prematch_over_2_5",
                "prematch_over_3_5",

                "track_score",
                "signal_score",
                "final_score",

                "track_minute",
                "signal_minute",
                "signal_time",

                "track_shots",
                "track_sot",
                "track_corners",

                "signal_shots",
                "signal_sot",
                "signal_corners",

                "home_shots",
                "away_shots",

                "home_sot",
                "away_sot",

                "home_corners",
                "away_corners",

                "home_accuracy",
                "away_accuracy",

                "delta_shots",
                "delta_sot",
                "delta_corners",

                "goals_at_signal"
            ])

            if not file_exists:
                writer.writeheader()

            signal_stats = data.get("signal_stats", {})

            writer.writerow({

                "match": data.get("match"),
                "result": data.get("result"),

                "signal_tier": data.get("signal_tier"),
                "signal_type": data.get("signal_type"),
                "signal_tags": ",".join(data.get("signal_tags", [])),

                "model_score": data.get("model_score"),

                "book_odds": data.get("book_odds"),
                "fair_odds": data.get("fair_odds"),
                "model_prob": data.get("model_prob"),
                "value": data.get("value"),

                "home_win_odds": data.get("home_win_odds"),
                "draw_odds": data.get("draw_odds"),
                "away_win_odds": data.get("away_win_odds"),

                "prematch_over_1_5": data.get("prematch_over_1_5"),
                "prematch_over_2_5": data.get("prematch_over_2_5"),
                "prematch_over_3_5": data.get("prematch_over_3_5"),

                "track_score": data.get("track_score"),
                "signal_score": data.get("signal_score"),
                "final_score": data.get("final_score"),

                "track_minute": data.get("track_minute"),
                "signal_minute": data.get("signal_minute"),
                "signal_time": data.get("signal_time"),

                "track_shots": data.get("track_stats", {}).get("shots"),
                "track_sot": data.get("track_stats", {}).get("sot"),
                "track_corners": data.get("track_stats", {}).get("corners"),

                "signal_shots": signal_stats.get("shots"),
                "signal_sot": signal_stats.get("sot"),
                "signal_corners": signal_stats.get("corners"),

                "home_shots": signal_stats.get("home_shots"),
                "away_shots": signal_stats.get("away_shots"),

                "home_sot": signal_stats.get("home_sot"),
                "away_sot": signal_stats.get("away_sot"),

                "home_corners": signal_stats.get("home_corners"),
                "away_corners": signal_stats.get("away_corners"),

                "home_accuracy": signal_stats.get("home_accuracy"),
                "away_accuracy": signal_stats.get("away_accuracy"),

                "delta_shots": data.get("delta", {}).get("shots"),
                "delta_sot": data.get("delta", {}).get("sot"),
                "delta_corners": data.get("delta", {}).get("corners"),

                "goals_at_signal": data.get("goals_at_signal")
            })

    except Exception as e:
        logging.error(f"CSV save error: {e}")

# =========================
# PERFORMANCE REPORT
# =========================
def generate_performance_report():

    try:

        if not os.path.exists("results.json"):
            return

        total = 0
        wins = 0

        with open("results.json", "r") as f:

            for line in f:

                try:

                    r = json.loads(line)

                    total += 1

                    if r["result"] == "✅ WIN":
                        wins += 1

                except:
                    continue

        if total == 0:
            return

        winrate = round((wins / total) * 100, 2)

        report = f"""
📊 PERFORMANCE REPORT

Total Signals: {total}
Winrate: {winrate}%
"""

        logging.info(report)

    except Exception as e:
        logging.error(f"Report error: {e}")

# =========================
# RESULT CHECKER
# =========================
def check_finished_matches():

    logging.info("📊 Checking results...")

    for match_id, data in list(seen_matches.items()):

        try:

            time_since = (datetime.now() - data["time"]).total_seconds()

            if time_since < 2400:
                continue

            r = requests.get(
                f"{BASE_URL}/fixtures?id={match_id}",
                headers=HEADERS
            )

            res = r.json().get("response", [])

            if not res:
                continue

            fixture = res[0]["fixture"]
            goals = res[0]["goals"]

            status = fixture["status"]["short"]

            if status not in ["FT", "AET", "PEN"]:
                continue

            final_home = goals["home"] or 0
            final_away = goals["away"] or 0

            initial_total = sum(
                map(int, data["initial_score"].split("-"))
            )

            final_total = final_home + final_away

            result = (
                "✅ WIN"
                if final_total >= initial_total + 2
                else "❌ LOSS"
            )

            result_data = data.copy()

            result_data["result"] = result
            result_data["final_score"] = f"{final_home}-{final_away}"

            save_result_to_file(result_data)
            save_result_to_csv(result_data)

            logging.info(
                f"📊 RESULT → {data['teams']} | "
                f"{result}"
            )

            del seen_matches[match_id]

            save_signals()

        except Exception as e:
            logging.error(f"Result error: {e}")

# =========================
# MAIN LOOP
# =========================
def run():

    global last_result_check

    logging.info("🚀 PRO SCANNER RUNNING")

    while True:

        try:

            logging.info("🔁 NEW SCAN")

            matches = get_live_matches()

            if not matches:
                time.sleep(60)
                continue

            logging.info(f"📊 Matches: {len(matches)}")

            for m in matches[:80]:

                try:

                    fixture = m["fixture"]
                    teams = m["teams"]
                    goals = m["goals"]

                    match_id = fixture["id"]

                    minute = fixture["status"]["elapsed"]

                    if not minute:
                        continue

                    if minute < 30 or minute > 70:
                        continue

                    home = teams["home"]["name"]
                    away = teams["away"]["name"]

                    home_goals = goals["home"] or 0
                    away_goals = goals["away"] or 0

                    total = home_goals + away_goals

                    if total >= 3:
                        continue

                    stats = get_stats(match_id)

                    if stats is None:
                        continue

                    # =========================
                    # TRACK
                    # =========================
                    if 35 <= minute <= 45:

                        if match_id not in tracked_matches:

                            logging.info(
                                f"TRACK CHECK → {home} vs {away} | "
                                f"shots:{stats['shots']} | "
                                f"sot:{stats['sot']} | "
                                f"corners:{stats['corners']}"
                            )

                            if stats["shots"] >= 5:

                                prematch = get_prematch_odds(match_id)

                                favorite = "NONE"

                                if prematch:
                                
                                    home_odds = prematch.get("home_win_odds")
                                    away_odds = prematch.get("away_win_odds")
                                
                                    if home_odds and away_odds:
                                
                                        if home_odds <= 1.80:
                                            favorite = "HOME"
                                
                                        elif away_odds <= 1.80:
                                            favorite = "AWAY"
                                
                                logging.info(
                                    f"FAVORITE → {favorite} | "
                                    f"home:{prematch.get('home_win_odds')} | "
                                    f"draw:{prematch.get('draw_odds')} | "
                                    f"away:{prematch.get('away_win_odds')}"
                                )

                                tracked_matches[match_id] = {

                                    "teams": f"{home} vs {away}",

                                    "track_minute": minute,
                                    "track_stats": stats,

                                    "score": f"{home_goals}-{away_goals}",

                                    "time": datetime.now(),

                                    "favorite": favorite,
                                    
                                    # PREMATCH
                                    "home_win_odds": prematch["home_win_odds"],
                                    "draw_odds": prematch["draw_odds"],
                                    "away_win_odds": prematch["away_win_odds"],

                                    "prematch_over_1_5": prematch["prematch_over_1_5"],
                                    "prematch_over_2_5": prematch["prematch_over_2_5"],
                                    "prematch_over_3_5": prematch["prematch_over_3_5"]
                                }

                                save_tracked()

                                logging.info(
                                    f"🧠 TRACKED → {home} vs {away}"
                                )

                    # =========================
                    # CONFIRM
                    # =========================
                    if 50 <= minute <= 60:

                        if match_id not in tracked_matches:
                            continue

                        if match_id in seen_matches:
                            continue

                        first = tracked_matches[match_id]

                        logging.info(
                            f"CONFIRM CHECK → {home} vs {away} | "
                            f"shots:{stats['shots']} | "
                            f"sot:{stats['sot']} | "
                            f"corners:{stats['corners']}"
                        )

                        # MOMENTUM
                        if stats["shots"] <= first["track_stats"]["shots"]:
                            continue

                        # SMALL FILTER 1
                        if stats["corners"] < 4:

                            logging.info(
                                f"SKIP LOW CORNERS → "
                                f"{home} vs {away}"
                            )

                            continue

                        # SMALL FILTER 2
                        accuracy = (
                            stats["sot"] / stats["shots"]
                            if stats["shots"] > 0 else 0
                        )

                        if stats["shots"] >= 12 and accuracy < 0.20:

                            logging.info(
                                f"SKIP FAKE PRESSURE → "
                                f"{home} vs {away} | "
                                f"accuracy:{round(accuracy,2)}"
                            )

                            continue

                        # ORIGINAL SOT FILTER
                        if stats["sot"] < 2:
                            continue

                        # =========================
                        # SCORING
                        # =========================
                        score = 40

                        if home_goals == away_goals:
                            score += 20

                        if stats["shots"] >= 12:
                            score += 15
                        elif stats["shots"] >= 9:
                            score += 10

                        if stats["sot"] >= 6:
                            score += 25
                        elif stats["sot"] >= 4:
                            score += 10

                        delta_shots = (
                            stats["shots"]
                            - first["track_stats"]["shots"]
                        )

                        if delta_shots >= 5:
                            score += 15
                        elif delta_shots >= 3:
                            score += 8

                        if stats["shots"] >= 12 and stats["sot"] <= 2:
                            score -= 15

                        tier = classify(score)

                        # =========================
                        # PRESSURE SPLIT
                        # =========================
                        home_pressure = (
                            stats["home_shots"] +
                            (stats["home_sot"] * 2) +
                            stats["home_corners"]
                        )
                        
                        away_pressure = (
                            stats["away_shots"] +
                            (stats["away_sot"] * 2) +
                            stats["away_corners"]
                        )
                        
                        total_pressure = home_pressure + away_pressure
                        
                        home_pressure_pct = round((home_pressure / total_pressure) * 100, 1) if total_pressure > 0 else 50
                        away_pressure_pct = round((away_pressure / total_pressure) * 100, 1) if total_pressure > 0 else 50
                        
                        logging.info(
                            f"PRESSURE SPLIT → "
                            f"{home}:{home_pressure_pct}% | "
                            f"{away}:{away_pressure_pct}%"
                        )
                        
                        signal_tags = []

                        # =========================
                        # WOUNDED FAVORITE
                        # =========================
                        if first.get("favorite") == "HOME" and home_goals <= away_goals:
                            signal_tags.append("WOUNDED_HOME_FAVORITE")
                        
                        if first.get("favorite") == "AWAY" and away_goals <= home_goals:
                            signal_tags.append("WOUNDED_AWAY_FAVORITE")
                        
                        # =========================
                        # DOMINANT PRESSURE
                        # =========================
                        if home_pressure_pct >= 70:
                            signal_tags.append("HOME_SIEGE")
                        
                        if away_pressure_pct >= 70:
                            signal_tags.append("AWAY_SIEGE")
                        
                        # =========================
                        # END TO END
                        # =========================
                        if (
                            stats["home_sot"] >= 2 and
                            stats["away_sot"] >= 2
                        ):
                            signal_tags.append("END_TO_END")
                        
                        # =========================
                        # CORNER PRESSURE
                        # =========================
                        if stats["corners"] >= 10:
                            signal_tags.append("HIGH_CORNERS")

                        logging.info(
                            f"SIGNAL TAGS → "
                            f"{', '.join(signal_tags) if signal_tags else 'NONE'}"
                        )
                        
                        # =========================
                        # ODDS
                        # =========================
                        odds_data = get_odds(match_id)

                        book_odds = get_target_odds(
                            odds_data,
                            total
                        )

                        delta = {
                            "shots": stats["shots"] - first["track_stats"]["shots"],
                            "sot": stats["sot"] - first["track_stats"]["sot"],
                            "corners": stats["corners"] - first["track_stats"]["corners"]
                        }

                        prob = estimate_probability(
                            stats,
                            delta,
                            minute
                        )

                        fair_odds = prob_to_odds(prob)

                        value = calculate_value(
                            book_odds,
                            fair_odds
                        ) if book_odds else None

                        if value is None or value < 2:

                            logging.info(
                                f"⛔ SKIPPED → {home} vs {away}\n"
                                f"book:{book_odds} | "
                                f"fair:{fair_odds} | "
                                f"value:{value}"
                            )

                            continue

                        # =========================
                        # TELEGRAM
                        # =========================
                        send_telegram(f"""
{tier} VALUE SIGNAL

{home} vs {away}

Min: {minute}'
Score: {home_goals}-{away_goals}

🎯 Market: Over {total + 1.5}

💰 Book Odds: {book_odds}
🧠 Fair Odds: {fair_odds}

📊 Model Prob: {round(prob*100)}%
🔥 Value: {value}%

⚽ Shots:
{stats['home_shots']} - {stats['away_shots']}

🎯 SOT:
{stats['home_sot']} - {stats['away_sot']}

🚩 Corners:
{stats['home_corners']} - {stats['away_corners']}

🏷 Tags:
{', '.join(signal_tags) if signal_tags else 'NONE'}
""")

                        # =========================
                        # SAVE SIGNAL
                        # =========================
                        seen_matches[match_id] = {

                            "time": datetime.now(),

                            "teams": f"{home} vs {away}",

                            "track_score": first["score"],
                            "signal_score": f"{home_goals}-{away_goals}",

                            "initial_score": f"{home_goals}-{away_goals}",

                            "track_minute": first["track_minute"],
                            "signal_minute": minute,

                            "signal_time": datetime.now().isoformat(),

                            "track_stats": first["track_stats"],
                            "signal_stats": stats,

                            "signal_tags": signal_tags,
                            
                            "delta": delta,

                            "model_score": score,

                            "signal_tier": tier,
                            "signal_type": "CONFIRMED",

                            "book_odds": book_odds,
                            "fair_odds": fair_odds,
                            "model_prob": prob,
                            "value": value,

                            # PREMATCH
                            "home_win_odds": first.get("home_win_odds"),
                            "draw_odds": first.get("draw_odds"),
                            "away_win_odds": first.get("away_win_odds"),

                            "prematch_over_1_5": first.get("prematch_over_1_5"),
                            "prematch_over_2_5": first.get("prematch_over_2_5"),
                            "prematch_over_3_5": first.get("prematch_over_3_5"),

                            "goals_at_signal": total
                        }

                        del tracked_matches[match_id]

                        save_tracked()
                        save_signals()

                except Exception as e:
                    logging.error(f"Match error: {e}")

            current_time = time.time()

            if (
                seen_matches
                and current_time - last_result_check > 1800
            ):

                check_finished_matches()

                generate_performance_report()

                last_result_check = current_time

            # CLEANUP
            now = datetime.now()

            for mid, t in list(tracked_matches.items()):

                try:

                    age = (
                        now - datetime.fromisoformat(t["time"])
                        if isinstance(t["time"], str)
                        else now - t["time"]
                    ).total_seconds()

                    if age > 3600:
                        del tracked_matches[mid]

                except:
                    del tracked_matches[mid]

            save_tracked()
            save_signals()

            time.sleep(300)

        except Exception as e:

            logging.error(f"LOOP ERROR: {e}")

            time.sleep(60)

# =========================
# START
# =========================
if __name__ == "__main__":

    load_signals()
    load_tracked()

    run()
