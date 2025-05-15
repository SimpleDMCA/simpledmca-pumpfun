import asyncio
import base64
import json
import os
from datetime import datetime
from typing import Optional, Dict

from dotenv import load_dotenv
from solana.rpc.async_api import AsyncClient
from solders.keypair import Keypair
from solders.pubkey import Pubkey
import websockets

from core.wallet import Wallet
from core.curve import BondingCurveManager
from trading.buyer import TokenBuyer
from trading.seller import TokenSeller
from utils.logger import get_logger
from core.priority_fee.manager import PriorityFeeManager
from core.pubkeys import LAMPORTS_PER_SOL, SystemAddresses, PumpAddresses

import sys
sys.path.append('/Users/sterling/Documents/simpleDmca/simpledmca-pumpfun/learning_examples')

from listen_migrations.listen_logsubscribe2 import listen_for_migrations

# Initialize logger and load environment variables
logger = get_logger(__name__)
load_dotenv()


RPC_ENDPOINT = os.getenv("SOLANA_NODE_RPC_ENDPOINT")
WSS_ENDPOINT = os.getenv("SOLANA_NODE_WSS_ENDPOINT")
PRIVATE_KEY = "2xcEU5Dgukd4m6aBfXUFh74TZ3mPPsQiQxWTHAr9edmyvB3bvXKMbsfDUrmEMd4qM8RdNhMt2ag7hYbAB5eT7sz8"
PUMP_PROGRAM_ID = Pubkey.from_string("6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P")
MIGRATION_PROGRAM_ID = Pubkey.from_string("39azUYFWPz3VHgKCf3VChUwbpURdCHRxjWVowf5jUJjg")



# Trading parameters
STOP_LOSS_PERCENTAGE = 0.50  # 50% loss
TAKE_PROFIT_PERCENTAGE = 0.20  # 20% gain
SLIPPAGE = 0.1  # 10% slippage
INITIAL_INVESTMENT = 0.2  # SOL amount to invest per trade

class TokenPosition:
    def __init__(self, token_address: str, entry_price: float, amount: float):
        self.token_address = token_address
        self.entry_price = entry_price
        self.amount = amount
        self.entry_time = datetime.now()
        self.current_price = entry_price
        self.profit_loss = 0.0
        self.last_updated = datetime.now()

class AutomatedTrader:
    def __init__(self, simulation_mode: bool = False):
        self.simulation_mode = simulation_mode
        self.positions: Dict[str, TokenPosition] = {}
        self.completed_trades: list[TokenPosition] = []
        self.total_pnl = 0.0
        
        # Initialize Solana client and wallet
        self.client = AsyncClient(os.getenv("SOLANA_NODE_RPC_ENDPOINT"))
        self.wallet = Wallet(os.getenv("SOLANA_PRIVATE_KEY"))
        
        # Initialize managers
        self.curve_manager = BondingCurveManager(self.client)
        self.priority_fee_manager = PriorityFeeManager(
            client=self.client,
            enable_dynamic_fee=False,
            enable_fixed_fee=True,
            fixed_fee=200_000,
            extra_fee=0.0,
            hard_cap=200_000
        )
        
        # Initialize trading components
        self.buyer = TokenBuyer(
            client=self.client,
            wallet=self.wallet,
            curve_manager=self.curve_manager,
            priority_fee_manager=self.priority_fee_manager,
            amount=INITIAL_INVESTMENT,
            slippage=SLIPPAGE,
            max_retries=5
        )
        
        self.seller = TokenSeller(
            client=self.client,
            wallet=self.wallet,
            curve_manager=self.curve_manager,
            priority_fee_manager=self.priority_fee_manager,
            slippage=SLIPPAGE,
            max_retries=5
        )

    def parse_migrate_instruction(data):
        if len(data) < 8:
            print(f"[ERROR] Data length too short: {len(data)} bytes")
            return None


    def process_transaction_details(self,log_data):
        """Process and return parsed transaction details"""
        logs = log_data.get("logs", [])
        parsed_data = None

        for log in logs:
            if log.startswith("Program data:"):
                try:
                    data = base64.b64decode(log.split(": ")[1])
                    parsed_data = self.parse_migrate_instruction(data)
                    if parsed_data:
                        return parsed_data
                except Exception as e:
                    print(f"[ERROR] Failed to decode Program data: {e}")

        return None

    async def process_graduation_event(self, event_data: dict):
        """Process a token graduation event"""
        try:
            # Extract relevant data from the graduation event
            token_address = event_data.get("token")
            if not token_address or token_address in self.positions:
                return

            logger.info(f"New token graduation detected: {token_address}")

            if self.simulation_mode:
                logger.info(f"[SIMULATION] Would execute buy for {token_address}")
                return

            # Execute buy
            result = await self.buyer.execute(token_address)
            
            if result.success:
                entry_price = await self.get_token_price(token_address)
                position = TokenPosition(token_address, entry_price, result.amount_received)
                self.positions[token_address] = position
                
                logger.info(f"Successfully bought {token_address} at {entry_price} SOL")
                
                # Start monitoring position
                asyncio.create_task(self.monitor_position(token_address))
            else:
                logger.error(f"Failed to buy {token_address}: {result.error_message}")

        except Exception as e:
            logger.error(f"Error processing graduation event: {str(e)}")

    async def monitor_position(self, token_address: str):
        """Monitor a position for take profit or stop loss"""
        while token_address in self.positions:
            try:
                position = self.positions[token_address]
                current_price = await self.get_token_price(token_address)
                
                if current_price is None:
                    await asyncio.sleep(5)
                    continue

                price_change = (current_price - position.entry_price) / position.entry_price
                position.current_price = current_price
                position.profit_loss = price_change
                position.last_updated = datetime.now()

                logger.info(f"Position {token_address}: Price change {price_change*100:.2f}%")

                if price_change <= -STOP_LOSS_PERCENTAGE or price_change >= TAKE_PROFIT_PERCENTAGE:
                    await self.close_position(token_address, current_price)
                    break

                await asyncio.sleep(10)  # Check every 10 seconds

            except Exception as e:
                logger.error(f"Error monitoring position {token_address}: {str(e)}")
                await asyncio.sleep(5)

    async def close_position(self, token_address: str, exit_price: float):
        """Close a position and record the trade"""
        try:
            position = self.positions[token_address]
            
            if self.simulation_mode:
                logger.info(f"[SIMULATION] Would close position for {token_address}")
            else:
                result = await self.seller.execute(token_address)
                
                if not result.success:
                    logger.error(f"Failed to sell {token_address}: {result.error_message}")
                    return

            # Record trade
            position.current_price = exit_price
            position.profit_loss = (exit_price - position.entry_price) / position.entry_price
            self.completed_trades.append(position)
            self.total_pnl += position.profit_loss * position.amount * LAMPORTS_PER_SOL
            
            del self.positions[token_address]
            
            logger.info(f"Closed position {token_address} with PnL: {position.profit_loss*100:.2f}%")
            logger.info(f"Total PnL: {self.total_pnl/LAMPORTS_PER_SOL:.4f} SOL")

        except Exception as e:
            logger.error(f"Error closing position {token_address}: {str(e)}")

    async def get_token_price(self, token_address: str) -> Optional[float]:
        """Get current token price"""
        try:
            price = await self.curve_manager.get_token_price(token_address)
            return float(price) / LAMPORTS_PER_SOL
        except Exception as e:
            logger.error(f"Error getting price for {token_address}: {str(e)}")
            return None

    # async def listen_for_graduations(self):
    #     """Listen for token graduation events"""
    #     while True:
    #         try:
    #             print("\n[INFO] Connecting to WebSocket ...")
    #             async with websockets.connect(WSS_ENDPOINT) as websocket:
    #                 subscription_message = json.dumps({
    #                     "jsonrpc": "2.0",
    #                     "id": 1,
    #                     "method": "logsSubscribe",
    #                     "params": [
    #                         {"mentions": [str(MIGRATION_PROGRAM_ID)]},
    #                         {"commitment": "processed"},
    #                     ],
    #                 })
    #                 await websocket.send(subscription_message)
    #                 print(f"Listening for graduation events from program: {MIGRATION_PROGRAM_ID}")

    #                 # Handle subscription confirmation
    #                 response = await websocket.recv()
    #                 data = json.loads(response)
    #                 if "error" in data:
    #                     raise Exception(f"Subscription failed: {data['error']}")
                    
    #                 while True:
    #                     try:
    #                         msg = await websocket.recv()
    #                         data = json.loads(msg)
                            
    #                         # Check if this is a notification message
    #                         if "method" in data and data["method"] == "logsNotification":
    #                             result = data.get("params", {}).get("result", {})
    #                             if isinstance(result, dict) and "value" in result:
    #                                 log_data = result["value"]
    #                                 logs = log_data.get("logs", [])
                                    
    #                                 signature = log_data.get('signature', 'N/A')
    #                                 logger.info(f"Processing transaction {signature}")

    #                                 if self.is_valid_graduation(logs):
    #                                     await self.process_graduation_event(log_data)
                                        
    #                     except websockets.exceptions.ConnectionClosed:
    #                         logger.warning("WebSocket connection closed")
    #                         break
    #                     except json.JSONDecodeError as e:
    #                         logger.error(f"Failed to parse message: {e}")
    #                     except Exception as e:
    #                         logger.error(f"Error processing message: {e}")
    #                         continue

    #         except Exception as e:
    #             logger.error(f"WebSocket error: {e}")
    #             logger.info("Reconnecting in 5 seconds...")
    #             await asyncio.sleep(5)

    # def is_transaction_successful(logs):
    #     for log in logs:
    #         if "AnchorError thrown" in log or "Error" in log:
    #             print(f"[ERROR] Transaction failed: {log}")
    #             return False
    #     return True

    async def listen_for_migrations(self):
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

                                # # Skip if we've already processed this transaction
                                # if signature in results["migrations"]:
                                #     print(f"[INFO] Skipping: already processed transaction {signature}")
                                #     continue

                                print(f"\n[INFO] Transaction signature: {signature}")

                                if self.is_valid_graduation(logs):
                                    await self.process_graduation_event(log_data)

                                    if not any("Program log: Instruction: Migrate" in log for log in logs):
                                        print("[INFO] Skipping: no migrate instruction")
                                        continue

                                    if any("Program log: Bonding curve already migrated" in log for log in logs):
                                        print("[INFO] Skipping: bonding curve already migrated")
                                        continue

                                    print("[INFO] Processing migration instruction...")
                                    parsed_data = self.process_transaction_details(log_data)
                                    
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
                                        print(migration_entry)

                                        # Update results
                                        # results["migrations"][signature] = migration_entry
                                        # results["last_updated"] = datetime.now().isoformat()
                                        
                                        # # Save to file
                                        # save_results(results)
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
                
    def is_valid_graduation(self, logs: list) -> bool:
        """Check if the logs indicate a valid graduation event"""
        if not logs:
            return False
            
        # Check for successful transaction
        if any("Error" in log for log in logs):
            return False
            
        # Check for migration instruction
        if not any("Program log: Instruction: Migrate" in log for log in logs):
            return False
            
        # Check if already migrated
        if any("Program log: Bonding curve already migrated" in log for log in logs):
            return False
            
        return True
    
    async def start(self):
        """Start the automated trader"""
        logger.info(f"Starting automated trader in {'simulation' if self.simulation_mode else 'live'} mode")
        
        try:
            await listen_for_migrations()
        except KeyboardInterrupt:
            logger.info("Shutting down automated trader")
        except Exception as e:
            logger.error(f"Unexpected error: {str(e)}")
        finally:
            await self.client.close()

async def main():
    # Create trader instance (set simulation_mode=False for live trading)
    trader = AutomatedTrader(simulation_mode=True)
    await trader.start()

if __name__ == "__main__":
    asyncio.run(main())