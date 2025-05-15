# Add these new imports at the top
import sys
sys.path.append('/Users/sterling/Documents/simpleDmca/PumpFun/pump-fun-bot/src')

from dataclasses import dataclass
from enum import Enum
import csv
from pathlib import Path
import asyncio
import os
import json
from time import monotonic
from datetime import datetime
from typing import Optional, Dict
from decimal import Decimal

from dotenv import load_dotenv
from solana.rpc.async_api import AsyncClient
from solders.keypair import Keypair
from solders.pubkey import Pubkey
import websockets

from core.client import SolanaClient
from core.wallet import Wallet
from core.curve import BondingCurveManager
from core.pubkeys import PumpAddresses
from core.priority_fee.manager import PriorityFeeManager

from trading.buyer import TokenBuyer
from trading.seller import TokenSeller
from trading.base import TokenInfo,TradeResult, Trader

from utils.logger import get_logger


from monitoring.logs_listener import LogsListener



logger = get_logger(__name__)
load_dotenv()


# Constants
RPC_ENDPOINT = os.getenv("SOLANA_NODE_RPC_ENDPOINT")
WSS_ENDPOINT = os.getenv("SOLANA_NODE_WSS_ENDPOINT")
PRIVATE_KEY = "2xcEU5Dgukd4m6aBfXUFh74TZ3mPPsQiQxWTHAr9edmyvB3bvXKMbsfDUrmEMd4qM8RdNhMt2ag7hYbAB5eT7sz8"
PUMP_PROGRAM_ID = Pubkey.from_string("6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P")
MIGRATION_PROGRAM_ID = Pubkey.from_string("39azUYFWPz3VHgKCf3VChUwbpURdCHRxjWVowf5jUJjg")


class TokenTrader(Trader):
    """Coordinates trading operations for pump.fun tokens."""
    
    def __init__(
        self,
        rpc_endpoint: str,
        wss_endpoint: str,
        private_key: str,
        buy_amount: float,
        buy_slippage: float = 0.25,
        sell_slippage: float = 0.25,
        max_retries: int = 3,
        wait_time_after_creation: int = 15,
        wait_time_after_buy: int = 15,
        max_token_age: float = 0.001,
    ):
        """Initialize the token trader.
        
        Args:
            rpc_endpoint: RPC endpoint URL
            wss_endpoint: WebSocket endpoint URL
            private_key: Wallet private key
            buy_amount: Amount of SOL to spend on buys
            buy_slippage: Slippage tolerance for buys
            sell_slippage: Slippage tolerance for sells
            max_retries: Maximum number of retry attempts
            wait_time_after_creation: Time to wait after token creation
            wait_time_after_buy: Time to wait after buying before selling
            max_token_age: Maximum age of token to process
        """
        # Initialize core components
        self.client = SolanaClient(rpc_endpoint)
        self.wallet = Wallet(private_key)
        self.curve_manager = BondingCurveManager(self.client)
        
        # Initialize priority fee manager
        self.priority_fee_manager = PriorityFeeManager(
            client=self.client,
            enable_dynamic_fee=False,
            enable_fixed_fee=True,
            fixed_fee=200_000,
            extra_fee=0.0,
            hard_cap=200_000
        )

        # Initialize buyer and seller
        self.buyer = TokenBuyer(
            self.client,
            self.wallet,
            self.curve_manager,
            self.priority_fee_manager,
            buy_amount,
            buy_slippage,
            max_retries
        )
        
        self.seller = TokenSeller(
            self.client,
            self.wallet,
            self.curve_manager,
            self.priority_fee_manager,
            sell_slippage,
            max_retries
        )

        # Initialize token listener
        self.token_listener = LogsListener(wss_endpoint, PumpAddresses.PROGRAM)

        # Trading parameters
        self.wait_time_after_creation = wait_time_after_creation
        self.wait_time_after_buy = wait_time_after_buy
        self.max_token_age = max_token_age
        
        # State tracking
        self.traded_mints: set[Pubkey] = set()
        self.processed_tokens: set[str] = set()
        self.token_timestamps: dict[str, float] = {}

    async def start(self) -> None:
        """Start the trading bot and listen for new tokens."""
        logger.info("Starting token trader")
        
        try:
            # Warm up RPC connection
            health_resp = await self.client.get_health()
            logger.info(f"RPC warm-up successful (getHealth passed: {health_resp})")
        except Exception as e:
            logger.warning(f"RPC warm-up failed: {e!s}")

        try:
            await self.token_listener.listen_for_tokens(
                self._handle_token,
                match_string=None,
                creator_address=None
            )
        except Exception as e:
            logger.error(f"Trading stopped due to error: {e!s}")
        finally:
            await self._cleanup_resources()


    async def _handle_token(self, token_info: TokenInfo) -> None:
        """Handle a new token creation event.
        
        Args:
            token_info: Token information
        """
        try:
            token_key = str(token_info.mint)
            
            # Skip if already processed
            if token_key in self.processed_tokens:
                return

            # Check token age
            current_time = monotonic()
            token_age = current_time - self.token_timestamps.get(token_key, current_time)
            
            if token_age > self.max_token_age:
                logger.info(f"Skipping {token_info.symbol} - too old ({token_age:.1f}s)")
                return

            self.processed_tokens.add(token_key)
            logger.info(f"Processing fresh token: {token_info.symbol} (age: {token_age:.1f}s)")

            # Wait for bonding curve to stabilize
            logger.info(f"Waiting {self.wait_time_after_creation}s for curve stabilization...")
            await asyncio.sleep(self.wait_time_after_creation)

            # Execute buy
            logger.info(f"Buying {token_info.symbol}...")
            buy_result: TradeResult = await self.buyer.execute(token_info)

            if buy_result.success:
                await self._handle_successful_buy(token_info, buy_result)
            else:
                logger.error(f"Failed to buy {token_info.symbol}: {buy_result.error_message}")

        except Exception as e:
            logger.error(f"Error handling token {token_info.symbol}: {e!s}")

    async def _handle_successful_buy(
        self,
        token_info: TokenInfo,
        buy_result: TradeResult
    ) -> None:
        """Handle successful token purchase.
        
        Args:
            token_info: Token information
            buy_result: The result of the buy operation
        """
        logger.info(f"Successfully bought {token_info.symbol}")
        self._log_trade(
            "buy",
            token_info,
            buy_result.price,
            buy_result.amount,
            buy_result.tx_signature
        )
        self.traded_mints.add(token_info.mint)

        # Execute sell
        logger.info(f"Waiting {self.wait_time_after_buy}s before selling...")
        await asyncio.sleep(self.wait_time_after_buy)

        logger.info(f"Selling {token_info.symbol}...")
        sell_result: TradeResult = await self.seller.execute(token_info)

        if sell_result.success:
            logger.info(f"Successfully sold {token_info.symbol}")
            self._log_trade(
                "sell",
                token_info,
                sell_result.price,
                sell_result.amount,
                sell_result.tx_signature
            )
        else:
            logger.error(f"Failed to sell {token_info.symbol}: {sell_result.error_message}")

    async def _cleanup_resources(self) -> None:
        """Clean up resources before shutting down."""
        # Clean up old timestamps
        old_keys = {k for k in self.token_timestamps if k not in self.processed_tokens}
        for key in old_keys:
            self.token_timestamps.pop(key, None)
            
        await self.client.close()
        logger.info("Token trader has shut down")

    def _log_trade(
        self,
        action: str,
        token_info: TokenInfo,
        price: float,
        amount: float,
        tx_hash: str | None,
    ) -> None:
        """Log trade information.
        
        Args:
            action: Trade action (buy/sell)
            token_info: Token information
            price: Token price in SOL
            amount: Trade amount
            tx_hash: Transaction hash
        """
        try:
            os.makedirs("trades", exist_ok=True)
            
            log_entry = {
                "timestamp": datetime.utcnow().isoformat(),
                "action": action,
                "token_address": str(token_info.mint),
                "symbol": token_info.symbol,
                "price": price,
                "amount": amount,
                "tx_hash": str(tx_hash) if tx_hash else None,
            }

            with open("trades/trades.log", "a") as log_file:
                log_file.write(json.dumps(log_entry) + "\n")
        except Exception as e:
            logger.error(f"Failed to log trade information: {e!s}")

if __name__ == "__main__":
    # Example usage
    trader = TokenTrader(
        rpc_endpoint=RPC_ENDPOINT,
        wss_endpoint=WSS_ENDPOINT,
        private_key=PRIVATE_KEY,
        buy_amount=0.1,  # Amount in SOL
        buy_slippage=0.25,
        sell_slippage=0.25,
        max_retries=3,
        wait_time_after_creation=15,
        wait_time_after_buy=15,
        max_token_age=0.001
    )
    
    asyncio.run(trader.start())