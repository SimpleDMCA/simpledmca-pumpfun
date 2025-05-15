import asyncio
import base64
import hashlib
import json
import os
import struct
from datetime import datetime
from dotenv import load_dotenv
from typing import Optional, Dict

import base58
import websockets
from solana.rpc.async_api import AsyncClient
from solana.rpc.commitment import Confirmed
from solana.rpc.types import TxOpts
from solders.compute_budget import set_compute_unit_price
from solders.instruction import AccountMeta, Instruction
from solders.keypair import Keypair
from solders.message import Message
from solders.pubkey import Pubkey
from solders.transaction import Transaction, VersionedTransaction
from spl.token.instructions import get_associated_token_address
import spl.token.instructions as spl_token
from construct import Bytes, Flag, Int64ul, Struct

load_dotenv()

# Constants from manual_buy.py and manual_sell.py
EXPECTED_DISCRIMINATOR = struct.pack("<Q", 6966180631402821399)
TOKEN_DECIMALS = 6

# Global constants
PUMP_PROGRAM = Pubkey.from_string("6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P")
PUMP_GLOBAL = Pubkey.from_string("4wTV1YmiEkRvAtNtsSGPtUrqRYQMe5SKy2uB4Jjaxnjf")
PUMP_EVENT_AUTHORITY = Pubkey.from_string("Ce6TQqeHC9p8KetsN6JsjHK7UTZk7nasjjnr7XxXp9F1")
PUMP_FEE = Pubkey.from_string("CebN5WGQ4jvEPvsVU4EoHEpgzq1VV7AbicfhtW4xC9iM")
SYSTEM_PROGRAM = Pubkey.from_string("11111111111111111111111111111111")
SYSTEM_TOKEN_PROGRAM = Pubkey.from_string("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA")
SYSTEM_ASSOCIATED_TOKEN_ACCOUNT_PROGRAM = Pubkey.from_string("ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL")
SOL = Pubkey.from_string("So11111111111111111111111111111111111111112")
LAMPORTS_PER_SOL = 1_000_000_000

# Trading parameters
STOP_LOSS_PERCENTAGE = 0.50  # 50% loss
TAKE_PROFIT_PERCENTAGE = 0.20  # 20% gain
SLIPPAGE = 0.25  # 25% slippage tolerance
INITIAL_INVESTMENT = 0.001  # Amount of SOL to invest per trade

# RPC endpoints
RPC_ENDPOINT = os.environ.get("SOLANA_NODE_RPC_ENDPOINT")
RPC_WEBSOCKET = os.environ.get("SOLANA_NODE_WSS_ENDPOINT")
SOLANA_PRIVATE_KEY="2xcEU5Dgukd4m6aBfXUFh74TZ3mPPsQiQxWTHAr9edmyvB3bvXKMbsfDUrmEMd4qM8RdNhMt2ag7hYbAB5eT7sz8"

WS_URL = "wss://pumpportal.fun/api/data"

class BondingCurveState:
    _STRUCT = Struct(
        "virtual_token_reserves" / Int64ul,
        "virtual_sol_reserves" / Int64ul,
        "real_token_reserves" / Int64ul,
        "real_sol_reserves" / Int64ul,
        "token_total_supply" / Int64ul,
        "complete" / Flag,
        "creator" / Bytes(32),
    )

    def __init__(self, data: bytes) -> None:
        if data[:8] != EXPECTED_DISCRIMINATOR:
            raise ValueError("Invalid curve state discriminator")

        parsed = self._STRUCT.parse(data[8:])
        self.__dict__.update(parsed)
        
        if hasattr(self, 'creator') and isinstance(self.creator, bytes):
            self.creator = Pubkey.from_bytes(self.creator)

class Trade:
    def __init__(self, token_info: dict, entry_price: float, amount: float):
        self.token_info = token_info
        self.entry_price = entry_price
        self.amount = amount
        self.entry_time = datetime.now()
        self.exit_price: Optional[float] = None
        self.exit_time: Optional[datetime] = None
        self.pnl: Optional[float] = None

class AutomatedTrader:
    def __init__(self, simulation_mode: bool = False):
        self.simulation_mode = simulation_mode
        self.active_trades: Dict[str, Trade] = {}
        self.completed_trades: list[Trade] = []
        self.total_pnl = 0.0
        self.client = AsyncClient(os.getenv("SOLANA_NODE_RPC_ENDPOINT"))
        
        # Load wallet
        private_key = os.getenv("SOLANA_PRIVATE_KEY")
        if private_key:
            self.wallet = Keypair.from_base58_string(private_key)
        else:
            raise ValueError("SOLANA_PRIVATE_KEY environment variable not set")

    async def execute_buy(self, token_info: dict) -> bool:
        if self.simulation_mode:
            print(f"[SIMULATION] Executing buy for token {token_info['mint']}")
            return True

        try:
            await buy_token(
                mint=Pubkey.from_string(token_info['mint']),
                bonding_curve=Pubkey.from_string(token_info['bondingCurveKey']),
                associated_bonding_curve=Pubkey.from_string(token_info['associatedBondingCurve']),
                creator_vault=Pubkey.from_string(token_info['traderPublicKey']),
                amount=INITIAL_INVESTMENT,
                slippage=SLIPPAGE
            )
            return True
        except Exception as e:
            print(f"Buy execution failed: {e}")
            return False

    async def execute_sell(self, token_info: dict) -> bool:
        if self.simulation_mode:
            print(f"[SIMULATION] Executing sell for token {token_info['mint']}")
            return True

        try:
            await sell_token(
                mint=Pubkey.from_string(token_info['mint']),
                bonding_curve=Pubkey.from_string(token_info['bondingCurveKey']),
                associated_bonding_curve=Pubkey.from_string(token_info['associatedBondingCurve']),
                creator_vault=Pubkey.from_string(token_info['traderPublicKey']),
                slippage=SLIPPAGE
            )
            return True
        except Exception as e:
            print(f"Sell execution failed: {e}")
            return False

    def calculate_price(self, token_info: dict) -> float:
        v_sol = token_info.get('vSolInBondingCurve', 0)
        v_tokens = token_info.get('vTokensInBondingCurve', 0)
        if v_tokens <= 0:
            return 0
        return v_sol / v_tokens

    async def monitor_price(self, token_info: dict, entry_price: float):
        mint = token_info['mint']
        while mint in self.active_trades:
            try:
                # In a real implementation, you would fetch the current price from the blockchain
                # For simulation, we'll use a simple price calculation
                current_price = self.calculate_price(token_info)
                price_change = (current_price - entry_price) / entry_price
                
                if price_change <= -STOP_LOSS_PERCENTAGE:
                    print(f"Stop loss triggered at {price_change*100:.2f}% loss")
                    return "sell"
                elif price_change >= TAKE_PROFIT_PERCENTAGE:
                    print(f"Take profit triggered at {price_change*100:.2f}% gain")
                    return "sell"
                
                print(f"Current price: {current_price:.8f} SOL (Change: {price_change*100:.2f}%)")
                await asyncio.sleep(1)
            
            except Exception as e:
                print(f"Error monitoring price: {e}")
                await asyncio.sleep(1)

    async def process_new_token(self, token_info: dict):
        mint = token_info['mint']
        print(f"\nProcessing new token: {token_info.get('name')} ({token_info.get('symbol')})")
        
        # Calculate entry price
        entry_price = self.calculate_price(token_info)
        print(f"Initial price: {entry_price:.8f} SOL")

        # Execute buy
        if await self.execute_buy(token_info):
            trade = Trade(token_info, entry_price, INITIAL_INVESTMENT)
            self.active_trades[mint] = trade
            
            # Monitor price and execute sell if needed
            action = await self.monitor_price(token_info, entry_price)
            
            if action == "sell":
                if await self.execute_sell(token_info):
                    # Update trade information
                    trade = self.active_trades.pop(mint)
                    trade.exit_time = datetime.now()
                    trade.exit_price = entry_price * (1 + (TAKE_PROFIT_PERCENTAGE if trade.exit_price > trade.entry_price else -STOP_LOSS_PERCENTAGE))
                    trade.pnl = (trade.exit_price - trade.entry_price) * trade.amount
                    self.completed_trades.append(trade)
                    self.total_pnl += trade.pnl
                    
                    print(f"Trade completed - PnL: {trade.pnl:.8f} SOL")
                    print(f"Total PnL: {self.total_pnl:.8f} SOL")

    async def start(self):
        print(f"Starting automated trader in {'simulation' if self.simulation_mode else 'live'} mode")
        
        async with websockets.connect(WS_URL) as websocket:
            # Subscribe to new token events
            await websocket.send(json.dumps({"method": "subscribeNewToken", "params": []}))
            print("Listening for new token creations...")

            while True:
                try:
                    message = await websocket.recv()
                    data = json.loads(message)

                    if "method" in data and data["method"] == "newToken":
                        token_info = data.get("params", [{}])[0]
                    elif "signature" in data and "mint" in data:
                        token_info = data
                    else:
                        continue

                    await self.process_new_token(token_info)

                except websockets.exceptions.ConnectionClosed:
                    print("\nWebSocket connection closed. Reconnecting...")
                    break
                except Exception as e:
                    print(f"Error in main loop: {e}")
                    await asyncio.sleep(1)

async def main():
    # Create trader instance
    trader = AutomatedTrader(simulation_mode=True)  # Set to False for live trading
    
    while True:
        try:
            await trader.start()
        except Exception as e:
            print(f"Connection error: {e}")
            print("Reconnecting in 5 seconds...")
            await asyncio.sleep(5)

if __name__ == "__main__":
    asyncio.run(main())