"""
Background meeting prep watcher. Runs every 5 minutes during configured hours.
Start with: python -m src.meeting_prep_watcher
"""

import time
import datetime
from src.services.meeting_prep import MeetingPrepProcessor

def main():
    print("🔁 Meeting prep watcher started. Press Ctrl+C to stop.")
    processor = MeetingPrepProcessor()
    while True:
        now = datetime.datetime.now()
        hour = now.hour
        # Only run between 5 AM and 8 PM
        if 5 <= hour < 20:
            print(f"[{now.strftime('%H:%M')}] Checking for new meetings...")
            try:
                processed = processor.process_new_events()
                if processed:
                    print(f"   ✅ Processed {processed} meeting(s).")
                processor.process_post_meetings()
            except Exception as e:
                print(f"   ⚠️ Error: {e}")
        else:
            print(f"[{now.strftime('%H:%M')}] Outside operating hours. Sleeping.")
        time.sleep(300)  # 5 minutes

if __name__ == "__main__":
    main()