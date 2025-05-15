"""
Listens for 'Migrate' instructions from a Solana migration program via WebSocket.
Parses and logs transaction details (e.g., mint, liquidity, token accounts) for successful migrations.

Note: skips transactions with truncated logs (no Program data in the logs -> no parsed data).
To cover those cases, please use an additional RPC call (get transaction data) or additional listener not based on logs.
"""

import asyncio
import base64
import json
import os
import struct

import base58
import websockets
from dotenv import load_dotenv
from solders.pubkey import Pubkey

load_dotenv()

WSS_ENDPOINT = os.environ.get("SOLANA_NODE_WSS_ENDPOINT")
MIGRATION_PROGRAM_ID = Pubkey.from_string("39azUYFWPz3VHgKCf3VChUwbpURdCHRxjWVowf5jUJjg")


def parse_migrate_instruction(data):
    if len(data) < 8:
        print(f"[ERROR] Data length too short: {len(data)} bytes")
        return None

    offset = 8
    parsed_data = {}

    fields = [
        ("timestamp", "i64"),
        ("index", "u16"), 
        ("creator", "publicKey"),
        ("baseMint", "publicKey"),
        ("quoteMint", "publicKey"),
        ("baseMintDecimals", "u8"),
        ("quoteMintDecimals", "u8"),
        ("baseAmountIn", "u64"),
        ("quoteAmountIn", "u64"),
        ("poolBaseAmount", "u64"),
        ("poolQuoteAmount", "u64"),
        ("minimumLiquidity", "u64"),
        ("initialLiquidity", "u64"),
        ("lpTokenAmountOut", "u64"),
        ("poolBump", "u8"),
        ("pool", "publicKey"),
        ("lpMint", "publicKey"),
        ("userBaseTokenAccount", "publicKey"),
        ("userQuoteTokenAccount", "publicKey"),
    ]

    try:
        for field_name, field_type in fields:
            if field_type == "publicKey":
                value = data[offset:offset + 32]
                parsed_data[field_name] = base58.b58encode(value).decode("utf-8")
                offset += 32
            elif field_type in {"u64", "i64"}:
                value = struct.unpack("<Q", data[offset:offset + 8])[0] if field_type == "u64" else struct.unpack("<q", data[offset:offset + 8])[0]
                parsed_data[field_name] = value
                offset += 8
            elif field_type == "u16":
                value = struct.unpack("<H", data[offset:offset + 2])[0]
                parsed_data[field_name] = value
                offset += 2
            elif field_type == "u8":
                value = data[offset]
                parsed_data[field_name] = value
                offset += 1

        return parsed_data

    except Exception as e:
        print(f"[ERROR] Failed to parse data at offset {offset}: {e}")
        return None


def is_transaction_successful(logs):
    for log in logs:
        if "AnchorError thrown" in log or "Error" in log:
            print(f"[ERROR] Transaction failed: {log}")
            return False
    return True


def print_transaction_details(log_data):
    logs = log_data.get("logs", [])
    parsed_data = {}

    for log in logs:
        if log.startswith("Program data:"):
            try:
                data = base64.b64decode(log.split(": ")[1])
                parsed_data = parse_migrate_instruction(data)
                if parsed_data:
                    print("[INFO] Parsed from Program data:")
                    for key, value in parsed_data.items():
                        print(f"  {key}: {value}")
            except Exception as e:
                print(f"[ERROR] Failed to decode Program data: {e}")

    if not parsed_data:
        print("[ERROR] Failed to parse migration data: parsed data is empty")


# async def listen_for_migrations():
#     while True:
#         try:
#             print("\n[INFO] Connecting to WebSocket ...")
#             async with websockets.connect(WSS_ENDPOINT) as websocket:
#                 subscription_message = json.dumps(
#                     {
#                         "jsonrpc": "2.0",
#                         "id": 1,
#                         "method": "logsSubscribe",
#                         "params": [
#                             {"mentions": [str(MIGRATION_PROGRAM_ID)]},
#                             {"commitment": "processed"},
#                         ],
#                     }
#                 )
#                 await websocket.send(subscription_message)
#                 print(f"[INFO] Listening for migration instructions from program: {MIGRATION_PROGRAM_ID}")

#                 response = await websocket.recv()
#                 print(f"[INFO] Subscription response: {response}")

#                 while True:
#                     try:
#                         response = await asyncio.wait_for(websocket.recv(), timeout=60)
#                         data = json.loads(response)

#                         if "method" in data and data["method"] == "logsNotification":
#                             log_data = data["params"]["result"]["value"]
#                             logs = log_data.get("logs", [])

#                             signature = log_data.get('signature', 'N/A')
#                             print(f"\n[INFO] Transaction signature: {signature}")

#                             if is_transaction_successful(logs):
#                                 if not any("Program log: Instruction: Migrate" in log for log in logs):
#                                     print("[INFO] Skipping: no migrate instruction")
#                                     continue

#                                 if any("Program log: Bonding curve already migrated" in log for log in logs):
#                                     print("[INFO] Skipping: bonding curve already migrated")
#                                     continue

#                                 print("[INFO] Processing migration instruction...")
#                                 print_transaction_details(log_data)
#                             else:
#                                 print("[INFO] Skipping failed transaction.")
#                     except TimeoutError:
#                         print("[INFO] Timeout waiting for WebSocket message, retrying...")
#                     except Exception as e:
#                         print(f"[ERROR] An error occurred: {e}")
#                         break

#         except Exception as e:
#             print(f"[ERROR] Connection error: {e}")
#             print("[INFO] Reconnecting in 5 seconds...")
#             await asyncio.sleep(5)

# if __name__ == "__main__":
#     asyncio.run(listen_for_migrations())



import json
from datetime import datetime

# Add these constants at the top with the others
RESULTS_FILE = "migration_results.json"

def load_results():
    """Load existing results from JSON file"""
    try:
        with open(RESULTS_FILE, 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        return {
            "last_updated": "",
            "migrations": {}
        }

def save_results(results):
    """Save results to JSON file"""
    try:
        with open(RESULTS_FILE, 'w') as f:
            json.dump(results, f, indent=4)
    except Exception as e:
        print(f"[ERROR] Failed to save results: {e}")

def process_transaction_details(log_data):
    """Process and return parsed transaction details"""
    logs = log_data.get("logs", [])
    parsed_data = None

    for log in logs:
        if log.startswith("Program data:"):
            try:
                data = base64.b64decode(log.split(": ")[1])
                parsed_data = parse_migrate_instruction(data)
                if parsed_data:
                    return parsed_data
            except Exception as e:
                print(f"[ERROR] Failed to decode Program data: {e}")

    return None

async def listen_for_migrations():
    """Main function to listen for migrations and save results"""
    # Load existing results
    results = load_results()
    
    while True:
        try:
            print("\n[INFO] Connecting to WebSocket ...")
            async with websockets.connect(WSS_ENDPOINT) as websocket:
                subscription_message = json.dumps({
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "logsSubscribe",
                    "params": [
                        {"mentions": [str(MIGRATION_PROGRAM_ID)]},
                        {"commitment": "processed"},
                    ],
                })
                await websocket.send(subscription_message)
                print(f"[INFO] Listening for migration instructions from program: {MIGRATION_PROGRAM_ID}")

                response = await websocket.recv()
                print(f"[INFO] Subscription response: {response}")

                while True:
                    try:
                        response = await asyncio.wait_for(websocket.recv(), timeout=60)
                        data = json.loads(response)

                        if "method" in data and data["method"] == "logsNotification":
                            log_data = data["params"]["result"]["value"]
                            logs = log_data.get("logs", [])
                            signature = log_data.get('signature', 'N/A')

                            # Skip if we've already processed this transaction
                            if signature in results["migrations"]:
                                print(f"[INFO] Skipping: already processed transaction {signature}")
                                continue

                            print(f"\n[INFO] Transaction signature: {signature}")

                            if is_transaction_successful(logs):
                                if not any("Program log: Instruction: Migrate" in log for log in logs):
                                    print("[INFO] Skipping: no migrate instruction")
                                    continue

                                if any("Program log: Bonding curve already migrated" in log for log in logs):
                                    print("[INFO] Skipping: bonding curve already migrated")
                                    continue

                                print("[INFO] Processing migration instruction...")
                                parsed_data = process_transaction_details(log_data)
                                
                                if parsed_data:
                                    # Format the migration data
                                    migration_entry = {
                                        "detection_time": datetime.now().isoformat(),
                                        "timestamp": parsed_data["timestamp"],
                                        "creator": parsed_data["creator"],
                                        "tokens": {
                                            "base": {
                                                "mint": parsed_data["baseMint"],
                                                "decimals": parsed_data["baseMintDecimals"],
                                                "amount": parsed_data["baseAmountIn"]
                                            },
                                            "quote": {
                                                "mint": parsed_data["quoteMint"],
                                                "decimals": parsed_data["quoteMintDecimals"],
                                                "amount": parsed_data["quoteAmountIn"]
                                            }
                                        },
                                        "pool": {
                                            "address": parsed_data["pool"],
                                            "lp_mint": parsed_data["lpMint"],
                                            "initial_liquidity": parsed_data["initialLiquidity"],
                                            "base_amount": parsed_data["poolBaseAmount"],
                                            "quote_amount": parsed_data["poolQuoteAmount"]
                                        }
                                    }

                                    # Update results
                                    results["migrations"][signature] = migration_entry
                                    results["last_updated"] = datetime.now().isoformat()
                                    
                                    # Save to file
                                    save_results(results)
                                    print(f"[INFO] Saved migration data for signature: {signature}")

                                    # Print details for logging
                                    print("[INFO] Parsed from Program data:")
                                    for key, value in parsed_data.items():
                                        print(f"  {key}: {value}")
                            else:
                                print("[INFO] Skipping failed transaction.")
                                
                    except TimeoutError:
                        print("[INFO] Timeout waiting for WebSocket message, retrying...")
                    except Exception as e:
                        print(f"[ERROR] An error occurred: {e}")
                        break

        except Exception as e:
            print(f"[ERROR] Connection error: {e}")
            print("[INFO] Reconnecting in 5 seconds...")
            await asyncio.sleep(5)

if __name__ == "__main__":
    asyncio.run(listen_for_migrations())