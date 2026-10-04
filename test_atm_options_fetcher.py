import unittest
from datetime import datetime

from atm_options_fetcher import ATMOptionsFetcher
from data_manager import IST, DataManager, Instrument
from security_master import SecurityMasterCache

NOW = datetime(2026, 9, 1, 10, 0, tzinfo=IST)


def _row(security_id, trading_symbol, strike, side, expiry, exchange="MCX", instrument="OPTFUT", sm_symbol_name=""):
    """Row shaped like the Dhan compact security master CSV."""
    return {
        "SEM_EXM_EXCH_ID": exchange,
        "SEM_SEGMENT": "M" if exchange == "MCX" else "D",
        "SEM_SMST_SECURITY_ID": str(security_id),
        "SEM_INSTRUMENT_NAME": instrument,
        "SEM_TRADING_SYMBOL": trading_symbol,
        "SEM_LOT_UNITS": "1",
        "SEM_EXPIRY_DATE": expiry,
        "SEM_STRIKE_PRICE": f"{float(strike):.5f}",
        "SEM_OPTION_TYPE": side,
        "SM_SYMBOL_NAME": sm_symbol_name,
    }


def _fetcher(rows):
    cache = SecurityMasterCache(row_source=lambda: iter(rows))
    return ATMOptionsFetcher(DataManager(security_master=cache))


def _commodity(symbol):
    return Instrument(
        symbol=symbol,
        security_id=None,
        exchange="MCX",
        exchange_segment="MCX_COMM",
        instrument_type="FUTCOM",
        category="commodity_options",
        data_source="dhan_primary",
    )


class ATMOptionsFetcherTests(unittest.TestCase):
    def test_resolves_nearest_matching_option_contract(self):
        fetcher = _fetcher(
            [
                _row(100, "GOLD-25Sep2026-72000-CE", 72000, "CE", "2026-09-25 23:30:00"),
                _row(101, "GOLD-30Oct2026-72000-CE", 72000, "CE", "2026-10-30 23:30:00"),
            ]
        )

        contract = fetcher._resolve_option_contract(_commodity("GOLD"), 72000, "CE", now=NOW)

        self.assertIsNotNone(contract)
        self.assertEqual(100, contract["security_id"])
        self.assertEqual("GOLD-25Sep2026-72000-CE", contract["option_symbol"])
        self.assertEqual("OPTFUT", contract["instrument_type"])

    def test_resolves_exact_commodity_root_and_ignores_mini_contracts(self):
        fetcher = _fetcher(
            [
                _row(300, "SILVERM-24Sep2026-283000-CE", 283000, "CE", "2026-09-24 23:30:00"),
                _row(301, "SILVER-24Sep2026-283000-CE", 283000, "CE", "2026-09-24 23:30:00"),
            ]
        )

        contract = fetcher._resolve_option_contract(_commodity("SILVER"), 283000, "CE", now=NOW)

        self.assertIsNotNone(contract)
        self.assertEqual(301, contract["security_id"])
        self.assertEqual("SILVER-24Sep2026-283000-CE", contract["option_symbol"])

    def test_resolves_spaced_crude_oil_symbol_to_crudeoil_contracts(self):
        fetcher = _fetcher([_row(400, "CRUDEOIL-17Sep2026-7200-CE", 7200, "CE", "2026-09-17 23:30:00")])

        contract = fetcher._resolve_option_contract(_commodity("CRUDE OIL"), 7200, "CE", now=NOW)

        self.assertIsNotNone(contract)
        self.assertEqual(400, contract["security_id"])

    def test_resolves_nifty_index_option_with_empty_sm_symbol_name(self):
        fetcher = _fetcher(
            [_row(200, "NIFTY-08Sep2026-24500-CE", 24500, "CE", "2026-09-08 14:30:00", exchange="NSE", instrument="OPTIDX")]
        )
        instrument = Instrument(
            symbol="NIFTY",
            security_id=None,
            exchange="NSE",
            exchange_segment="IDX_I",
            instrument_type="INDEX",
            category="index_options",
            data_source="dhan_primary",
        )

        contract = fetcher._resolve_option_contract(instrument, 24500, "CE", now=NOW)

        self.assertIsNotNone(contract)
        self.assertEqual(200, contract["security_id"])
        self.assertEqual("NIFTY-08Sep2026-24500-CE", contract["option_symbol"])
        self.assertEqual("NSE_FNO", contract["exchange_segment"])


if __name__ == "__main__":
    unittest.main()
