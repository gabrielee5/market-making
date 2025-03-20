#!/usr/bin/env python3
"""
Hyperliquid Market Maker - Launcher Script
This script initializes and starts the Hyperliquid market maker
"""

import os
import asyncio
import logging
import argparse
from dotenv import load_dotenv
from hyperliquid_mm import HyperliquidMarketMaker

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger('market_maker_launcher')

async def main():
    """Main function to parse arguments and start the market maker"""
    parser = argparse.ArgumentParser(description='Hyperliquid Market Maker')
    
    parser.add_argument(
        '-c', '--config',
        type=str,
        default='config.json',
        help='Path to configuration file (default: config.json)'
    )
    
    parser.add_argument(
        '-t', '--testnet',
        action='store_true',
        help='Use testnet instead of mainnet'
    )
    
    parser.add_argument(
        '-v', '--verbose',
        action='store_true',
        help='Enable verbose debug logging'
    )
    
    args = parser.parse_args()
    
    # Load environment variables
    load_dotenv()
    
    # Set log level based on verbosity flag
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
        logger.debug("Verbose logging enabled")
    
    # Override testnet setting if specified
    if args.testnet:
        logger.info("Testnet mode enabled via command line")
        os.environ['USE_TESTNET'] = 'true'
    
    # Set config path in environment for the market maker to use
    os.environ['CONFIG_PATH'] = args.config
    
    try:
        logger.info(f"Starting market maker with config: {args.config}")
        
        # Create market maker instance
        market_maker = HyperliquidMarketMaker(args.config)
        
        # Run the market maker
        await market_maker.run()
        
    except Exception as e:
        logger.error(f"Error starting market maker: {str(e)}")
        raise
    finally:
        # Make sure we have a clean exit
        logger.info("Market maker shutdown complete")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Market maker stopped by user")
    except Exception as e:
        logger.error(f"Market maker stopped due to error: {str(e)}")