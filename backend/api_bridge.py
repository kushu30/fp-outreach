#!/usr/bin/env python3
import subprocess
import sys
import json
import os

def scan_domain(domain):
    """Run scanner for single domain and return result."""
    try:
        result = subprocess.run(
            [sys.executable, "scanner_v2.py", "--domain", domain, "--mode", "single"],
            capture_output=True,
            text=True,
            timeout=90
        )
        
        # Parse JSON output from scanner
        for line in result.stdout.split('\n'):
            if line.strip().startswith('{'):
                try:
                    data = json.loads(line)
                    if data.get("status") == "complete":
                        return data.get("result")
                except:
                    pass
        
        return None
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return None

if __name__ == "__main__":
    if len(sys.argv) > 1:
        domain = sys.argv[1]
        result = scan_domain(domain)
        if result:
            print(json.dumps(result))
        else:
            print(json.dumps({"error": "Scan failed"}))