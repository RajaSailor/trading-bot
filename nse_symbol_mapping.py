from __future__ import annotations

import logging


logger = logging.getLogger(__name__)


class NSESymbolMapper:
    """Map index symbols to known Dhan security-master name variants."""

    NSE_INDICES = {
        "NIFTY": {
            "dhan_names": ["NIFTY", "NIFTY50", "NIFTY 50", "NIFTY50INDEX"],
            "exchange": "NSE_FNO",
        },
        "BANKNIFTY": {
            "dhan_names": ["BANKNIFTY", "NIFTYBANK", "BANK NIFTY"],
            "exchange": "NSE_FNO",
        },
        "SENSEX": {
            "dhan_names": ["SENSEX", "BSE SENSEX", "BSESENSEX"],
            "exchange": "BSE_FNO",
        },
    }

    @staticmethod
    def get_nse_index_details(symbol: str) -> dict | None:
        details = NSESymbolMapper.NSE_INDICES.get(symbol.upper())
        if details:
            logger.debug("Found index mapping for %s", symbol)
        return details

    @staticmethod
    def get_possible_dhan_names(symbol: str) -> list[str]:
        details = NSESymbolMapper.get_nse_index_details(symbol)
        if details:
            return list(details["dhan_names"])
        return [symbol.upper()]

    @staticmethod
    def get_exchange(symbol: str) -> str:
        details = NSESymbolMapper.get_nse_index_details(symbol)
        if details:
            return str(details["exchange"])
        return "NSE_FNO"
