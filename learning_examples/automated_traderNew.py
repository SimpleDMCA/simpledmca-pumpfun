import asyncio
import base64
import json
import os
import struct
import time
from typing import Optional, Dict
import base58
import websockets
from dotenv import load_dotenv
from solana.rpc.async_api import AsyncClient
from solders.keypair import Keypair
from solders.pubkey import Pubkey
from spl.token.instructions import get_associated_token_address

from core.wallet import Wallet
from utils.logger import get_logger
from core.pubkeys import LAMPORTS_PER_SOL, SystemAddresses

from pumpswap.manual_buy_pumpswap import (
    get_market_address_by_base_mint,
    get_market_data,
    find_coin_creator_vault,
    buy_pump_swap,
    PUMP_AMM_PROGRAM_ID,
    SLIPPAGE,
)

# Load environment variables
load_dotenv()

# Constants
WSS_ENDPOINT = os.getenv("SOLANA_NODE_WSS_ENDPOINT")
RPC_ENDPOINT = os.getenv("RPC_ENDPOINT")
PUMP_PROGRAM_ID = Pubkey.from_string("6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P")
INITIAL_INVESTMENT = 0.2 * LAMPORTS_PER_SOL
PRIVATE_KEY = "2xcEU5Dgukd4m6aBfXUFh74TZ3mPPsQiQxWTHAr9edmyvB3bvXKMbsfDUrmEMd4qM8RdNhMt2ag7hYbAB5eT7sz8"
MIGRATION_PROGRAM_ID = Pubkey.from_string("39azUYFWPz3VHgKCf3VChUwbpURdCHRxjWVowf5jUJjg")


logger = get_logger(__name__)

class TokenTrader:
    def __init__(self, simulation_mode: bool = False):
        self.simulation_mode = simulation_mode
        self.client = AsyncClient(RPC_ENDPOINT)
        self.wallet = Wallet(PRIVATE_KEY)
        self.token_queue = asyncio.Queue()
        self.is_listening = True
    
    def parse_migrate_instruction(self,data):
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


    def is_transaction_successful(self,logs):
        for log in logs:
            if "AnchorError thrown" in log or "Error" in log:
                print(f"[ERROR] Transaction failed: {log}")
                return False
        return True
    
    def print_transaction_details(self, log_data):
        logs = log_data.get("logs", [])
        parsed_data = {}

        for log in logs:
            if log.startswith("Program data:"):
                try:
                    data = base64.b64decode(log.split(": ")[1])
                    parsed_data = self.parse_migrate_instruction(data)
                    if parsed_data:
                        print("[INFO] Parsed from Program data:")
                        for key, value in parsed_data.items():
                            print(f"  {key}: {value}")
                except Exception as e:
                    print(f"[ERROR] Failed to decode Program data: {e}")

        if not parsed_data:
            print("[ERROR] Failed to parse migration data: parsed data is empty")


    async def listen_for_migrations(self):
        while True:
            try:
                print("\n[INFO] Connecting to WebSocket ...")
                async with websockets.connect(WSS_ENDPOINT) as websocket:
                    subscription_message = json.dumps(
                        {
                            "jsonrpc": "2.0",
                            "id": 1,
                            "method": "logsSubscribe",
                            "params": [
                                {"mentions": [str(MIGRATION_PROGRAM_ID)]},
                                {"commitment": "processed"},
                            ],
                        }
                    )
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
                                # print("Printing Log_data and log_data.get")
                                # print(log_data)
                                # print("="*50)
                                # print(log_data.get("logs", []))
                                logs = log_data.get("logs", [])

                                signature = log_data.get('signature', 'N/A')
                                print(f"\n[INFO] Transaction signature: {signature}")

                                if self.is_transaction_successful(logs):
                                    if not any("Program log: Instruction: Migrate" in log for log in logs):
                                        print("[INFO] Skipping: no migrate instruction")
                                        continue

                                    if any("Program log: Bonding curve already migrated" in log for log in logs):
                                        print("[INFO] Skipping: bonding curve already migrated")
                                        continue

                                    print("[INFO] Processing migration instruction...")
                                    self.print_transaction_details(log_data)
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


    async def process_tokens(self):
        """Process tokens from the queue"""
        logger.info("Starting token processor...")
        
        while True:
            try:
                # Wait for new tokens in the queue
                token_data = await self.token_queue.get()
                
                if token_data is None:
                    continue

                await self.execute_trade(token_data)
                
            except Exception as e:
                logger.error(f"Error processing token: {e}")
            finally:
                self.token_queue.task_done()

    async def execute_trade(self, token_data: dict):
        """Execute a trade for a new token"""
        print('Executing Trade running Now.....')
        time.sleep(2)
        return None

        try:
            token_mint = token_data['mint']
            logger.info(f"Processing trade for {token_data['name']} ({token_data['symbol']})")

            if self.simulation_mode:
                logger.info(f"[SIMULATION] Would execute buy for {token_mint}")
                return

            # Get PumpSwap market data
            token_mint_pubkey = Pubkey.from_string(token_mint)
            market_address = await get_market_address_by_base_mint(
                self.client, 
                token_mint_pubkey, 
                PUMP_AMM_PROGRAM_ID
            )
            
            market_data = await get_market_data(self.client, market_address)
            coin_creator_vault_authority = find_coin_creator_vault(
                Pubkey.from_string(market_data["coin_creator"])
            )
            coin_creator_vault_ata = get_associated_token_address(
                coin_creator_vault_authority, 
                SystemAddresses.SOL
            )

            # Execute buy using PumpSwap
            tx_hash = await buy_pump_swap(
                self.client,
                market_address,
                self.wallet.keypair,
                token_mint_pubkey,
                get_associated_token_address(self.wallet.pubkey(), token_mint_pubkey),
                get_associated_token_address(self.wallet.pubkey(), SystemAddresses.SOL),
                Pubkey.from_string(market_data["pool_base_token_account"]),
                Pubkey.from_string(market_data["pool_quote_token_account"]),
                coin_creator_vault_authority,
                coin_creator_vault_ata,
                INITIAL_INVESTMENT,
                SLIPPAGE
            )

            if tx_hash:
                logger.info(f"Successfully bought {token_data['name']}")
                logger.info(f"Transaction hash: {tx_hash}")
            else:
                logger.error(f"Failed to buy {token_data['name']}")

        except Exception as e:
            logger.error(f"Error executing trade: {str(e)}")

    async def start(self):
        """Start the token trader"""
        try:
            # Create tasks for listening and processing
            listener = asyncio.create_task(self.listen_for_migrations())
            processor = asyncio.create_task(self.process_tokens())
            
            # Wait for both tasks
            await asyncio.gather(listener, processor)
            
        except KeyboardInterrupt:
            logger.info("Shutting down token trader...")
            self.is_listening = False
        except Exception as e:
            logger.error(f"Unexpected error: {str(e)}")
        finally:
            await self.client.close()

async def main():
    # Create trader instance (set simulation_mode=False for live trading)
    trader = TokenTrader(simulation_mode=True)
    await trader.start()

if __name__ == "__main__":
    asyncio.run(main())