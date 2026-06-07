#!/usr/bin/env python3
import argparse
import asyncio
import json
import sys
import os

# Import the fast scraper module which defines run_scanner
try:
    import backend.fast_scraper as fs
except Exception as e:
    print(json.dumps({"error": f"failed to import fast_scraper: {e}"}))
    sys.exit(1)

async def scan_single(domain):
    try:
        results = await fs.run_scanner([domain], max_concurrent=1)
        if results and len(results) > 0:
            single = results[0]
            # Persist to results.json (merge/replace by domain)
            outpath = "../data/results.json"
            try:
                existing = []
                if os.path.exists(outpath):
                    with open(outpath, 'r', encoding='utf-8') as f:
                        existing = json.load(f)
                # remove any existing entry for this domain
                existing = [e for e in existing if e.get('domain') != single.get('domain')]
                existing.insert(0, single)
                with open(outpath, 'w', encoding='utf-8') as f:
                    json.dump(existing, f, indent=2, ensure_ascii=False)
            except Exception as e:
                print(json.dumps({"warning": f"failed to persist results.json: {e}"}))

            print(json.dumps(single, ensure_ascii=False))
            return 0
        else:
            print(json.dumps({"domain": domain, "error": "no result"}))
            return 2
    except Exception as e:
        print(json.dumps({"domain": domain, "error": str(e)}))
        return 3

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--domain', required=True, help='Domain to scan')
    args = parser.parse_args()
    rc = asyncio.run(scan_single(args.domain))
    sys.exit(rc)
