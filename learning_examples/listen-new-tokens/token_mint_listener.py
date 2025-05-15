"""
Listen to transactions for a specific token mint using logsSubscribe.
"""

import asyncio
import json
import os
from typing import Optional

import websockets
from dotenv import load_dotenv
from solders.pubkey import Pubkey

# from utils.logger import get_logger
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

load_dotenv()

WSS_ENDPOINT = os.environ.get("SOLANA_NODE_WSS_ENDPOINT")

class TokenMintListener:
    def __init__(self, wss_endpoint: str, token_mint: Pubkey):
        """Initialize token mint listener.
        
        Args:
            wss_endpoint: WebSocket endpoint URL
            token_mint: Token mint address to monitor
        """
        self.wss_endpoint = wss_endpoint
        self.token_mint = token_mint
        self.ping_interval = 1  # seconds

    async def _subscribe_to_logs(self, websocket) -> None:
        """Subscribe to logs mentioning the token mint.
        
        Args:
            websocket: Active WebSocket connection
        """
        subscription_message = json.dumps({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "logsSubscribe",
            "params": [
                {"mentions": [str(self.token_mint)]},
                {"commitment": "confirmed"}
            ]
        })

        await websocket.send(subscription_message)
        logger.info(f"Subscribed to logs mentioning token mint: {self.token_mint}")

        # Wait for subscription confirmation
        response = await websocket.recv()
        response_data = json.loads(response)
        if "result" in response_data:
            logger.info(f"Subscription confirmed with ID: {response_data['result']}")
        else:
            logger.warning(f"Unexpected subscription response: {response}")

    async def _ping_loop(self, websocket) -> None:
        """Keep connection alive with pings.
        
        Args:
            websocket: Active WebSocket connection
        """
        try:
            while True:
                await asyncio.sleep(self.ping_interval)
                try:
                    pong_waiter = await websocket.ping()
                    await asyncio.wait_for(pong_waiter, timeout=10)
                except asyncio.TimeoutError:
                    logger.warning("Ping timeout - server not responding")
                    await websocket.close()
                    return
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Ping error: {str(e)}")

    async def _process_transaction(self, log_data: dict) -> None:
        """Process transaction data from logs.
        
        Args:
            log_data: Transaction log data
        """
        try:
            signature = log_data.get("signature", "unknown")
            logs = log_data.get("logs", [])

            # Print transaction details
            logger.info("=" * 80)
            logger.info(f"Transaction signature: {signature}")
            
            # Extract and log relevant information
            for log in logs:
                if str(self.token_mint) in log:
                    logger.info(f"Log: {log}")
                    
                # You can add more specific parsing based on your needs
                if "Program log:" in log:
                    logger.info(f"Program log: {log}")
                    
            logger.info("=" * 80)

        except Exception as e:
            logger.error(f"Error processing transaction: {str(e)}")

    async def listen(self) -> None:
        """Start listening for transactions involving the token mint."""
        while True:
            try:
                async with websockets.connect(self.wss_endpoint) as websocket:
                    await self._subscribe_to_logs(websocket)
                    ping_task = asyncio.create_task(self._ping_loop(websocket))

                    try:
                        # while True:
                        response = await websocket.recv()
                        data = json.loads(response)

                        if "method" in data and data["method"] == "logsNotification":
                            log_data = data["params"]["result"]["value"]
                            await self._process_transaction(log_data)

                    except websockets.exceptions.ConnectionClosed:
                        logger.warning("WebSocket connection closed. Reconnecting...")
                        ping_task.cancel()

            except Exception as e:
                logger.error(f"WebSocket connection error: {str(e)}")
                logger.info("Reconnecting in 5 seconds...")
                await asyncio.sleep(5)

async def main():
    # Replace with your token mint address
    token_mint = Pubkey.from_string("C2KsTjzyYUWUfUfFBFfPL914xCtyxiQcCDZvpZy6pump")
    
    print('initializing listener')
    listener = TokenMintListener(
        wss_endpoint=WSS_ENDPOINT,
        token_mint=token_mint
    )
    print('initialized listener')
    
    logger.info(f"Starting to monitor transactions for token mint: {token_mint}")
    await listener.listen()

if __name__ == "__main__":
    asyncio.run(main())