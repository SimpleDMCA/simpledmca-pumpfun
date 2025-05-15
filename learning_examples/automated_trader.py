import asyncio
import os
import json
from datetime import datetime
from typing import Optional, Dict
from decimal import Decimal

from dotenv import load_dotenv
from solana.rpc.async_api import AsyncClient
from solders.keypair import Keypair
from solders.pubkey import Pubkey
import websockets

from src.core.wallet import Wallet
from src.core.curve import BondingCurveManager
from src.trading.buyer import TokenBuyer
from src.trading.seller import TokenSeller
from src.utils.logger import get_logger
from src.core.priority_fee import PriorityFeeManager

# Initialize logger
logger = get_logger(__name__)
load_dotenv()

# Constants
RPC_ENDPOINT = os.getenv("SOLANA_NODE_RPC_ENDPOINT")
WSS_ENDPOINT = os.getenv("SOLANA_NODE_WSS_ENDPOINT")
PRIVATE_KEY = os.getenv("SOLANA_PRIVATE_KEY")
PUMP_PROGRAM_ID = Pubkey.from_string("6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P")
MIGRATION_PROGRAM_ID = Pubkey.from_string("PumpSwapMigrationProgram111111111111111111")

class TokenPosition:
    def __init__(self, token_address: str, entry_price: float, amount: float, entry_time: datetime):
        self.token_address = token_address
        self.entry_price = entry_price
        self.amount = amount
        self.entry_time = entry_time
        self.current_price = entry_price
        self.profit_loss = 0.0
        self.last_updated = entry_time

class PositionManager:
    def __init__(self):
        self.positions: Dict[str, TokenPosition] = {}
        self.PROFIT_TARGET = 0.20  # 20% profit target
        self.STOP_LOSS = -0.50    # 50% stop loss
        
    def add_position(self, token_address: str, entry_price: float, amount: float):
        self.positions[token_address] = TokenPosition(
            token_address=token_address,
            entry_price=entry_price,
            amount=amount,
            entry_time=datetime.now()
        )
        self.save_positions()

    def update_position(self, token_address: str, current_price: float) -> Optional[str]:
        """
        Updates position and returns 'sell' if selling criteria met, None otherwise
        """
        if token_address not in self.positions:
            return None

        position = self.positions[token_address]
        position.current_price = current_price
        position.profit_loss = (current_price - position.entry_price) / position.entry_price
        position.last_updated = datetime.now()

        self.save_positions()

        if position.profit_loss >= self.PROFIT_TARGET:
            return "sell_profit"
        elif position.profit_loss <= self.STOP_LOSS:
            return "sell_loss"
        return None

    def save_positions(self):
        """Save positions to JSON file"""
        positions_data = {
            addr: {
                "entry_price": pos.entry_price,
                "current_price": pos.current_price,
                "amount": pos.amount,
                "profit_loss": pos.profit_loss,
                "entry_time": pos.entry_time.isoformat(),
                "last_updated": pos.last_updated.isoformat()
            } for addr, pos in self.positions.items()
        }
        
        with open('token_positions.json', 'w') as f:
            json.dump(positions_data, f, indent=4)

class TokenTrader:
    def __init__(self):
        self.client = AsyncClient(RPC_ENDPOINT)
        self.wallet = Wallet(PRIVATE_KEY)
        self.curve_manager = BondingCurveManager(self.client)
        self.priority_fee_manager = PriorityFeeManager(self.client)
        
        self.buyer = TokenBuyer(
            client=self.client,
            wallet=self.wallet,
            curve_manager=self.curve_manager,
            priority_fee_manager=self.priority_fee_manager,
            amount=0.1,  # SOL amount
            slippage=0.02,
            max_retries=5
        )
        
        self.seller = TokenSeller(
            client=self.client,
            wallet=self.wallet,
            priority_fee_manager=self.priority_fee_manager,
            slippage=0.02,
            max_retries=5
        )
        
        self.position_manager = PositionManager()
        self.processed_migrations = set()

    async def monitor_token_price(self, token_address: str):
        """Monitor token price and update position"""
        while token_address in self.position_manager.positions:
            try:
                # Get current price (implement based on your price source)
                current_price = await self.get_token_price(token_address)
                
                if current_price:
                    action = self.position_manager.update_position(token_address, current_price)
                    
                    if action:
                        position = self.position_manager.positions[token_address]
                        
                        if action.startswith("sell"):
                            logger.info(f"Selling {token_address} - Reason: {action}")
                            
                            # Execute sell
                            result = await self.seller.execute(
                                token_address=token_address,
                                amount=position.amount
                            )
                            
                            if result.success:
                                logger.info(f"Successfully sold {token_address}")
                                del self.position_manager.positions[token_address]
                                self.position_manager.save_positions()
                                return
                            else:
                                logger.error(f"Failed to sell {token_address}: {result.error_message}")
                
                await asyncio.sleep(10)  # Price check interval
                
            except Exception as e:
                logger.error(f"Error monitoring {token_address}: {str(e)}")
                await asyncio.sleep(30)

    async def listen_for_migrations(self):
        """Listen for migration events using WebSocket"""
        while True:
            try:
                async with websockets.connect(WSS_ENDPOINT) as websocket:
                    subscription = {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "logsSubscribe",
                        "params": [
                            {"mentions": [str(MIGRATION_PROGRAM_ID)]},
                            {"commitment": "confirmed"}
                        ]
                    }
                    await websocket.send(json.dumps(subscription))
                    logger.info("Subscribed to migration program logs")

                    while True:
                        try:
                            msg = await websocket.recv()
                            data = json.loads(msg)
                            
                            if "result" in data and "value" in data["result"]:
                                await self.process_migration_event(data["result"]["value"])
                                
                        except websockets.exceptions.ConnectionClosed:
                            logger.warning("WebSocket connection closed. Reconnecting...")
                            break
                            
            except Exception as e:
                logger.error(f"Error in migration listener: {str(e)}")
                await asyncio.sleep(5)

    async def process_migration_event(self, event_data):
        """Process migration event and execute buy if needed"""
        try:
            token_address = self.extract_token_address(event_data)
            
            if not token_address or token_address in self.processed_migrations:
                return

            logger.info(f"New migration detected for token: {token_address}")
            
            # Get token information and execute buy
            token_info = await self.get_token_info(token_address)
            
            if token_info:
                result = await self.buyer.execute(token_info)
                
                if result.success:
                    logger.info(f"Successfully bought {token_address}")
                    
                    # Add position
                    entry_price = await self.get_token_price(token_address)
                    self.position_manager.add_position(
                        token_address=token_address,
                        entry_price=entry_price,
                        amount=result.amount_received
                    )
                    
                    # Start monitoring position
                    asyncio.create_task(self.monitor_token_price(token_address))
                    
                    self.processed_migrations.add(token_address)
                else:
                    logger.error(f"Failed to buy {token_address}: {result.error_message}")

        except Exception as e:
            logger.error(f"Error processing migration event: {str(e)}")

    async def get_token_price(self, token_address: str) -> Optional[float]:
        """Get current token price"""
        # Implement based on your price source
        # This is a placeholder
        try:
            # Get price from your preferred source
            return 0.0
        except Exception as e:
            logger.error(f"Error getting price for {token_address}: {str(e)}")
            return None

    def extract_token_address(self, event_data) -> Optional[str]:
        """Extract token address from event data"""
        try:
            # Implement based on your event structure
            return None
        except Exception as e:
            logger.error(f"Error extracting token address: {str(e)}")
            return None

    async def get_token_info(self, token_address: str):
        """Get token information for buying"""
        try:
            # Implement token info retrieval
            return None
        except Exception as e:
            logger.error(f"Error getting token info: {str(e)}")
            return None

async def main():
    trader = TokenTrader()
    
    try:
        logger.info("Starting token trader...")
        await trader.listen_for_migrations()
    except KeyboardInterrupt:
        logger.info("Shutting down token trader...")
    except Exception as e:
        logger.error(f"Unexpected error: {str(e)}")
    finally:
        await trader.client.close()

if __name__ == "__main__":
    asyncio.run(main())