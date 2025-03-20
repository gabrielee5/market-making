# Hyperliquid Market Maker

A Python-based market making system for the Hyperliquid exchange using CCXT.

## Features

- Connects to Hyperliquid exchange via CCXT
- Implements basic market making strategy with configurable parameters
- Multi-layer order placement for better liquidity provision
- Risk management including position limits, stop loss, and take profit
- Adapts to market conditions by adjusting spread based on volatility and order book imbalance
- Supports order book analysis for smart pricing
- Configurable via JSON configuration file
- Environment variable support for secure credential management

## Requirements

- Python 3.8+
- Required packages:
  - ccxt
  - python-dotenv
  - aiohttp
  - asyncio
  - pandas (for data analysis)

## Installation

1. Clone this repository:
```bash
git clone https://github.com/yourusername/hyperliquid-market-maker.git
cd hyperliquid-market-maker
```

2. Install the required dependencies:
```bash
pip install -r requirements.txt
```

3. Create a `.env` file from the example template:
```bash
cp .env.example .env
```

4. Edit the `.env` file with your Hyperliquid API credentials:
```
HYPERLIQUID_WALLET=your_api_key_here
HYPERLIQUID_SECRET_KEY=your_api_secret_here
```

5. Review and modify the `config.json` file to adjust your trading parameters.

## Configuration

The market maker is configured through a JSON configuration file that includes:

- **Exchange settings**: Connection details for Hyperliquid
- **Trading parameters**: Symbol, order sizes, position limits, etc.
- **Market making parameters**: Spread percentages, update intervals, layers
- **Risk management**: Stop loss, take profit, max drawdown, etc.
- **Technical indicators**: Settings for EMA, RSI, Bollinger Bands
- **Development settings**: Logging, backtesting options

Refer to the `config.json` file for all available options and descriptions.

## Usage

Run the market maker:

```bash
python run_market_maker.py
```

For testnet (paper trading):
```bash
# Ensure USE_TESTNET=true in your .env file
python run_market_maker.py
```