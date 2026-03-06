"""
check_api_databases.py
Lists all court database IDs from the CanLII API.
Run this to find the correct db_id values for courts.py.

Usage:
    python check_api_databases.py
"""

from config import CANLII_API_KEY
import requests

resp = requests.get(
    "https://api.canlii.org/v1/caseBrowse/en/",
    params={"api_key": CANLII_API_KEY},
    timeout=15,
)
resp.raise_for_status()

databases = resp.json().get("caseDatabases", [])

print(f"\n  {len(databases)} databases found\n")
print(f"  {'DB ID':<12} {'Jurisdiction':<6} Name")
print(f"  {'─'*12} {'─'*6} {'─'*50}")

for db in sorted(databases, key=lambda d: str(d.get("jurisdiction", ""))):
    jur = db.get("jurisdiction", "?")
    if isinstance(jur, dict):
        jur = jur.get("id", "?")
    print(f"  {db['databaseId']:<12} {str(jur):<6} {db['name']}")
