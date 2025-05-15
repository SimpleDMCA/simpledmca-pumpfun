"""
Module for querying and analyzing soon-to-graduate tokens in the Pump.fun program.
"""

import asyncio
import json
import os
import struct
from typing import Final

from dotenv import load_dotenv
from datetime import datetime
from solana.rpc.async_api import AsyncClient
from solana.rpc.types import MemcmpOpts, TokenAccountOpts
from solders.pubkey import Pubkey

load_dotenv()

# Constants
RPC_ENDPOINT: Final[str] = os.environ.get("SOLANA_NODE_RPC_ENDPOINT")
PUMP_PROGRAM_ID: Final[Pubkey] = Pubkey.from_string("6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P")
TOKEN_PROGRAM_ID: Final[Pubkey] = Pubkey.from_string("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA")
BONDING_CURVE_DISCRIMINATOR_BYTES: Final[bytes] = bytes.fromhex("17b7f83760d8ac60")

async def get_bonding_curves_by_reserves(client: AsyncClient | None = None) -> list:
    """Fetch bonding curve accounts with real token reserves below threshold."""
    threshold: int = 100_000_000_000_000
    threshold_bytes: bytes = threshold.to_bytes(8, "little")
    msb_prefix: bytes = threshold_bytes[6:]

    should_close_client: bool = client is None
    try:
        if should_close_client:
            client = AsyncClient(RPC_ENDPOINT, commitment="processed", timeout=180)
            await client.is_connected()
            
        filters = [
            MemcmpOpts(offset=0, bytes=BONDING_CURVE_DISCRIMINATOR_BYTES),
            MemcmpOpts(offset=30, bytes=msb_prefix),
            MemcmpOpts(offset=48, bytes=b"\x00"),
        ]

        response = await client.get_program_accounts(
            PUMP_PROGRAM_ID,
            encoding="base64",
            filters=filters
        )

        result = []
        for acc in response.value:
            raw = acc.account.data
            real_token_reserves: int = struct.unpack("<Q", raw[24:32])[0]

            if real_token_reserves < threshold:
                result.append({
                    'pubkey': acc.pubkey,
                    'real_token_reserves': real_token_reserves
                })

        return result
    finally:
        if should_close_client and client:
            await client.close()



async def get_token_holders(mint_address: str, client: AsyncClient) -> list:
    """Get token largest accounts with detailed information."""
    try:
        # Get largest token accounts
        response = await client.get_token_largest_accounts(
            Pubkey.from_string(mint_address)
        )
        
        if not response.value:
            return []

        holders_data = []
        total_supply = 0
        
        # Calculate total supply first
        for account in response.value:
            if hasattr(account.amount, 'ui_amount') and account.amount.ui_amount:
                total_supply += account.amount.ui_amount

        # Process each holder
        for account in response.value:
            if hasattr(account.amount, 'ui_amount') and account.amount.ui_amount:
                percentage = (account.amount.ui_amount / total_supply * 100) if total_supply > 0 else 0
                
                # Only include holders with >1% of supply
                if percentage > 1:
                    holders_data.append({
                        "address": str(account.address),
                        "balance": account.amount.ui_amount,
                        "raw_balance": account.amount.amount,
                        "decimals": account.amount.decimals,
                        "percentage": percentage
                    })

        return sorted(holders_data, key=lambda x: x['balance'], reverse=True)

    except Exception as e:
        print(f"Error fetching token holders: {e}")
        return []

async def find_associated_bonding_curve(bonding_curve_address: str, client: AsyncClient) -> dict | None:
    """Find the SPL token account owned by a bonding curve."""
    try:
        response = await client.get_token_accounts_by_owner(
            Pubkey.from_string(bonding_curve_address),
            TokenAccountOpts(program_id=TOKEN_PROGRAM_ID)
        )
            
        if response.value and len(response.value) > 0:
            return response.value[0].account
        return None
    except Exception as e:
        print(f"Error finding associated token account: {e}")
        return None

def get_mint_address(data: bytes) -> str:
    """Extract mint address from SPL token account data."""
    return str(Pubkey(data[:32]))


# async def main() -> None:
#     """Main entry point for monitoring graduating tokens."""
#     # Define the JSON file path
#     json_file = "graduating_tokens_results.json"
    
#     # Load existing results if file exists
#     if os.path.exists(json_file):
#         with open(json_file, 'r') as f:
#             results = json.load(f)
#     else:
#         results = {"tokens": {}}
    

#     async with AsyncClient(RPC_ENDPOINT, commitment="processed", timeout=120) as client:
#         await client.is_connected()
        
#         while True:
#             try:
#                 bonding_curves = await get_bonding_curves_by_reserves(client)
                

#                 for curve in bonding_curves:
#                     bonding_curve_address = str(curve['pubkey'])
#                     associated_token_account = await find_associated_bonding_curve(
#                         str(curve['pubkey']), client
#                     )
                    
#                     if associated_token_account:
#                         # print(associated_token_account.data)
#                         # print(str(Pubkey(associated_token_account.data)))
#                         mint_address = get_mint_address(associated_token_account.data)
#                         print("\n=== NEW GRADUATING TOKEN DETECTED ===")
#                         print(f"Mint Address: {mint_address}")
#                         print(f"Bonding Curve: {curve['pubkey']}")
#                         print(f"Real token reserves: {curve['real_token_reserves'] / 10**6} tokens")

#                     # Get holder information directly using bonding curve address
#                     holders_data = []
#                     holders = await get_token_holders(bonding_curve_address, client)
                    
#                     if holders:  # Only proceed if we found holders
#                         print("\nTop Holders (>1%):")
#                         for holder in holders:
#                             holder_info = {
#                                 "address": str(holder['address']),
#                                 "balance": holder['balance'] / 10**6,
#                                 "percentage": holder['percentage']
#                             }
#                             holders_data.append(holder_info)
#                             print(f"Address: {holder['address']}")
#                             print(f"Balance: {holder['balance'] / 10**6:.2f} tokens")
#                             print(f"Percentage: {holder['percentage']:.2f}%")
#                             print("-" * 30)

#                     # Save to results dictionary using bonding curve address as key
#                     results["tokens"][mint_address] = {
#                         "detection_time": datetime.now().isoformat(),
#                         "real_token_reserves": curve['real_token_reserves'] / 10**6,
#                         "holders": holders_data
#                     }
                    
#                     # Save to file after each new token
#                     with open(json_file, 'w') as f:
#                         json.dump(results, f, indent=4)
                    
#                     print("=" * 50)

#                 await asyncio.sleep(30)
                
#             except Exception as e:
#                 print(f"Error occurred: {e}")
#                 # Even if there's an error, the JSON file will have saved the last successful state
#                 await asyncio.sleep(5)

# if __name__ == "__main__":
#     asyncio.run(main())

#latest working version
# async def main() -> None:
#     """Main entry point for monitoring graduating tokens."""
#         # Define the JSON file path
#     json_file = "graduating_tokens_results.json"
    
#     # Load existing results if file exists
#     if os.path.exists(json_file):
#         with open(json_file, 'r') as f:
#             results = json.load(f)
#     else:
#         results = {"tokens": {}}
    

#     async with AsyncClient(RPC_ENDPOINT, commitment="processed", timeout=120) as client:
#         await client.is_connected()
        
#         while True:
#             # try:
#             bonding_curves = await get_bonding_curves_by_reserves(client)
            
#             for curve in bonding_curves:
#                 associated_token_account = await find_associated_bonding_curve(
#                     str(curve['pubkey']), client
#                 )
                
#                 if associated_token_account:
#                     mint_address = get_mint_address(associated_token_account.data)
#                     bonding_curve = curve['pubkey']
#                     print("\n=== NEW GRADUATING TOKEN DETECTED ===")
#                     print(f"Mint Address: {mint_address}")
#                     print(f"Bonding Curve: {bonding_curve}")
#                     print(f"Real token reserves: {curve['real_token_reserves'] / 10**6} tokens")
                    
#                     print("\nTop Holders (>1%):")
#                     holders = await get_token_holders(mint_address, client)
#                     holders_data=[]
#                     if holders:
#                         for holder in holders:
#                             holder_info = {
#                                 "address": str(holder['address']),
#                                 "balance": holder['balance'] / 10**6,
#                                 "percentage": holder['percentage']
#                             }
#                             holders_data.append(holder_info)
#                             print(f"Address: {holder['address']}")
#                             print(f"Balance: {holder['balance'] / 10**6:.2f} tokens")
#                             print(f"Percentage: {holder['percentage']:.2f}%")
#                             print("-" * 30)

                    
#                     print("=" * 50)
#                     # Save to results dictionary using bonding curve address as key
#                     results["tokens"][mint_address] = {
#                         "detection_time": datetime.now().isoformat(),
#                         "bonding_curve":str(bonding_curve),
#                         "real_token_reserves": curve['real_token_reserves'] / 10**6,
#                         "holders": holders_data,
#                     }
#                     print(results["tokens"][mint_address])
                
#                     # Save to file after each new token
#                     with open(json_file, 'w') as f:
#                         json.dump(results, f, indent=4)
                
#                     print("=" * 50)

#             await asyncio.sleep(10)
            
#             # except Exception as e:
#             #     print(f"Error occurred: {e}")
#             #     # Even if there's an error, the JSON file will have saved the last successful state
#             #     await asyncio.sleep(10)
                

# if __name__ == "__main__":
#     asyncio.run(main())


async def main() -> None:
    """Main entry point for monitoring graduating tokens."""
    json_file = "graduating_tokens_results.json"
    
    if os.path.exists(json_file):
        with open(json_file, 'r') as f:
            results = json.load(f)
    else:
        results = {"tokens": {}}

    async with AsyncClient(RPC_ENDPOINT, commitment="processed", timeout=120) as client:
        await client.is_connected()
        
        while True:
            try:
                bonding_curves = await get_bonding_curves_by_reserves(client)
                
                for curve in bonding_curves:
                    associated_token_account = await find_associated_bonding_curve(
                        str(curve['pubkey']), client
                    )
                    
                    if associated_token_account:
                        mint_address = get_mint_address(associated_token_account.data)
                        bonding_curve = curve['pubkey']
                        
                        # Skip if we've already recorded this mint
                        if mint_address in results["tokens"]:
                            continue
                            
                        print("\n=== NEW GRADUATING TOKEN DETECTED ===")
                        print(f"Mint Address: {mint_address}")
                        print(f"Bonding Curve: {bonding_curve}")
                        print(f"Real token reserves: {curve['real_token_reserves'] / 10**6} tokens")
                        
                        print("\nTop Holders (>1%):")
                        holders = await get_token_holders(mint_address, client)
                        holders_data = []
                        
                        if holders:
                            for holder in holders:
                                holders_data.append({
                                    "address": holder['address'],
                                    "balance": holder['balance'],
                                    "raw_balance": holder['raw_balance'],
                                    "decimals": holder['decimals'],
                                    "percentage": holder['percentage']
                                })
                                
                                print(f"Address: {holder['address']}")
                                print(f"Balance: {holder['balance']:.2f} tokens")
                                print(f"Raw Balance: {holder['raw_balance']}")
                                print(f"Decimals: {holder['decimals']}")
                                print(f"Percentage: {holder['percentage']:.2f}%")
                                print("-" * 30)

                        # Save to results dictionary
                        results["tokens"][mint_address] = {
                            "detection_time": datetime.now().isoformat(),
                            "type":"ATG",
                            "bonding_curve": str(bonding_curve),
                            "real_token_reserves": curve['real_token_reserves'] / 10**6,
                            "holders": holders_data
                        }
                        
                        # Save to file after each new token
                        with open(json_file, 'w') as f:
                            json.dump(results, f, indent=4)
                        
                        print("=" * 50)
                await asyncio.sleep(15)
                
            except Exception as e:
                print(f"Error occurred: {e}")
                await asyncio.sleep(15)
if __name__ == "__main__":
    asyncio.run(main())
