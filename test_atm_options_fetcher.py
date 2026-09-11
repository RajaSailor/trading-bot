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

    def test_resolves_commodity_contract_from_trading_symbol_without_sm_symbol_name(self):
        manager = DataManager()
        manager._security_master_cache = [
            {
                "SEM_EXM_EXCH_ID": "MCX",
                "SEM_SMST_SECURITY_ID": "300",
                "SEM_STRIKE_PRICE": "283000",
                "SEM_OPTION_TYPE": "CE",
                "SEM_EXCH_INSTRUMENT_TYPE": "OPTFUT",
                "SEM_TRADING_SYMBOL": "SILVERM-24Sep2026-283000-CE",
                "SEM_CUSTOM_SYMBOL": "SILVERM 24 SEP 283000 CALL",
                "SEM_EXPIRY_DATE": "2026-09-24 00:00:00",
            },
            {
                "SEM_EXM_EXCH_ID": "MCX",
                "SEM_SMST_SECURITY_ID": "301",
                "SEM_STRIKE_PRICE": "283000",
                "SEM_OPTION_TYPE": "CE",
                "SEM_EXCH_INSTRUMENT_TYPE": "OPTFUT",
                "SEM_TRADING_SYMBOL": "SILVER-24Sep2026-283000-CE",
                "SEM_CUSTOM_SYMBOL": "SILVER 24 SEP 283000 CALL",
                "SEM_EXPIRY_DATE": "2026-09-24 00:00:00",
            },
        ]
        manager._security_master_cache_ts = 1
        fetcher = ATMOptionsFetcher(manager)
        instrument = Instrument(
            symbol="SILVER",
            security_id=None,
            exchange="MCX",
            exchange_segment="MCX_COMM",
            instrument_type="FUTCOM",
            category="commodity_options",
            data_source="dhan_primary",
        )

        contract = fetcher._resolve_option_contract(instrument, 283000, "CE")

        self.assertIsNotNone(contract)
        self.assertEqual(301, contract["security_id"])
        self.assertEqual("SILVER-24Sep2026-283000-CE", contract["option_symbol"])

    def test_resolves_spaced_crude_oil_contract_from_custom_symbol(self):
        manager = DataManager()
        manager._security_master_cache = [
            {
                "SEM_EXM_EXCH_ID": "MCX",
                "SEM_SMST_SECURITY_ID": "400",
                "SEM_STRIKE_PRICE": "7200",
                "SEM_OPTION_TYPE": "CE",
                "SEM_EXCH_INSTRUMENT_TYPE": "OPTFUT",
                "SEM_CUSTOM_SYMBOL": "CRUDE OIL 19 SEP 7200 CALL",
                "SEM_EXPIRY_DATE": "2026-09-19 00:00:00",
            }
        ]
        manager._security_master_cache_ts = 1
        fetcher = ATMOptionsFetcher(manager)
        instrument = Instrument(
            symbol="CRUDE OIL",
            security_id=None,
            exchange="MCX",
            exchange_segment="MCX_COMM",
            instrument_type="FUTCOM",
            category="commodity_options",
            data_source="dhan_primary",
        )

        contract = fetcher._resolve_option_contract(instrument, 7200, "CE")

        self.assertIsNotNone(contract)
        self.assertEqual(400, contract["security_id"])

    def test_resolves_nifty_contract_with_security_master_alias_name(self):
        manager = DataManager()
        manager._security_master_cache = [
            {
                "SM_SYMBOL_NAME": "NIFTY 50",
                "SEM_EXM_EXCH_ID": "NSE",
                "SEM_SMST_SECURITY_ID": "200",
                "SEM_STRIKE_PRICE": "24500",
                "SEM_OPTION_TYPE": "CE",
                "SEM_EXCH_INSTRUMENT_TYPE": "OPTIDX",
                "SEM_TRADING_SYMBOL": "NIFTY-18SEP-24500-CE",
                "SEM_EXPIRY_DATE": "2026-09-18 00:00:00",
            }
        ]
        manager._security_master_cache_ts = 1
        fetcher = ATMOptionsFetcher(manager)
        instrument = Instrument(
            symbol="NIFTY",
            security_id=None,
            exchange="NSE_FNO",
            exchange_segment="NSE_FNO",
            instrument_type="FUTIDX",
            category="index_options",
            data_source="dhan_primary",
        )

        contract = fetcher._resolve_option_contract(instrument, 24500, "CE")

        self.assertIsNotNone(contract)
        self.assertEqual(200, contract["security_id"])
        self.assertEqual("NIFTY-18SEP-24500-CE", contract["option_symbol"])


if __name__ == "__main__":
    unittest.main()
