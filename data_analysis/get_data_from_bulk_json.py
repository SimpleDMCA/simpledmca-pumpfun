import requests
import json
import pandas as pd
import time
from datetime import datetime
import os
from typing import Dict, List

tokens_path = '/Users/sterling/Documents/simpleDmca/simpledmca-pumpfun/data/graduating_tokens_results.json'

def load_graduating_tokens() -> Dict:
    """Load graduating tokens from JSON file."""
    try:
        with open(tokens_path, 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        return {"last_updated": "", "tokens": {}}


def load_processed_transactions() -> Dict:
    """Load already processed transactions from JSON file."""
    try:
        with open('token_transactions.json', 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        return {"last_updated": "", "transactions": {}}

def fetch_token_transactions(mint: str, headers: Dict) -> List:
    """Fetch all transactions for a token from Moralis API."""
    base_url = f"https://solana-gateway.moralis.io/token/mainnet/{mint}/swaps?order=ASC"
    all_transactions = []
    cursor = None

    while True:
        url = f"{base_url}&cursor={cursor}" if cursor else base_url
        
        try:
            response = requests.get(url, headers=headers)
            if response.status_code != 200:
                print(f"Error fetching data for {mint}: {response.status_code}")
                break

            json_data = response.json()
            
            for transaction in json_data["result"]:
                txn_type = transaction["transactionType"]
                date = transaction["blockTimestamp"]
                txn_hash = transaction["transactionHash"]

                if txn_type == "buy":
                    ths_data = transaction["bought"]
                    sol_data = transaction["sold"]
                else:  # sell
                    ths_data = transaction["sold"]
                    sol_data = transaction["bought"]

                transaction_data = {
                    "date": date,
                    "type": txn_type,
                    "ths_amount": ths_data["amount"],
                    "ths_usd_price": ths_data["usdPrice"],
                    "ths_usd_amount": ths_data["usdAmount"],
                    "sol_amount": sol_data["amount"],
                    "sol_usd_price": sol_data["usdPrice"],
                    "sol_usd_amount": sol_data["usdAmount"],
                    "txn_hash": txn_hash
                }
                all_transactions.append(transaction_data)

            cursor = json_data.get("cursor")
            if not cursor:
                break

        except Exception as e:
            print(f"Error processing {mint}: {str(e)}")
            break

    return all_transactions

def main():
    # API Configuration
    headers = {
        "Accept": "application/json",
        "X-API-Key": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJub25jZSI6IjlhMTYzMmY0LWU0NTUtNDAzOS1hMzE0LTI4ZjViODdlMjc5ZSIsIm9yZ0lkIjoiNDQ1ODE4IiwidXNlcklkIjoiNDU4Njg5IiwidHlwZUlkIjoiOTUwYmY3NmYtNWY4MC00N2ZkLTg3OTktNDczY2Y0OGU4ZjBiIiwidHlwZSI6IlBST0pFQ1QiLCJpYXQiOjE3NDY2NTAwMTksImV4cCI6NDkwMjQxMDAxOX0.7bNqCTLZzZ9-lKpijzfx-RJHYdeq5lQTKQCAnlX1I7E"
    }

    while True:
        try:
            # Load current data
            graduating_tokens = load_graduating_tokens()
            processed_transactions = load_processed_transactions()

                # Process new tokens
            for mint in graduating_tokens["tokens"]:
                if mint not in processed_transactions["transactions"]:
                    print(f"Processing new token: {mint[:8]}")
                    
                    # Fetch transactions
                    transactions = fetch_token_transactions(mint, headers)
                    
                    if transactions:
                        # Add to processed transactions
                        processed_transactions["transactions"][mint] = {
                            "first_seen": graduating_tokens["tokens"][mint]["detection_time"],
                            "last_updated": datetime.now().isoformat(),
                            "transactions": transactions
                        }

                        # Save updated transactions
                        with open('graduating_token_transactions.json', 'w') as f:
                            json.dump(processed_transactions, f, indent=4)
                        
                        print(f"Saved {len(transactions)} transactions for {mint[:8]}")

                # Export to CSV if needed
                all_transactions = []
                for mint, data in processed_transactions["transactions"].items():
                    for txn in data["transactions"]:
                        txn["mint"] = mint
                        all_transactions.append(txn)

                if all_transactions:
                    df = pd.DataFrame(all_transactions)
                    df.to_csv('data/all_transactions.csv', index=False)


        except Exception as e:
            print(f"Error in main loop: {str(e)}")
            time.sleep(60)

if __name__ == "__main__":
    main()