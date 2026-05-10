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

# MOdificat
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

logging.info("🔥 Bot started - Phase 1 & 2 Active")

# =========================
# CONFIG
# =========================
API_KEY = os.getenv("API_FOOTBALL_KEY")
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

BASE_URL = "https://v3.football.api-sports.io"
HEADERS = {"x-apisports-key": API_KEY}

seen_matches = {}
tracked_matches = {}
last_result_check = 0

SIGNALS_FILE = "signals.json"
TRACKED_FILE = "tracked.json"
RESULTS_CSV = "results_v2.csv"

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
# API HELPERS
# =========================
def get_live_matches():
    try:
        r = requests.get(f"{BASE_URL}/fixtures?live=all", headers=HEADERS)
        return r.json().get("response", [])
    except Exception as e:
        logging.error(f"Live matches error: {e}")
        return []

def get_stats(fixture_id):
    try:
        r = requests.get(f"{BASE_URL}/fixtures/statistics?fixture={fixture_id}", headers=HEADERS)
        data = r.json().get("response", [])
        if len(data) < 2: return None

        stats = {
            "home_shots": 0, "away_shots": 0,
            "home_sot": 0, "away_sot": 0,
            "home_corners": 0, "away_corners": 0,
        }

        for idx, team in enumerate(data):
            side = "home" if idx == 0 else "away"
            for s in team.get("statistics", []):
                try: val = int(s.get("value") or 0)
                except: val = 0
                if s["type"] == "Total Shots": stats[f"{side}_shots"] = val
                elif s["type"] == "Shots on Goal": stats[f"{side}_sot"] = val
                elif s["type"] == "Corner Kicks": stats[f"{side}_corners"] = val

        stats["shots"] = stats["home_shots"] + stats["away_shots"]
        stats["sot"] = stats["home_sot"] + stats["away_sot"]
        stats["corners"] = stats["home_corners"] + stats["away_corners"]
        stats["home_accuracy"] = round(stats["home_sot"] / stats["home_shots"], 2) if stats["home_shots"] > 0 else 0
        stats["away_accuracy"] = round(stats["away_sot"] / stats["away_shots"], 2) if stats["away_shots"] > 0 else 0
        return stats
    except Exception as e:
        logging.error(f"Stats error: {e}")
        return None

def get_odds(fixture_id):
    try:
        r = requests.get(f"{BASE_URL}/odds?fixture={fixture_id}", headers=HEADERS)
        data = r.json().get("response", [])
        if not data: return None
        for book in data[0].get("bookmakers", []):
            for bet in book.get("bets", []):
                if bet["name"] == "Goals Over/Under":
                    return bet["values"]
        return None
    except Exception as e:
        logging.error(f"Odds error: {e}")
        return None

def get_prematch_odds(fixture_id):
    result = {
        "home_win_odds": None, "draw_odds": None, "away_win_odds": None,
        "prematch_over_1_5": None, "prematch_over_2_5": None, "prematch_over_3_5": None
    }
    try:
        r = requests.get(f"{BASE_URL}/odds?fixture={fixture_id}", headers=HEADERS)
        data = r.json().get("response", [])
        if not data: return result
        bookmakers = data[0].get("bookmakers", [])
        for book in bookmakers:
            for bet in book.get("bets", []):
                if bet["name"] == "Match Winner":
                    for v in bet["values"]:
                        if v["value"] == "Home": result["home_win_odds"] = float(v["odd"])
                        elif v["value"] == "Draw": result["draw_odds"] = float(v["odd"])
                        elif v["value"] == "Away": result["away_win_odds"] = float(v["odd"])
                elif bet["name"] == "Over/Under":
                    for v in bet["values"]:
                        if v["value"] == "Over 1.5": result["prematch_over_1_5"] = float(v["odd"])
                        elif v["value"] == "Over 2.5": result["prematch_over_2_5"] = float(v["odd"])
                        elif v["value"] == "Over 3.5": result["prematch_over_3_5"] = float(v["odd"])
        return result
    except Exception as e:
        logging.error(f"Prematch odds error: {e}")
        return result

# =========================
# HARD FILTERS (QUALITY CONTROL)
# =========================
def is_high_quality_signal(stats, prematch):
    """
    Eliminate low probability matches before they become signals.
    """
    # 1. THE GHOST FILTER: One team isn't even playing
    if (stats['home_sot'] == 0 and stats['home_corners'] == 0) or \
       (stats['away_sot'] == 0 and stats['away_corners'] == 0):
        return False, "Ghost Team"

    # 2. SPRAY AND PRAY: High volume but zero accuracy
    for side in ['home', 'away']:
        shots = stats[f"{side}_shots"]
        sot = stats[f"{side}_sot"]
        if shots >= 7 and sot <= 1:
            return False, f"Spray & Pray ({side})"

    # 3. BROKEN FAVORITE: Heavy favorite being dominated by underdog SOT
    home_odds = prematch.get("home_win_odds") or 2.0
    away_odds = prematch.get("away_win_odds") or 2.0
    if home_odds <= 1.40 and stats['away_sot'] > stats['home_sot']:
        return False, "Broken Favorite (Home)"
    if away_odds <= 1.40 and stats['home_sot'] > stats['away_sot']:
        return False, "Broken Favorite (Away)"

    return True, "Valid"

# =========================
# LOGIC FUNCTIONS
# =========================
def classify(score):
    if score >= 85: return "🔥 ELITE"
    elif score >= 70: return "🔥 STRONG"
    return "⚡ MEDIUM"

def estimate_probability(stats, delta, minute):
    prob = 0.45
    if stats["shots"] >= 10: prob += 0.10
    if stats["sot"] >= 4: prob += 0.15
    if delta["shots"] >= 3: prob += 0.10
    if minute >= 60: prob += 0.05
    return min(prob, 0.85)

def prob_to_odds(prob):
    return round(1 / prob, 2) if prob > 0 else None

def get_target_odds(odds_data, total_goals):
    if not odds_data: return None
    target = float(total_goals) + 1.5
    for o in odds_data:
        try:
            val = o["value"].replace("Over ", "").strip()
            if abs(float(val) - target) < 0.01: return float(o["odd"])
        except: continue
    return None

def calculate_value(book_odds, fair_odds):
    try: return round(((book_odds / fair_odds) - 1) * 100, 2)
    except: return None

# =========================
# SAVE RESULTS
# =========================
def save_result_to_csv(data):
    try:
        file_exists = os.path.isfile(RESULTS_CSV)
        with open(RESULTS_CSV, "a", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=[
                "match", "result", "signal_tier", "signal_tags", "model_score",
                "book_odds", "fair_odds", "model_prob", "value",
                "home_win_odds", "draw_odds", "away_win_odds",
                "pre_o15", "pre_o25", "pre_o35",
                "track_minute", "signal_minute", "total_shots", "total_sot", "total_corners",
                "home_shots", "away_shots", "home_sot", "away_sot", "home_corners", "away_corners",
                "home_accuracy", "away_accuracy", "delta_shots", "goals_at_signal"
            ])
            if not file_exists: writer.writeheader()
            
            sig = data.get("signal_stats", {})
            writer.writerow({
                "match": data.get("match") or data.get("teams"),
                "result": data.get("result"),
                "signal_tier": data.get("signal_tier"),
                "signal_tags": ",".join(data.get("signal_tags", [])),
                "model_score": data.get("model_score"),
                "book_odds": data.get("book_odds"),
                "fair_odds": data.get("fair_odds"),
                "model_prob": data.get("model_prob"),
                "value": data.get("value"),
                "home_win_odds": data.get("home_win_odds"),
                "draw_odds": data.get("draw_odds"),
                "away_win_odds": data.get("away_win_odds"),
                "pre_o15": data.get("prematch_over_1_5"),
                "pre_o25": data.get("prematch_over_2_5"),
                "pre_o35": data.get("prematch_over_3_5"),
                "track_minute": data.get("track_minute"),
                "signal_minute": data.get("signal_minute"),
                "total_shots": sig.get("shots"),
                "total_sot": sig.get("sot"),
                "total_corners": sig.get("corners"),
                "home_shots": sig.get("home_shots"),
                "away_shots": sig.get("away_shots"),
                "home_sot": sig.get("home_sot"),
                "away_sot": sig.get("away_sot"),
                "home_corners": sig.get("home_corners"),
                "away_corners": sig.get("away_corners"),
                "home_accuracy": sig.get("home_accuracy"),
                "away_accuracy": sig.get("away_accuracy"),
                "delta_shots": data.get("delta", {}).get("shots"),
                "goals_at_signal": data.get("goals_at_signal")
            })
    except Exception as e:
        logging.error(f"CSV save error: {e}")

# =========================
# RESULT CHECKER
# =========================
def check_finished_matches():
    logging.info("📊 Checking results...")
    for match_id, data in list(seen_matches.items()):
        try:
            if (datetime.now() - data["time"]).total_seconds() < 2400: continue
            r = requests.get(f"{BASE_URL}/fixtures?id={match_id}", headers=HEADERS)
            res = r.json().get("response", [])
            if not res: continue
            fixture = res[0]["fixture"]
            goals = res[0]["goals"]
            if fixture["status"]["short"] not in ["FT", "AET", "PEN"]: continue

            initial_total = sum(map(int, data["initial_score"].split("-")))
            final_total = (goals["home"] or 0) + (goals["away"] or 0)
            result = "✅ WIN" if final_total >= initial_total + 1 else "❌ LOSS" # Using +1 based on Over +0.5 logic usually

            result_data = data.copy()
            result_data["result"] = result
            save_result_to_csv(result_data)
            logging.info(f"📊 RESULT → {data['teams']} | {result}")
            del seen_matches[match_id]
            save_signals()
        except Exception as e:
            logging.error(f"Result error: {e}")

# =========================
# MAIN LOOP
# =========================
def run():
    global last_result_check
    logging.info("🚀 PRO SCANNER RUNNING - Phases 1 & 2")
    while True:
        try:
            matches = get_live_matches()
            if not matches:
                time.sleep(60); continue

            for m in matches[:80]:
                try:
                    fixture, teams, goals = m["fixture"], m["teams"], m["goals"]
                    match_id, minute = fixture["id"], fixture["status"]["elapsed"]
                    if not minute or minute < 30 or minute > 70: continue

                    home, away = teams["home"]["name"], teams["away"]["name"]
                    h_goals, a_goals = goals["home"] or 0, goals["away"] or 0
                    total = h_goals + a_goals
                    if total >= 3: continue

                    stats = get_stats(match_id)
                    if stats is None: continue

                    # TRACKING (35-45 min)
                    if 35 <= minute <= 45 and match_id not in tracked_matches:
                        prematch = get_prematch_odds(match_id)
                        favorite = "NONE"
                        h_odds, a_odds = prematch.get("home_win_odds"), prematch.get("away_win_odds")
                        if h_odds and a_odds:
                            if h_odds <= 1.80: favorite = "HOME"
                            elif a_odds <= 1.80: favorite = "AWAY"

                        tracked_matches[match_id] = {
                            "teams": f"{home} vs {away}", "track_minute": minute, "track_stats": stats,
                            "score": f"{h_goals}-{a_goals}", "time": datetime.now(), "favorite": favorite,
                            **prematch
                        }
                        save_tracked()
                        logging.info(f"🧠 TRACKED → {home} vs {away} | Fav: {favorite}")

                    # CONFIRMATION (50-65 min)
                    if 50 <= minute <= 65 and match_id in tracked_matches and match_id not in seen_matches:
                        first = tracked_matches[match_id]
                        
                        # Apply Hard Filters (Quality Control)
                        is_valid, reason = is_high_quality_signal(stats, first)
                        if not is_valid:
                            logging.info(f"⛔ FILTERED → {home} vs {away} | Reason: {reason}")
                            del tracked_matches[match_id]; continue

                        # Basic Momentum Check
                        if stats["shots"] <= first["track_stats"]["shots"]: continue
                        if stats["corners"] < 4: continue

                        # Scoring logic (Unchanged as requested)
                        score = 40
                        if h_goals == a_goals: score += 20
                        if stats["shots"] >= 12: score += 15
                        elif stats["shots"] >= 9: score += 10
                        if stats["sot"] >= 6: score += 25
                        elif stats["sot"] >= 4: score += 10
                        delta_s = stats["shots"] - first["track_stats"]["shots"]
                        if delta_s >= 5: score += 15
                        elif delta_s >= 3: score += 8
                        
                        tier = classify(score)
                        
                        # Tagging Archetypes
                        tags = []
                        h_p = stats["home_shots"] + (stats["home_sot"]*2) + stats["home_corners"]
                        a_p = stats["away_shots"] + (stats["away_sot"]*2) + stats["away_corners"]
                        total_p = h_p + a_p
                        h_pct = (h_p / total_p * 100) if total_p > 0 else 50
                        
                        if first["favorite"] == "HOME" and h_goals <= a_goals: tags.append("WOUNDED_FAV")
                        elif first["favorite"] == "AWAY" and a_goals <= h_goals: tags.append("WOUNDED_FAV")
                        if h_pct >= 70: tags.append("HOME_SIEGE")
                        elif h_pct <= 30: tags.append("AWAY_SIEGE")
                        if stats["home_sot"] >= 2 and stats["away_sot"] >= 2: tags.append("END_TO_END")

                        # Odds & Value
                        odds_data = get_odds(match_id)
                        book_odds = get_target_odds(odds_data, total)
                        delta_dict = {"shots": delta_s}
                        prob = estimate_probability(stats, delta_dict, minute)
                        fair_odds = prob_to_odds(prob)
                        value = calculate_value(book_odds, fair_odds) if book_odds else None

                        if value and value >= 2:
                            send_telegram(f"{tier} SIGNAL\n{home} vs {away}\nMin: {minute}\nScore: {h_goals}-{a_goals}\nMarket: O{total+1.5}\nBook: {book_odds}\nFair: {fair_odds}\nValue: {value}%\nTags: {', '.join(tags)}")
                            
                            seen_matches[match_id] = {
                                "time": datetime.now(), "teams": f"{home} vs {away}", "initial_score": f"{h_goals}-{a_goals}",
                                "signal_minute": minute, "track_minute": first["track_minute"], "track_stats": first["track_stats"],
                                "signal_stats": stats, "signal_tags": tags, "delta": delta_dict, "model_score": score,
                                "signal_tier": tier, "book_odds": book_odds, "fair_odds": fair_odds, "model_prob": prob, "value": value,
                                "goals_at_signal": total, **first
                            }
                            del tracked_matches[match_id]
                            save_tracked(); save_signals()

                except Exception as e:
                    logging.error(f"Match logic error: {e}")

            # Check Results every 30 mins
            if time.time() - last_result_check > 1800:
                check_finished_matches()
                last_result_check = time.time()

            time.sleep(300)
        except Exception as e:
            logging.error(f"LOOP ERROR: {e}")
            time.sleep(60)

if __name__ == "__main__":
    load_signals()
    load_tracked()
    run()
