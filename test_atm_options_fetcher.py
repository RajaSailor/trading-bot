import unittest

from atm_options_fetcher import ATMOptionsFetcher
from data_manager import DataManager, Instrument


class ATMOptionsFetcherTests(unittest.TestCase):
    def test_resolves_nearest_matching_option_contract(self):
        manager = DataManager()
        manager._security_master_cache = [
            {
                "SM_SYMBOL_NAME": "GOLD",
                "SEM_EXM_EXCH_ID": "MCX",
                "SEM_SMST_SECURITY_ID": "100",
                "SEM_STRIKE_PRICE": "72000",
                "SEM_OPTION_TYPE": "CE",
                "SEM_EXCH_INSTRUMENT_TYPE": "OPTFUT",
                "SEM_TRADING_SYMBOL": "GOLD-26SEP-72000-CE",
                "SEM_EXPIRY_DATE": "2026-09-25 00:00:00",
            },
            {
                "SM_SYMBOL_NAME": "GOLD",
                "SEM_EXM_EXCH_ID": "MCX",
                "SEM_SMST_SECURITY_ID": "101",
                "SEM_STRIKE_PRICE": "72000",
                "SEM_OPTION_TYPE": "CE",
                "SEM_EXCH_INSTRUMENT_TYPE": "OPTFUT",
                "SEM_TRADING_SYMBOL": "GOLD-30OCT-72000-CE",
                "SEM_EXPIRY_DATE": "2026-10-30 00:00:00",
            },
        ]
        manager._security_master_cache_ts = 1
        fetcher = ATMOptionsFetcher(manager)
        instrument = Instrument(
            symbol="GOLD",
            security_id=None,
            exchange="MCX",
            exchange_segment="MCX_COMM",
            instrument_type="FUTCOM",
            category="commodity_options",
            data_source="dhan_primary",
        )

        contract = fetcher._resolve_option_contract(instrument, 72000, "CE")

        self.assertIsNotNone(contract)
        self.assertEqual(100, contract["security_id"])
        self.assertEqual("GOLD-26SEP-72000-CE", contract["option_symbol"])
        self.assertEqual("OPTFUT", contract["instrument_type"])


if __name__ == "__main__":
    unittest.main()
