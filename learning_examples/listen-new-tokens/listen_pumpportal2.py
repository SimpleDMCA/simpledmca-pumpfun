"""
Listens for new Pump.fun token creations via PumpPortal WebSocket.
"""

import asyncio
import json
import os
from typing import Final
import websockets
import sys
from datetime import datetime
import pandas as pd
from solana.rpc.async_api import AsyncClient
from dotenv import load_dotenv
load_dotenv()


sys.path.append('/Users/sterling/Documents/simpleDmca/PumpFun/pump-fun-bot/learning_examples')

from bonding_curve_progress.top_holders import get_token_holders



dir_path = os.path.dirname(os.path.realpath(__file__))
print("current working dir: %s" % dir_path)


# PumpPortal WebSocket URL
WS_URL = "wss://pumpportal.fun/api/data"
TOKENS = 1000
RPC_ENDPOINT: Final[str] = os.environ.get("SOLANA_NODE_RPC_ENDPOINT")
def format_sol(value):
    return f"{value:.6f} SOL"


def format_timestamp(timestamp):
    return datetime.fromtimestamp(timestamp / 1000).strftime("%Y-%m-%d %H:%M:%S")


# async def listen_for_new_tokens():
#     async with websockets.connect(WS_URL) as websocket:
#         tokens = TOKENS
#         # Subscribe to new token events
#         await websocket.send(json.dumps({"method": "subscribeNewToken", "params": []}))

#         print("Listening for new token creations...")
#         columns = [
#             "date",
#             "address",
#             "creator",
#             "initialBuy",
#             "marketCap",
#             "bondingCurveKey",
#             "vSolInBondingCurve",
#             "vTokensInBondingCurve",
#             "uri",
#             "signature"
#         ]
#         df = pd.DataFrame(columns=columns)

#         for _ in range(tokens):  # Updated to iterate properly
#             try:
#                 message = await websocket.recv()
#                 data = json.loads(message)

#                 if "method" in data and data["method"] == "newToken":
#                     token_info = data.get("params", [{}])[0]
#                 elif "signature" in data and "mint" in data:
#                     token_info = data
#                 else:
#                     continue

#                 new_row = pd.DataFrame([{
#                     "date": pd.Timestamp.now(),
#                     "address": token_info.get('mint'),
#                     "creator": token_info.get('traderPublicKey'),
#                     "initialBuy": token_info.get('initialBuy', 0),
#                     "marketCap": token_info.get('marketCapSol', 0),
#                     "bondingCurveKey": token_info.get('bondingCurveKey'),
#                     "vSolInBondingCurve": token_info.get('vSolInBondingCurve', 0),
#                     "vTokensInBondingCurve": token_info.get('vTokensInBondingCurve', 0),
#                     "uri": token_info.get('uri'),
#                     "signature": token_info.get('signature')
#                 }])

#                 print("\n" + "=" * 50)
#                 print(
#                     f"New token created: {token_info.get('name')} ({token_info.get('symbol')})"
#                 )
#                 print("=" * 50)
#                 print(f"Address:        {token_info.get('mint')}")
#                 print(f"Creator:        {token_info.get('traderPublicKey')}")
#                 print(f"Initial Buy:    {format_sol(token_info.get('initialBuy', 0))}")
#                 print(
#                     f"Market Cap:     {format_sol(token_info.get('marketCapSol', 0))}"
#                 )
#                 print(f"Bonding Curve:  {token_info.get('bondingCurveKey')}")
#                 print(
#                     f"Virtual SOL:    {format_sol(token_info.get('vSolInBondingCurve', 0))}"
#                 )
#                 print(
#                     f"Virtual Tokens: {token_info.get('vTokensInBondingCurve', 0):,.0f}"
#                 )
#                 print(f"Metadata URI:   {token_info.get('uri')}")
#                 print(f"Signature:      {token_info.get('signature')}")
#                 print("=" * 50)
                
#                 # Use pd.concat to add the new row to the DataFrame
#                 df = pd.concat([df, new_row], ignore_index=True)
#             except websockets.exceptions.ConnectionClosed:
#                 print("\nWebSocket connection closed. Reconnecting...")
#                 break
#             except json.JSONDecodeError:
#                 print(f"\nReceived non-JSON message: {message}")
#             except Exception as e:
#                 print(f"\nAn error occurred: {e}")
#         # Export DataFrame to CSV after loop
#         df.to_csv(f'tokens{pd.Timestamp.now()}.csv', index=False)


async def listen_for_new_tokens():
    async with websockets.connect(WS_URL) as websocket, AsyncClient(RPC_ENDPOINT, commitment="processed", timeout=120) as client:
        await client.is_connected()
        tokens = TOKENS
        
        
        
        # Initialize results dictionary
        results = {
            "last_updated": "",
            "tokens": {}
        }
        
        # Load existing results if file exists
        json_file = "new_tokens_results.json"
        if os.path.exists(json_file):
            with open(json_file, 'r') as f:
                results = json.load(f)

        await websocket.send(json.dumps({"method": "subscribeNewToken", "params": []}))
        print("Listening for new token creations...")

        for _ in range(tokens):
            try:
                message = await websocket.recv()
                data = json.loads(message)

                if "method" in data and data["method"] == "newToken":
                    token_info = data.get("params", [{}])[0]
                elif "signature" in data and "mint" in data:
                    token_info = data
                else:
                    continue

                mint_address = token_info.get('mint')
                
                print("-"*50)
                print(f'mint_address:{mint_address}')
              
                
                
                # Skip if we've already recorded this mint
                if mint_address in results["tokens"]:
                    continue

                #========Optional if you would like to collect top holders data upon minting
                # holders = await get_token_holders(mint_address, client)
                # # holders = await get_token_holders("C3VVwpqKan4z8c4vZFTWNbiy7jgQ1CTPvJ9d2y1dpump", client)
            
                # print("-"*50)
                # print(f'holders:{holders}')

                # holders_data = []                
                # if holders:
                #     for holder in holders:
                #         holders_data.append({
                #             "address": holder['address'],
                #             "balance": holder['balance'],
                #             "raw_balance": holder['raw_balance'],
                #             "decimals": holder['decimals'],
                #             "percentage": holder['percentage']
                #         })
                #     print(f"Current Holders: /n {len(holders_data)}")
                    
                # Format token data
                token_data = {
                    "detection_time": datetime.now().isoformat(),
                    "type":"NEW",
                    "creator": token_info.get('traderPublicKey'),
                    "name": token_info.get('name'),
                    "symbol": token_info.get('symbol'),
                    "initial_buy": token_info.get('initialBuy', 0),
                    "market_cap": token_info.get('marketCapSol', 0),
                    "bonding_curve": token_info.get('bondingCurveKey'),
                    "virtual_sol": token_info.get('vSolInBondingCurve', 0),
                    "virtual_tokens": token_info.get('vTokensInBondingCurve', 0),
                    "metadata_uri": token_info.get('uri'),
                    "signature": token_info.get('signature'),
                    # "holders":holders_data,
                }

                # Print token information
                print("\n" + "=" * 50)
                print(f"New token created: {token_data['name']} ({token_data['symbol']})")
                print("=" * 50)
                print(f"Address:        {mint_address}")
                print(f"Creator:        {token_data['creator']}")
                print(f"Initial Buy:    {format_sol(token_data['initial_buy'])}")
                print(f"Market Cap:     {format_sol(token_data['market_cap'])}")
                print(f"Bonding Curve:  {token_data['bonding_curve']}")
                print(f"Virtual SOL:    {format_sol(token_data['virtual_sol'])}")
                print(f"Virtual Tokens: {token_data['virtual_tokens']:,.0f}")
                print(f"Metadata URI:   {token_data['metadata_uri']}")
                print(f"Signature:      {token_data['signature']}")
                print("=" * 50)

                # Update results
                results["tokens"][mint_address] = token_data
                results["last_updated"] = datetime.now().isoformat()

                # Save to JSON file after each new token
                try:
                    with open(json_file, 'w') as f:
                        json.dump(results, f, indent=4)
                except Exception as e:
                    print(f"Error saving to JSON file: {e}")

            except websockets.exceptions.ConnectionClosed:
                print("\nWebSocket connection closed. Reconnecting...")
                break
            except json.JSONDecodeError:
                print(f"\nReceived non-JSON message: {message}")
            except Exception as e:
                print(f"\nAn error occurred: {e}")

        # Final save to JSON file
        try:
            with open(json_file, 'w') as f:
                json.dump(results, f, indent=4)
        except Exception as e:
            print(f"Error saving final results to JSON file: {e}")


async def main():
    # while True:
    try:
        await listen_for_new_tokens()
    except Exception as e:
        print(f"\nAn error occurred: {e}")
        print("Reconnecting in 5 seconds...")
        await asyncio.sleep(5)


if __name__ == "__main__":
    asyncio.run(main())
