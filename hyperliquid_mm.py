# Basic Market Making Infrastructure for Hyperliquid using CCXT
import ccxt.async_support as ccxt_async  # Use the async version of CCXT
import time
import os
import logging
import asyncio
from dotenv import load_dotenv
from typing import Dict, List, Any, Tuple
import json

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger('hyperliquid_mm')

class HyperliquidMarketMaker:
    def __init__(self, config_path: str = 'config.json'):
        """
        Initialize the market maker with configuration
        
        Args:
            config_path: Path to the configuration JSON file
        """
        # Load configuration
        with open(config_path, 'r') as f:
            self.config = json.load(f)
        
        # Initialize exchange connection - delay actual connection until run()
        self.exchange = None
        
        # Track open orders and positions
        self.open_orders = []
        self.current_position = {"size": 0, "side": "flat", "unrealized_pnl": 0}
        
        # Performance tracking
        self.start_balance = 0
        self.current_balance = 0
        self.trades_executed = 0
        
        # Flag to track connection status
        self.is_connected = False
        
    async def _initialize_exchange(self) -> ccxt_async.Exchange:
        """Initialize and return the CCXT exchange object with enhanced error handling"""
        if self.exchange:
            # Close existing connection first
            await self.exchange.close()
            
        retry_count = 0
        max_retries = 3
        
        while retry_count < max_retries:
            try:
                # Create exchange instance
                exchange_id = self.config["exchange"]["name"]
                exchange_class = getattr(ccxt_async, exchange_id)
                
                # Check for required API keys
                wallet_address = os.getenv('HYPERLIQUID_WALLET')
                private_key = os.getenv('HYPERLIQUID_SECRET_KEY')
                
                if not wallet_address or not private_key:
                    logger.error("Missing required environment variables: HYPERLIQUID_WALLET and/or HYPERLIQUID_SECRET_KEY")
                    raise ValueError("Missing API credentials")
                
                exchange = exchange_class({
                    'walletAddress': wallet_address,
                    'privateKey': private_key,
                    'enableRateLimit': True,
                    'options': {
                        'defaultType': 'swap',  # For futures/perpetual swaps
                        'adjustForTimeDifference': True,
                    }
                })
                
                # Use testnet if configured
                if self.config["exchange"]["testnet"]:
                    exchange.set_sandbox_mode(True)
                    logger.info("Using TESTNET mode")
                
                # Load markets to get trading pairs info with timeout handling
                try:
                    await exchange.load_markets()
                    logger.info(f"Connected to {exchange.name}")
                    
                    available_markets = list(exchange.markets.keys())
                    perpetual_markets = [market for market in available_markets if market.endswith(':USDC')]
                    logger.info(f"Found {len(perpetual_markets)} perpetual markets")
                    logger.info(f"Available markets (first 5): {perpetual_markets[:5]}")
                except Exception as market_error:
                    logger.error(f"Failed to load markets: {str(market_error)}")
                    # We can continue without markets loaded, but it may cause issues later
                
                # Get initial account balance
                try:
                    balance = await exchange.fetch_balance()
                    usdc_balance = self._get_usdt_balance(balance)
                    
                    if usdc_balance is not None:
                        self.start_balance = usdc_balance
                        self.current_balance = self.start_balance
                        logger.info(f"Initial balance: {self.start_balance} USDC")
                    else:
                        logger.warning("Could not find USDC balance")
                        self.start_balance = 0
                        self.current_balance = 0
                except Exception as balance_error:
                    logger.warning(f"Could not fetch balance: {str(balance_error)}")
                    self.start_balance = 0
                    self.current_balance = 0
                
                self.is_connected = True
                return exchange
                
            except ccxt_async.NetworkError as e:
                retry_count += 1
                wait_time = 2 ** retry_count  # Exponential backoff
                logger.warning(f"Network error: {str(e)}. Retrying in {wait_time} seconds... (Attempt {retry_count}/{max_retries})")
                await asyncio.sleep(wait_time)
            except ccxt_async.ExchangeError as e:
                retry_count += 1
                wait_time = 2 ** retry_count
                logger.warning(f"Exchange error: {str(e)}. Retrying in {wait_time} seconds... (Attempt {retry_count}/{max_retries})")
                await asyncio.sleep(wait_time)
            except Exception as e:
                logger.error(f"Critical error initializing exchange: {str(e)}")
                raise
        
        # If we've exhausted retries
        logger.error(f"Failed to initialize exchange after {max_retries} attempts")
        raise ConnectionError(f"Could not connect to {self.config['exchange']['name']} exchange")

    def _get_usdt_balance(self, balance: Dict) -> float:
        """Extract USDC balance from the account balance response with error handling"""
        try:
            if balance is None:
                return 0.0
                
            if 'USDC' in balance.get('total', {}):
                usdc_val = balance['total']['USDC']
                return float(usdc_val) if usdc_val is not None else 0.0
            elif 'USDC' in balance.get('free', {}):
                usdc_val = balance['free']['USDC']
                return float(usdc_val) if usdc_val is not None else 0.0
            
            # If we couldn't find USDC, try with USDT as fallback
            if 'USDT' in balance.get('total', {}):
                usdt_val = balance['total']['USDT']
                return float(usdt_val) if usdt_val is not None else 0.0
            elif 'USDT' in balance.get('free', {}):
                usdt_val = balance['free']['USDT']
                return float(usdt_val) if usdt_val is not None else 0.0
                
            return 0.0
        except Exception as e:
            logger.error(f"Error parsing balance: {str(e)}")
            return 0.0
            
    async def initialize_markets(self):
        """Load available markets and find the correct symbol format"""
        try:
            if not self.exchange:
                logger.error("Exchange not initialized")
                return None
                
            # Get all available markets
            markets = self.exchange.markets
            
            # Get the configured symbol from config (possibly in wrong format)
            config_symbol = self.config["trading"]["symbol"]
            
            # Try to find the correct symbol format
            symbol_key = None
            
            # Look for direct match first
            if config_symbol in markets:
                symbol_key = config_symbol
                logger.info(f"Found exact symbol match: {symbol_key}")
            else:
                # Try different variations (forward slash vs dash)
                alt_symbol = config_symbol.replace('/', '-')
                if alt_symbol in markets:
                    symbol_key = alt_symbol
                    logger.info(f"Found alternative symbol format: {symbol_key}")
                else:
                    alt_symbol = config_symbol.replace('-', '/')
                    if alt_symbol in markets:
                        symbol_key = alt_symbol
                        logger.info(f"Found alternative symbol format: {symbol_key}")
                
                # Check for base/quote variations
                if not symbol_key:
                    base, quote = config_symbol.replace('-', '/').split('/')
                    for key in markets:
                        market_base = markets[key].get('base', '')
                        market_quote = markets[key].get('quote', '')
                        if (base == market_base and quote == market_quote) or f"{market_base}-{market_quote}" == config_symbol:
                            symbol_key = key
                            logger.info(f"Found symbol by base/quote match: {symbol_key}")
                            break
            
            if not symbol_key:
                # List available symbols
                available_symbols = list(markets.keys())
                logger.error(f"Symbol {config_symbol} not found. Available symbols: {available_symbols[:10]}...")
                raise ValueError(f"Could not find trading symbol {config_symbol} on Hyperliquid")
                
            # Update the config with the correct symbol format
            self.config["trading"]["symbol"] = symbol_key
            logger.info(f"Using symbol: {symbol_key}")
            
            return symbol_key
            
        except Exception as e:
            logger.error(f"Error initializing markets: {str(e)}")
            raise

    def get_symbol_config(self, symbol: str) -> dict:
        """
        Get trading configuration for a specific symbol
        
        Args:
            symbol: Trading pair symbol
            
        Returns:
            Dictionary with trading parameters for the symbol
        """
        # Check if symbol exists in the configuration
        symbol_config = None
        
        # Find the config for this symbol in the symbols array
        if "symbols" in self.config:
            for s in self.config["symbols"]:
                if s.get("symbol") == symbol:
                    symbol_config = s
                    break
        
        # If we don't have a specific config for this symbol, or symbols array doesn't exist
        if symbol_config is None:
            # Check if we have a legacy trading config or fallback to default params
            if "trading" in self.config:
                # Legacy config format
                return self.config["trading"]
            elif "defaultTradingParams" in self.config:
                # Use default params
                return self.config["defaultTradingParams"]
            else:
                # Last resort - return a minimal set of defaults
                logger.warning(f"No configuration found for symbol {symbol}, using hardcoded defaults")
                return {
                    "orderSize": 0.01,
                    "maxOrderSize": 0.05,
                    "minOrderSize": 0.001,
                    "maxPositionSize": 0.1,
                    "leverageLevel": 1
                }
        
        # Merge with default params to ensure all required parameters exist
        if "defaultTradingParams" in self.config:
            # Create a new dict with defaults, then update with symbol-specific values
            merged_config = self.config["defaultTradingParams"].copy()
            merged_config.update(symbol_config)
            return merged_config
        
        # If no defaults exist, just return the symbol config
        return symbol_config            

    async def initialize_markets_for_symbol(self, symbol: str) -> str:
        """
        Load available markets and find the correct symbol format for a specific symbol
        
        Args:
            symbol: Symbol to initialize
            
        Returns:
            Validated symbol string
        """
        try:
            if not self.exchange:
                logger.error("Exchange not initialized")
                return symbol
                
            # Get all available markets
            markets = self.exchange.markets
            
            # Try to find the correct symbol format
            symbol_key = None
            
            # Look for direct match first
            if symbol in markets:
                symbol_key = symbol
                logger.info(f"Found exact symbol match: {symbol_key}")
            else:
                # Try different variations (forward slash vs dash)
                alt_symbol = symbol.replace('/', '-')
                if alt_symbol in markets:
                    symbol_key = alt_symbol
                    logger.info(f"Found alternative symbol format: {symbol_key}")
                else:
                    alt_symbol = symbol.replace('-', '/')
                    if alt_symbol in markets:
                        symbol_key = alt_symbol
                        logger.info(f"Found alternative symbol format: {symbol_key}")
                
                # Check for base/quote variations
                if not symbol_key:
                    base, quote = symbol.replace('-', '/').split('/')
                    for key in markets:
                        market_base = markets[key].get('base', '')
                        market_quote = markets[key].get('quote', '')
                        if (base == market_base and quote == market_quote) or f"{market_base}-{market_quote}" == symbol:
                            symbol_key = key
                            logger.info(f"Found symbol by base/quote match: {symbol_key}")
                            break
            
            if not symbol_key:
                # List available symbols
                available_symbols = list(markets.keys())
                logger.error(f"Symbol {symbol} not found. Available symbols: {available_symbols[:10]}...")
                raise ValueError(f"Could not find trading symbol {symbol} on Hyperliquid")
            
            return symbol_key
            
        except Exception as e:
            logger.error(f"Error initializing market for {symbol}: {str(e)}")
            raise

    async def fetch_market_data(self, symbol: str) -> Dict[str, Any]:
        """
        Fetch market data from the exchange
        
        Args:
            symbol: Trading pair symbol (e.g., 'BTC/USDC')
            
        Returns:
            Dictionary containing orderbook, trades, and ticker data
        """
        if not self.exchange:
            logger.error("Exchange not initialized")
            return {
                "orderbook": {"bids": [], "asks": []},
                "trades": [],
                "ticker": {"bid": 0, "ask": 0, "last": 0}
            }
            
        try:
            # Get order book
            orderbook_depth = self.config["marketMaking"]["orderBookDepth"]
            orderbook = await self.exchange.fetch_order_book(symbol, orderbook_depth)
            
            # Get recent trades
            trades = await self.exchange.fetch_trades(symbol, None, 50)  # Last 50 trades
            
            # Get ticker for latest price info
            ticker = await self.exchange.fetch_ticker(symbol)
            
            return {
                "orderbook": orderbook,
                "trades": trades,
                "ticker": ticker
            }
        except Exception as e:
            logger.error(f"Error fetching market data: {str(e)}")
            # Return minimal structure to prevent downstream errors
            return {
                "orderbook": {"bids": [], "asks": []},
                "trades": [],
                "ticker": {"bid": 0, "ask": 0, "last": 0}
            }
    
    def calculate_prices(self, market_data: Dict[str, Any]) -> Dict[str, float]:
        """
        Calculate optimal bid/ask prices based on order book and strategy
        
        Args:
            market_data: Market data dictionary
            
        Returns:
            Dictionary with calculated prices
        """
        # Safely get orderbook and ticker with defaults
        orderbook = market_data.get("orderbook", {"bids": [], "asks": []})
        if not orderbook:
            orderbook = {"bids": [], "asks": []}
            
        ticker = market_data.get("ticker", {"bid": 0, "ask": 0, "last": 0})
        if not ticker:
            ticker = {"bid": 0, "ask": 0, "last": 0}
        
        # Get bids and asks safely
        bids = orderbook.get("bids", [])
        asks = orderbook.get("asks", [])
        
        # Calculate mid price with safety checks
        best_bid = bids[0][0] if len(bids) > 0 else ticker.get("bid", 0)
        best_ask = asks[0][0] if len(asks) > 0 else ticker.get("ask", 0)
        
        # Handle potential None values
        if best_bid is None:
            best_bid = ticker.get("last", 0) * 0.99 if ticker.get("last") else 0
        if best_ask is None:
            best_ask = ticker.get("last", 0) * 1.01 if ticker.get("last") else 0
        
        # Handle zero or None values
        if not best_bid or not best_ask:
            logger.warning("Invalid orderbook data - using last price from ticker")
            last_price = ticker.get("last", 0)
            if not last_price:
                logger.error("No valid price data available")
                return {
                    "mid_price": 0,
                    "bid_price": 0,
                    "ask_price": 0,
                    "spread_amount": 0,
                    "volatility_adjustment": 0
                }
            best_bid = last_price * 0.99  # Fallback values
            best_ask = last_price * 1.01
            
        mid_price = (best_bid + best_ask) / 2
        
        # Calculate spread based on configuration
        spread_percentage = self.config["marketMaking"]["spreadPercentage"]
        spread_amount = mid_price * (spread_percentage / 100)
        
        # Set bid and ask prices
        bid_price = mid_price - (spread_amount / 2)
        ask_price = mid_price + (spread_amount / 2)
        
        # Adjust based on recent volatility (simplified approach) - FIX HERE
        percentage = ticker.get("percentage", 0)
        if percentage is None:
            percentage = 0
        volatility_adjustment = abs(percentage) * 0.1 if "percentage" in ticker else 0
        
        # Adapt spread based on orderbook depth and imbalance with safety checks
        try:
            # Ensure we have bids and asks to calculate depth
            bids_to_use = orderbook.get("bids", [])[:5] if orderbook.get("bids") else []
            asks_to_use = orderbook.get("asks", [])[:5] if orderbook.get("asks") else []
            
            # Calculate depths safely
            bid_depth = sum([amount for price, amount in bids_to_use if amount is not None])
            ask_depth = sum([amount for price, amount in asks_to_use if amount is not None])
        except Exception as e:
            logger.warning(f"Error calculating orderbook depth: {str(e)}")
            bid_depth = 1.0
            ask_depth = 1.0
        
        # If more buying than selling pressure, tighten bid and widen ask
        if bid_depth > ask_depth * 1.5:
            bid_price = mid_price - (spread_amount / 2.5)
            ask_price = mid_price + (spread_amount / 1.5)
        # If more selling than buying pressure, tighten ask and widen bid
        elif ask_depth > bid_depth * 1.5:
            bid_price = mid_price - (spread_amount / 1.5)
            ask_price = mid_price + (spread_amount / 2.5)
        
        # Make sure prices are valid numbers and properly formatted
        price_precision = self.price_precision(ticker)
        bid_price = round(max(0.00001, bid_price * (1 - volatility_adjustment)), price_precision)
        ask_price = round(max(0.00001, ask_price * (1 + volatility_adjustment)), price_precision)
        
        return {
            "mid_price": round(mid_price, price_precision),
            "bid_price": bid_price,
            "ask_price": ask_price,
            "spread_amount": round(spread_amount, price_precision),
            "volatility_adjustment": volatility_adjustment
        }
    
    def price_precision(self, ticker: Dict[str, Any]) -> int:
        """
        Determine price precision based on ticker information from Hyperliquid
        
        Args:
            ticker: Dictionary containing ticker information
            
        Returns:
            int: Number of decimal places for price precision
        """
        try:
            # For Hyperliquid, precision information is in the ticker's info dictionary
            # The price precision isn't directly provided, but we can extract it from
            # the actual prices by looking at their decimal places
            
            # Try to get the most precise price value available
            mark_price = ticker.get('markPrice') or ticker.get('info', {}).get('markPx')
            mid_price = ticker.get('last') or ticker.get('info', {}).get('midPx')
            oracle_price = ticker.get('info', {}).get('oraclePx')
            
            # Use the first non-None price we find
            price_str = None
            for price in [mark_price, mid_price, oracle_price]:
                if price is not None:
                    price_str = str(price)
                    break
            
            if price_str and '.' in price_str:
                # Count decimal places
                return len(price_str.split('.')[-1])
            
            # If we can't determine precision from prices, try to find it in the info dictionary
            if ticker.get('info') and 'szDecimals' in ticker['info']:
                # As a fallback, we can use szDecimals (size decimals)
                # For most trading pairs, price precision is typically higher than size precision
                return int(ticker['info']['szDecimals']) + 1
            
            # Default precision if we can't determine it
            return 1
        except Exception as e:
            # Log the error and return a safe default
            logger.warning(f"Error determining price precision: {str(e)}")
            return 1
    
    async def get_current_position(self, symbol: str) -> Dict[str, Any]:
        """
        Get current position for a symbol with improved error handling
        
        Args:
            symbol: Trading pair symbol
            
        Returns:
            Position details with safe defaults
        """
        if not self.exchange:
            logger.error("Exchange not initialized")
            return {"size": 0, "side": "flat", "unrealized_pnl": 0}
            
        try:
            positions = await self.exchange.fetch_positions([symbol])
            if positions and len(positions) > 0:
                position = positions[0]
                # Extract values with safe defaults
                contracts = position.get("contracts", 0)
                side = position.get("side", "flat")
                unrealized_pnl = position.get("unrealizedPnl", 0)
                
                # Convert to appropriate types with error handling
                try:
                    size = float(contracts) if contracts is not None else 0
                except (ValueError, TypeError):
                    logger.warning(f"Invalid position size value: {contracts}")
                    size = 0
                    
                try:
                    pnl = float(unrealized_pnl) if unrealized_pnl is not None else 0
                except (ValueError, TypeError):
                    logger.warning(f"Invalid PnL value: {unrealized_pnl}")
                    pnl = 0
                    
                return {
                    "size": size,
                    "side": side if side is not None else "flat",
                    "unrealized_pnl": pnl
                }
            return {"size": 0, "side": "flat", "unrealized_pnl": 0}
        except Exception as e:
            logger.error(f"Error fetching positions: {str(e)}")
            return {"size": 0, "side": "flat", "unrealized_pnl": 0}
    
    async def place_market_making_orders_batch(self, symbol: str, prices: Dict[str, float], position: Dict[str, Any], symbol_config: Dict[str, Any] = None) -> List[Dict]:
        """
        Place market making orders with batch operation for faster execution
        
        Args:
            symbol: Trading pair symbol
            prices: Calculated prices
            position: Current position
            symbol_config: Configuration for this specific symbol
            
        Returns:
            List of placed orders
        """
        if not self.exchange:
            logger.error("Exchange not initialized")
            return []
            
        # If no symbol config provided, get it
        if symbol_config is None:
            symbol_config = self.get_symbol_config(symbol)
            
        # Skip if prices are zeros or invalid
        if prices["bid_price"] <= 0 or prices["ask_price"] <= 0:
            logger.warning("Invalid prices - skipping order placement")
            return []
            
        try:
            # Cancel existing orders to avoid conflicts
            await self.cancel_all_orders(symbol)
            
            # Safe extraction of position data with defaults
            position_size = float(position.get("size", 0)) if position.get("size") is not None else 0
            position_side = position.get("side", "flat")
            if position_side is None:
                position_side = "flat"
            
            # Get configured order size with validation from symbol-specific config
            try:
                base_order_size = float(symbol_config.get("orderSize", 0.01))
                if base_order_size <= 0:
                    logger.warning(f"Invalid order size in config for {symbol}, using default")
                    base_order_size = 0.01
            except (ValueError, TypeError, KeyError):
                logger.warning(f"Error reading order size from config for {symbol}, using default")
                base_order_size = 0.01
                
            try:
                max_position_size = float(symbol_config.get("maxPositionSize", 0.1))
                if max_position_size <= 0:
                    logger.warning(f"Invalid max position size in config for {symbol}, using default")
                    max_position_size = 0.1
            except (ValueError, TypeError, KeyError):
                logger.warning(f"Error reading max position size from config for {symbol}, using default")
                max_position_size = 0.1
            
            # Adjust order sizes based on current position
            bid_size = base_order_size
            ask_size = base_order_size
            
            if position_side == "long" and position_size >= max_position_size:
                bid_size = 0  # Don't buy more if we're at max long position
            elif position_side == "short" and position_size >= max_position_size:
                ask_size = 0  # Don't sell more if we're at max short position
            
            # Placement layers configuration
            try:
                layers = int(self.config["marketMaking"].get("placementLayers", 3))
                if layers <= 0:
                    layers = 1
            except (ValueError, TypeError):
                logger.warning("Invalid layers setting, using default")
                layers = 1
                
            try:
                multiplier = float(self.config["marketMaking"].get("layerSpreadMultiplier", 1.5))
                if multiplier <= 1:
                    multiplier = 1.5
            except (ValueError, TypeError):
                logger.warning("Invalid multiplier setting, using default")
                multiplier = 1.5

            try:
                # Get price_step from symbol-specific config instead of marketMaking section
                price_step = float(symbol_config.get("price_step", 0.1))
                if price_step <= 0:
                    logger.warning(f"Invalid price_step value ({price_step}) for {symbol}, using default")
                    price_step = 0.1
            except (ValueError, TypeError):
                logger.warning(f"Invalid price_step setting for {symbol}, using default")
                price_step = 0.1
            
            # Prepare batch orders
            batch_orders = []
            
            # Try to get price precision for rounding
            try:
                ticker = await self.exchange.fetch_ticker(symbol)
                price_precision = self.price_precision(ticker)
            except Exception:
                price_precision = 2  # Default precision if unable to determine
            
            # Generate Fibonacci sequence for order size multipliers
            fibonacci_sequence = [1, 1, 2, 3, 5, 8, 13, 21]  # Start with the first two numbers
            for i in range(2, layers + 1):
                fibonacci_sequence.append(fibonacci_sequence[i-1] + fibonacci_sequence[i-2])
            
            # Add bid orders to batch
            if bid_size > 0 and prices["bid_price"] > 0:
                for i in range(layers):
                    # layer_bid_price = round(prices["bid_price"] * (1 - (i * (multiplier - 1) / 100)), price_precision)
                    layer_bid_price = round(prices["bid_price"] * (1 - (price_step /100 * fibonacci_sequence[i])), price_precision)
                    
                    # Use Fibonacci multiplier for order size
                    # First layer (i=0) is the base_order_size
                    layer_bid_size = bid_size * fibonacci_sequence[i]
                    
                    try:
                        min_order_size = float(symbol_config.get("minOrderSize", 0.001))
                    except (ValueError, TypeError):
                        min_order_size = 0.001
                    
                    if layer_bid_size >= min_order_size:
                        batch_orders.append({
                            "symbol": symbol,
                            "type": "limit",
                            "side": "buy",
                            "amount": layer_bid_size,
                            "price": layer_bid_price,
                            "params": {
                                "timeInForce": "Gtc",
                                "postOnly": True
                            }
                        })
                        logger.debug(f"Added bid order to batch: {layer_bid_size} @ {layer_bid_price}")
            
            # Add ask orders to batch
            if ask_size > 0 and prices["ask_price"] > 0:
                for i in range(layers):
                    # layer_ask_price = round(prices["ask_price"] * (1 + (i * (multiplier - 1) / 100)), price_precision)
                    layer_ask_price = round(prices["ask_price"] * (1 + (price_step /100 * fibonacci_sequence[i])), price_precision)
                    
                    # Use Fibonacci multiplier for order size
                    # First layer (i=0) is the base_order_size
                    layer_ask_size = ask_size * fibonacci_sequence[i]
                    
                    try:
                        min_order_size = float(symbol_config.get("minOrderSize", 0.001))
                    except (ValueError, TypeError):
                        min_order_size = 0.001
                    
                    if layer_ask_size >= min_order_size:
                        batch_orders.append({
                            "symbol": symbol,
                            "type": "limit",
                            "side": "sell",
                            "amount": layer_ask_size,
                            "price": layer_ask_price,
                            "params": {
                                "timeInForce": "Gtc",
                                "postOnly": True
                            }
                        })
                        logger.debug(f"Added ask order to batch: {layer_ask_size} @ {layer_ask_price}")
            
            # Place batch orders
            if batch_orders:
                logger.info(f"Placing {len(batch_orders)} orders in batch for {symbol}")
                
                # Add additional logging for debugging
                logger.debug(f"Batch order details: {json.dumps(batch_orders, default=str)}")
                
                # Execute the batch order creation
                try:
                    result = await self.exchange.create_orders(batch_orders)
                    
                    # Log success
                    success_count = len(result) if isinstance(result, list) else 1
                    logger.info(f"Successfully placed {success_count} orders in batch for {symbol}")
                    
                    self.open_orders = result
                    return result
                except Exception as batch_error:
                    logger.error(f"Batch order placement failed for {symbol}: {str(batch_error)}")
                    
                    # Try to provide more context in the error
                    if hasattr(batch_error, 'args') and len(batch_error.args) > 0:
                        logger.error(f"Error details: {batch_error.args[0]}")
                        
                    # Check if the method exists but failed, or doesn't exist
                    if "has no attribute" in str(batch_error):
                        logger.warning("create_orders method not available, falling back to individual orders")
                        
                        # Fall back to placing orders individually
                        individual_results = []
                        for order in batch_orders:
                            try:
                                if order['side'] == 'buy':
                                    result = await self.exchange.create_limit_buy_order(
                                        order['symbol'],
                                        order['amount'],
                                        order['price'],
                                        order.get('params', {})
                                    )
                                else:
                                    result = await self.exchange.create_limit_sell_order(
                                        order['symbol'],
                                        order['amount'],
                                        order['price'],
                                        order.get('params', {})
                                    )
                                individual_results.append(result)
                                logger.debug(f"Placed individual {order['side']} order: {order['amount']} @ {order['price']}")
                            except Exception as order_error:
                                logger.error(f"Error placing individual order for {symbol}: {str(order_error)}")
                        
                        self.open_orders = individual_results
                        return individual_results
                    return []
            else:
                logger.warning(f"No orders to place for {symbol}")
                return []
                
        except Exception as e:
            logger.error(f"Error in place_market_making_orders_batch for {symbol}: {str(e)}")
            return []

    async def cancel_all_orders(self, symbol: str) -> bool:
        """
        Cancel all open orders for a symbol using batch operation
        
        Args:
            symbol: Trading pair symbol
            
        Returns:
            Success boolean
        """
        if not self.exchange:
            logger.error("Exchange not initialized")
            return False
            
        try:
            open_orders = await self.exchange.fetch_open_orders(symbol)
            
            # Skip if no orders to cancel
            if not open_orders or len(open_orders) == 0:
                return True
                
            # Extract all order IDs
            order_ids = [order["id"] for order in open_orders if "id" in order]
            
            if order_ids:
                # Use cancelOrders to cancel multiple orders in one request
                await self.exchange.cancel_orders(order_ids, symbol)
                logger.info(f"Cancelled {len(order_ids)} orders in batch")
                
            self.open_orders = []
            return True
        except Exception as e:
            logger.error(f"Error cancelling orders in batch: {str(e)}")
            return False
    
    def assess_risk(self, position: Dict[str, Any], market_data: Dict[str, Any], symbol_config: Dict[str, Any] = None) -> Dict[str, Any]:
        """
        Perform risk assessment based on current position and market data
        
        Args:
            position: Current position
            market_data: Market data
            symbol_config: Configuration for this specific symbol
            
        Returns:
            Risk assessment
        """
        try:
            # If no symbol config provided, use empty dict
            if symbol_config is None:
                symbol_config = {}
                
            # Ensure position object exists
            if position is None:
                position = {"size": 0, "side": "flat", "unrealized_pnl": 0}
                
            # Safe extraction of ticker data
            ticker = market_data.get("ticker", {})
            if ticker is None:
                ticker = {}
                
            # Safe extraction of price with default
            current_price = ticker.get("last", 0)
            if current_price is None or current_price == 0:
                logger.warning("Missing or zero price in ticker data")
                current_price = 1  # Placeholder to avoid division by zero
            
            # Safe extraction of position data with explicit defaults
            position_size = position.get("size", 0)
            position_side = position.get("side", "flat")
            unrealized_pnl = position.get("unrealized_pnl", 0)
            
            # Ensure all values are not None
            if position_size is None:
                position_size = 0
            if position_side is None:
                position_side = "flat"
            if unrealized_pnl is None:
                unrealized_pnl = 0
                
            # Convert values safely to float with explicit error handling
            try:
                position_size_float = float(position_size)
            except (TypeError, ValueError):
                logger.warning(f"Invalid position size: {position_size}, using 0")
                position_size_float = 0
                
            try:
                unrealized_pnl_float = float(unrealized_pnl)
            except (TypeError, ValueError):
                logger.warning(f"Invalid unrealized PnL: {unrealized_pnl}, using 0")
                unrealized_pnl_float = 0
                
            # Now it's safe to use abs()
            position_size_abs = abs(position_size_float)
            
            # Calculate position value
            position_value = position_size_abs * current_price
            
            # Safely get max position size from config - first from symbol config, then general config
            try:
                max_position_size = float(symbol_config.get("maxPositionSize", 0))
                if max_position_size <= 0:
                    # Try the general config if not found in symbol config
                    max_position_size = float(self.config.get("trading", {}).get("maxPositionSize", 0.1))
                    if max_position_size <= 0:
                        max_position_size = 0.1
            except (TypeError, ValueError, KeyError):
                logger.warning("Invalid maxPositionSize in config, using default")
                max_position_size = 0.1
                
            # Check if position exceeds limits
            exceeds_position_limit = position_size_abs > max_position_size
            
            # Calculate max acceptable drawdown safely
            try:
                max_daily_loss_config = self.config.get("riskManagement", {}).get("maxDailyLoss", 100)
                max_daily_loss = float(os.getenv('MAX_DAILY_LOSS', max_daily_loss_config))
                if max_daily_loss <= 0:
                    max_daily_loss = 100
            except (TypeError, ValueError):
                logger.warning("Invalid maxDailyLoss, using default")
                max_daily_loss = 100
            
            # Calculate profit percentage safely
            if position_value > 0 and unrealized_pnl_float is not None:
                profit_percentage = (unrealized_pnl_float / position_value * 100)
            else:
                profit_percentage = 0
            
            # Get take profit percentage safely
            try:
                take_profit_percentage = float(self.config.get("riskManagement", {}).get("takeProfitPercentage", 1))
                if take_profit_percentage <= 0:
                    take_profit_percentage = 1
            except (TypeError, ValueError):
                logger.warning("Invalid takeProfitPercentage, using default")
                take_profit_percentage = 1
                
            # Check for take profit
            should_take_profit = profit_percentage > take_profit_percentage
            
            # Get stop loss percentage safely
            try:
                stop_loss_percentage = float(self.config.get("riskManagement", {}).get("stopLossPercentage", 2))
                if stop_loss_percentage <= 0:
                    stop_loss_percentage = 2
            except (TypeError, ValueError):
                logger.warning("Invalid stopLossPercentage, using default")
                stop_loss_percentage = 2
                
            # Fix for original error - ensure we use unrealized_pnl_float with abs()
            should_stop_loss = (unrealized_pnl_float < 0 and 
                            abs(unrealized_pnl_float) > position_value * stop_loss_percentage / 100)
            
            # Check daily loss limit
            daily_loss_limit_exceeded = (self.current_balance < self.start_balance - max_daily_loss)
            
            # Simplified risk score from 0-100
            risk_score = 100 if exceeds_position_limit else (position_size_abs / max_position_size * 80)
            
            return {
                "risk_score": risk_score,
                "exceeds_position_limit": exceeds_position_limit,
                "should_take_profit": should_take_profit,
                "should_stop_loss": should_stop_loss,
                "daily_loss_limit_exceeded": daily_loss_limit_exceeded,
                "profit_percentage": profit_percentage,
                "current_pnl": unrealized_pnl_float,
                "recommendations": {
                    "reduce_position": exceeds_position_limit,
                    "take_profit": should_take_profit,
                    "stop_loss": should_stop_loss,
                    "pause_trading": daily_loss_limit_exceeded,
                    "adjust_spread": risk_score > 60,
                }
            }
        except Exception as e:
            logger.error(f"Error in risk assessment: {str(e)}")
            # Return a safe default
            return {
                "risk_score": 0,
                "exceeds_position_limit": False,
                "should_take_profit": False,
                "should_stop_loss": False,
                "daily_loss_limit_exceeded": False,
                "profit_percentage": 0,
                "current_pnl": 0,
                "recommendations": {
                    "reduce_position": False,
                    "take_profit": False,
                    "stop_loss": False,
                    "pause_trading": False,
                    "adjust_spread": False,
                }
            }
    
    async def execute_risk_management_actions(self, symbol: str, risk_assessment: Dict[str, Any]) -> None:
        """
        Execute risk management actions based on assessment
        
        Args:
            symbol: Trading pair symbol
            risk_assessment: Risk assessment data
        """
        if not self.exchange:
            logger.error("Exchange not initialized")
            return
            
        try:
            if risk_assessment["recommendations"]["pause_trading"]:
                logger.warning("Daily loss limit exceeded - pausing trading")
                await self.cancel_all_orders(symbol)
                return
                
            if risk_assessment["recommendations"]["stop_loss"]:
                logger.warning("Stop loss triggered - closing position")
                await self.close_position(symbol)
                return
                
            if risk_assessment["recommendations"]["take_profit"]:
                logger.info("Take profit triggered - closing position")
                await self.close_position(symbol)
                return
                
            if risk_assessment["recommendations"]["reduce_position"]:
                logger.warning("Position limit exceeded - reducing position")
                await self.reduce_position(symbol)
                
        except Exception as e:
            logger.error(f"Error executing risk management actions: {str(e)}")
    
    async def close_position(self, symbol: str) -> None:
        """Close the entire position for a symbol with improved error handling"""
        if not self.exchange:
            logger.error("Exchange not initialized")
            return
            
        try:
            position = await self.get_current_position(symbol)
            
            # Safe extraction with defaults
            position_size = float(position.get("size", 0)) if position.get("size") is not None else 0
            position_side = position.get("side", "flat")
            
            if position_size > 0 and position_side != "flat":
                # Create market order in the opposite direction
                if position_side == "long":
                    await self.exchange.create_market_sell_order(symbol, position_size)
                    logger.info(f"Closed long position: {position_size}")
                else:
                    await self.exchange.create_market_buy_order(symbol, position_size)
                    logger.info(f"Closed short position: {position_size}")
                    
        except Exception as e:
            logger.error(f"Error closing position: {str(e)}")
    
    async def reduce_position(self, symbol: str) -> None:
        """Reduce the position by half with improved error handling"""
        if not self.exchange:
            logger.error("Exchange not initialized")
            return
            
        try:
            position = await self.get_current_position(symbol)
            
            # Safe extraction with defaults
            position_size = float(position.get("size", 0)) if position.get("size") is not None else 0
            position_side = position.get("side", "flat")
            
            if position_size > 0 and position_side != "flat":
                # Reduce by 50%
                reduction_size = position_size / 2
                
                # Create market order in the opposite direction
                if position_side == "long":
                    await self.exchange.create_market_sell_order(symbol, reduction_size)
                    logger.info(f"Reduced long position by: {reduction_size}")
                else:
                    await self.exchange.create_market_buy_order(symbol, reduction_size)
                    logger.info(f"Reduced short position by: {reduction_size}")
                    
        except Exception as e:
            logger.error(f"Error reducing position: {str(e)}")
    
    async def update_balance(self) -> None:
        """Update the current balance"""
        if not self.exchange:
            logger.error("Exchange not initialized")
            return
            
        try:
            balance = await self.exchange.fetch_balance()
            self.current_balance = self._get_usdt_balance(balance)
            logger.debug(f"Current balance: {self.current_balance} USDC")
        except Exception as e:
            logger.error(f"Error updating balance: {str(e)}")
    
    async def run(self) -> None:
        """Main market making loop"""
        try:
            logger.info("Starting market maker...")
            
            # Initialize the exchange connection
            self.exchange = await self._initialize_exchange()
            if not self.exchange:
                raise ConnectionError("Failed to initialize exchange connection")
            
            # Get symbols to trade
            symbols_to_trade = []
            
            # Check if we have the new config format with symbols array
            if "symbols" in self.config:
                for symbol_config in self.config["symbols"]:
                    if symbol_config.get("enabled", True):  # Only include enabled symbols
                        symbols_to_trade.append(symbol_config["symbol"])
                
                if not symbols_to_trade:
                    logger.warning("No enabled symbols found in config, falling back to default")
                    # Fall back to the legacy config
                    symbols_to_trade = [self.config.get("trading", {}).get("symbol", "BTC/USDC:USDC")]
            else:
                # Legacy config format
                symbols_to_trade = [self.config.get("trading", {}).get("symbol", "BTC/USDC:USDC")]
            
            logger.info(f"Trading the following symbols: {symbols_to_trade}")
            
            # Validate all symbols
            validated_symbols = []
            for symbol in symbols_to_trade:
                try:
                    # Use the initialize_markets method but with the specific symbol
                    validated_symbol = await self.initialize_markets_for_symbol(symbol)
                    validated_symbols.append(validated_symbol)
                    logger.info(f"Initialized trading with symbol: {validated_symbol}")
                except Exception as e:
                    logger.error(f"Failed to initialize market for {symbol}: {str(e)}")
                    logger.warning(f"Skipping symbol: {symbol}")
            
            if not validated_symbols:
                raise ValueError("No valid symbols to trade")
            
            update_interval = self.config["marketMaking"]["updateInterval"] / 1000  # Convert to seconds
            
            while True:
                for symbol in validated_symbols:
                    try:
                        # Get the trading config for this symbol
                        symbol_config = self.get_symbol_config(symbol)
                        
                        # Fetch latest market data
                        market_data = await self.fetch_market_data(symbol)
                        
                        # Get current position
                        position = await self.get_current_position(symbol)
                        self.current_position = position
                        
                        # Update account balance
                        await self.update_balance()
                        
                        # Calculate optimal prices
                        prices = self.calculate_prices(market_data)
                        
                        # Perform risk assessment
                        risk_assessment = self.assess_risk(position, market_data, symbol_config)
                        
                        # Log current status with safety checks
                        logger.info(f"------- {symbol} -------")
                        
                        # Safe access to ticker data
                        current_price = market_data.get('ticker', {}).get('last', 0)
                        if current_price is None:
                            current_price = 0
                        logger.info(f"Current price: {current_price}")
                        
                        # Safe access to position data
                        position_side = position.get('side', 'flat')
                        position_size = position.get('size', 0)
                        if position_side is None:
                            position_side = 'flat'
                        if position_size is None:
                            position_size = 0
                        logger.info(f"Position: {position_side} {position_size}")
                        
                        # Safe access to P&L
                        pnl = position.get('unrealized_pnl', 0)
                        if pnl is None:
                            pnl = 0
                        logger.info(f"P&L: {pnl}")
                        
                        # Safe access to prices
                        bid_price = prices.get('bid_price', 0)
                        ask_price = prices.get('ask_price', 0)
                        if bid_price is None:
                            bid_price = 0
                        if ask_price is None:
                            ask_price = 0
                        logger.info(f"Prices: Bid={bid_price}, Ask={ask_price}")
                        
                        # Safe access to risk score
                        risk_score = risk_assessment.get('risk_score', 0)
                        if risk_score is None:
                            risk_score = 0
                        logger.info(f"Risk score: {risk_score}")
                        
                        # Execute risk management actions if needed
                        await self.execute_risk_management_actions(symbol, risk_assessment)
                        
                        # Place market making orders if not paused
                        if not risk_assessment["recommendations"]["pause_trading"]:
                            # Pass the symbol-specific config
                            await self.place_market_making_orders_batch(symbol, prices, position, symbol_config)
                            
                    except Exception as e:
                        logger.error(f"Error in market making loop for {symbol}: {str(e)}")
                
                # Wait for next update
                await asyncio.sleep(update_interval)
                
        except Exception as e:
            logger.error(f"Fatal error in market maker: {str(e)}")
            raise
        finally:
            # Ensure we close the exchange connection properly
            if self.exchange:
                try:
                    await self.exchange.close()
                    logger.info("Exchange connection closed")
                except Exception as e:
                    logger.error(f"Error closing exchange connection: {str(e)}")

# Main function to start the market maker
async def main():
    # Load configuration
    config_path = os.getenv('CONFIG_PATH', 'config.json')
    
    # Create and start market maker
    market_maker = HyperliquidMarketMaker(config_path)
    try:
        await market_maker.run()
    finally:
        # Ensure the exchange connection is closed if it exists
        if market_maker.exchange:
            await market_maker.exchange.close()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Market maker stopped by user")
    except Exception as e:
        print(f"Market maker stopped due to error: {str(e)}")