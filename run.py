"""
Single entry point for Sasha's Sales Support Agent.
Starts the Flask server, runs the meeting prep watcher, and executes the daily briefing.
Designed to be launched once at login; handles laptop sleep and missed runs safely.
"""

import datetime
import time
import threading
import traceback

from src.utils.logger import get_logger
from src.utils.agent_state import load_agent_status, save_agent_status
from src.services.status_reporter import send_status_email

from server import app as flask_app

logger = get_logger("run")

# ---- Constants ----
WATCHER_INTERVAL_SECONDS = 300   # 5 minutes
BRIEFING_HOUR = 5                # 5 AM
BRIEFING_MINUTE = 0

def should_run_briefing_today() -> bool:
    """Return True if today is a weekday and the briefing has not yet run today."""
    now = datetime.datetime.now()
    if now.weekday() >= 5:   # Saturday or Sunday
        return False

    status = load_agent_status()
    today_str = now.strftime("%Y-%m-%d")

    # If status_date is today and briefing ran, don't run again
    return status.get("status_date") != today_str


def start_flask_server():
    """Start the Flask control centre server in a background daemon thread."""
    def run_flask():
        logger.info("Starting Flask control centre server on http://127.0.0.1:8500")
        flask_app.run(
            host="127.0.0.1",
            port=8500,
            debug=False,
            use_reloader=False,
        )

    thread = threading.Thread(target=run_flask, daemon=True)
    thread.start()
    logger.info("Flask server thread started.")


def run_daily_briefing():
    """Import and run the daily briefing once."""
    logger.info("Daily briefing trigger fired.")
    try:
        # Import inside function to avoid circular imports at startup
        from src.main import main as briefing_main
        briefing_main()
        logger.info("Daily briefing completed successfully.")
    except Exception as e:
        logger.error("Daily briefing failed: %s", e)
        logger.error(traceback.format_exc())
        raise


def watcher_cycle():
    """Run one meeting prep + post-meeting check."""
    logger.info("Watcher cycle started.")
    try:
        from src.services.meeting_prep import MeetingPrepProcessor
        prep = MeetingPrepProcessor()
        processed = prep.process_new_events()
        if processed:
            logger.info("Meeting prep processed %d new event(s).", processed)
        prep.process_post_meetings()
        logger.info("Watcher cycle complete.")
    except Exception as e:
        logger.error("Watcher cycle failed: %s", e)
        logger.error(traceback.format_exc())
        raise


def main_loop():
    """Main orchestration loop. Runs forever until process is terminated."""
    logger.info("Sales Support Agent started.")

    # Start Flask server
    start_flask_server()

    # Initial catch-up: run briefing if missed
    try:
        if should_run_briefing_today():
            logger.info("Starting catch-up daily briefing.")
            run_daily_briefing()
    except Exception:
        logger.error("Catch-up briefing failed. Continuing watcher loop.")
        # Send error email to developer
        try:
            send_status_email(manual=True)
        except Exception:
            pass

    # Main loop
    while True:
        try:
            now = datetime.datetime.now()

            # Run briefing at 5 AM on weekdays if not already run
            if should_run_briefing_today() and now.hour >= BRIEFING_HOUR:
                run_daily_briefing()

            # Run watcher cycle every 5 minutes during waking hours
            if 5 <= now.hour < 20:
                watcher_cycle()
            else:
                logger.info("Outside operating hours (5 AM - 8 PM). Sleeping.")

        except Exception:
            logger.error("Unhandled error in main loop:\n%s", traceback.format_exc())
            # Send one status email to Rishi on error, then continue
            try:
                send_status_email(manual=True)
            except Exception:
                pass

        time.sleep(WATCHER_INTERVAL_SECONDS)


if __name__ == "__main__":
    main_loop()