#!/usr/bin/env python
"""Test script to trigger monitor_hazards() and capture logs for external feed integration."""

import logging
import sys
from datetime import datetime

# Configure logging to capture all output
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('hazard_monitoring_test.log', mode='w')
    ]
)

# Import the scheduler module
from scheduler import monitor_hazards

if __name__ == "__main__":
    print("\n" + "="*80)
    print("Starting hazard monitoring test at", datetime.utcnow().isoformat())
    print("="*80 + "\n")
    
    try:
        monitor_hazards()
        print("\n" + "="*80)
        print("Hazard monitoring completed successfully")
        print("="*80 + "\n")
    except Exception as e:
        print("\n" + "="*80)
        print(f"ERROR during hazard monitoring: {e}", file=sys.stderr)
        print("="*80 + "\n")
        import traceback
        traceback.print_exc()
        sys.exit(1)
