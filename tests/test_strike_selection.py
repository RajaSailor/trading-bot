import logging
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from atm_options_fetcher import REASON_ATM, REASON_EXPIRY_ITM_PLUS_1, ATMOptionsFetcher
from data_manager import IST, DataManager, Instrument


NON_EXPIRY_DAY = datetime(2026, 10, 1, 11, 0, tzinfo=IST)
EXPIRY = "2026-10-27 00:00:00"
EXPIRY_DAY = datetime(2026, 10, 27, 11, 0, tzinfo=IST)


def _instrument(symbol, category="commodity_options"):
    if category == "commodity_options":
        exchange, segment, inst_type = "MCX", "MCX_COMM", "FUTCOM"
    elif category == "index_options":
        exchange, segment, inst_type = "NSE_FNO", "NSE_FNO", "FUTIDX"
    else:
        exchange, segment, inst_type = "NSE_FNO", "NSE_FNO", "FUTSTK"
    return Instrument(
        symbol=symbol, security_id=None, exchange=exchange, exchange_segment=segment,
        instrument_type=inst_type, category=category, data_source="dhan_primary",
    )


def _option_rows(symbol, strikes, side, expiry=EXPIRY, exchange="MCX", inst_type="OPTFUT", start_id=1000):
    rows = []
    for offset, strike in enumerate(strikes):
        strike_text = f"{strike:g}"
        rows.append(
            {
                "SM_SYMBOL_NAME": symbol,
                "SEM_EXM_EXCH_ID": exchange,
                "SEM_SMST_SECURITY_ID": str(start_id + offset),
                "SEM_STRIKE_PRICE": str(strike),
                "SEM_OPTION_TYPE": side,
                "SEM_EXCH_INSTRUMENT_TYPE": inst_type,
                "SEM_TRADING_SYMBOL": f"{symbol.replace(' ', '')}-{expiry[:10]}-{strike_text}-{side}",
                "SEM_EXPIRY_DATE": expiry,
            }
        )
    return rows


def _fetcher(rows):
    manager = DataManager()
    manager._security_master_cache = rows
    manager._security_master_cache_ts = 1
    return ATMOptionsFetcher(manager)


class ContractDrivenStrikeSelectionTests(unittest.TestCase):
    def test_atm_uses_nearest_listed_strike_not_fixed_rounding(self):
        # GOLD lists strikes every 500; fixed 100-step rounding would give 72100 (not listed).
        strikes = [71000, 71500, 72000, 72500, 73000]
        fetcher = _fetcher(_option_rows("GOLD", strikes, "CE") + _option_rows("GOLD", strikes, "PE", start_id=2000))

        ce = fetcher._resolve_option_contract(_instrument("GOLD"), 72140.0, "CE", now=NON_EXPIRY_DAY)
        pe = fetcher._resolve_option_contract(_instrument("GOLD"), 72140.0, "PE", now=NON_EXPIRY_DAY)

        self.assertEqual(72000, ce["atm_strike"])
        self.assertEqual(72000, pe["atm_strike"])
        self.assertEqual(REASON_ATM, ce["strike_reason"])
        self.assertEqual(REASON_ATM, pe["strike_reason"])
        self.assertEqual(1002, ce["security_id"])
        self.assertEqual(2002, pe["security_id"])
        self.assertEqual("27OCT2026", ce["expiry"])

    def test_selects_nearest_active_expiry_and_skips_expired_contracts(self):
        rows = (
            _option_rows("GOLD", [72000], "CE", expiry="2026-09-25 00:00:00", start_id=1)
            + _option_rows("GOLD", [72000], "CE", expiry="2026-11-25 00:00:00", start_id=2)
            + _option_rows("GOLD", [72000], "CE", expiry=EXPIRY, start_id=3)
        )
        contract = _fetcher(rows)._resolve_option_contract(_instrument("GOLD"), 72000.0, "CE", now=NON_EXPIRY_DAY)

        self.assertEqual(3, contract["security_id"])
        self.assertEqual("27OCT2026", contract["expiry"])

    def test_expiry_day_ce_and_pe_use_itm_plus_one(self):
        strikes = [24300, 24350, 24400, 24450, 24500, 24550, 24600]
        rows = (
            _option_rows("NIFTY", strikes, "CE", exchange="NSE", inst_type="OPTIDX")
            + _option_rows("NIFTY", strikes, "PE", exchange="NSE", inst_type="OPTIDX", start_id=2000)
        )
        fetcher = _fetcher(rows)
        nifty = _instrument("NIFTY", "index_options")

        with self.assertLogs("atm_options_fetcher", level=logging.INFO) as logs:
            ce = fetcher._resolve_option_contract(nifty, 24460.0, "CE", now=EXPIRY_DAY)
            pe = fetcher._resolve_option_contract(nifty, 24460.0, "PE", now=EXPIRY_DAY)

        self.assertEqual(24400, ce["atm_strike"])
        self.assertEqual(24450, ce["listed_atm_strike"])
        self.assertEqual(REASON_EXPIRY_ITM_PLUS_1, ce["strike_reason"])
        self.assertEqual(24500, pe["atm_strike"])
        self.assertEqual(REASON_EXPIRY_ITM_PLUS_1, pe["strike_reason"])
        joined = "\n".join(logs.output)
        self.assertIn("[NIFTY] CE contract selected | spot=24460.00 | expiry=27OCT2026 | strike=24400 | reason=EXPIRY_ITM_PLUS_1", joined)
        self.assertIn("[NIFTY] PE contract selected | spot=24460.00 | expiry=27OCT2026 | strike=24500 | reason=EXPIRY_ITM_PLUS_1", joined)

    def test_non_expiry_day_logs_atm_reason(self):
        fetcher = _fetcher(_option_rows("SENSEX", [80000, 80100, 80200], "CE", exchange="BSE", inst_type="OPTIDX"))

        with self.assertLogs("atm_options_fetcher", level=logging.INFO) as logs:
            contract = fetcher._resolve_option_contract(_instrument("SENSEX", "index_options"), 80120.0, "CE", now=NON_EXPIRY_DAY)

        self.assertEqual(80100, contract["atm_strike"])
        self.assertEqual("BSE_FNO", contract["exchange_segment"])
        self.assertIn("reason=ATM", "\n".join(logs.output))

    def test_expiry_day_falls_back_to_further_itm_when_one_step_missing(self):
        # 71900 (one step below ATM 72000) is not listed.
        strikes = [71700, 71800, 72000, 72100, 72200]
        fetcher = _fetcher(_option_rows("GOLD", strikes, "CE"))

        contract = fetcher._resolve_option_contract(_instrument("GOLD"), 72010.0, "CE", now=EXPIRY_DAY)

        self.assertEqual(71800, contract["atm_strike"])
        self.assertEqual(REASON_EXPIRY_ITM_PLUS_1, contract["strike_reason"])

    def test_expiry_day_falls_back_to_atm_when_no_itm_strike_exists(self):
        strikes = [7000, 7050, 7100]
        fetcher = _fetcher(_option_rows("NATURALGAS", strikes, "PE"))

        contract = fetcher._resolve_option_contract(_instrument("NATURALGAS"), 7120.0, "PE", now=EXPIRY_DAY)

        self.assertEqual(7100, contract["atm_strike"])
        self.assertEqual(REASON_ATM, contract["strike_reason"])

    def test_expiry_day_uses_ist_date(self):
        strikes = [72000, 72100, 72200]
        fetcher = _fetcher(_option_rows("GOLD", strikes, "CE"))
        # 2026-10-26 20:00 UTC is already 2026-10-27 01:30 IST (expiry day).
        utc_now = datetime(2026, 10, 26, 20, 0, tzinfo=timezone.utc)

        contract = fetcher._resolve_option_contract(_instrument("GOLD"), 72100.0, "CE", now=utc_now)

        self.assertEqual(72000, contract["atm_strike"])
        self.assertEqual(REASON_EXPIRY_ITM_PLUS_1, contract["strike_reason"])

    def test_commodity_contracts_resolve_from_listed_strikes(self):
        cases = [
            ("GOLD", [125000, 125500, 126000, 126500], 125730.0, 125500, 125000, 126000),
            ("SILVER", [150000, 151000, 152000, 153000], 151620.0, 152000, 151000, 153000),
            ("CRUDE OIL", [5400, 5450, 5500, 5550], 5468.0, 5450, 5400, 5500),
            ("NATURALGAS", [270, 275, 280, 285], 277.4, 275, 270, 280),
        ]
        for symbol, strikes, spot, atm, ce_itm, pe_itm in cases:
            with self.subTest(symbol=symbol):
                rows = _option_rows(symbol, strikes, "CE") + _option_rows(symbol, strikes, "PE", start_id=5000)
                fetcher = _fetcher(rows)
                instrument = _instrument(symbol)

                ce = fetcher._resolve_option_contract(instrument, spot, "CE", now=NON_EXPIRY_DAY)
                pe = fetcher._resolve_option_contract(instrument, spot, "PE", now=NON_EXPIRY_DAY)
                self.assertEqual((atm, atm), (ce["atm_strike"], pe["atm_strike"]))
                self.assertEqual("MCX_COMM", ce["exchange_segment"])

                ce_exp = fetcher._resolve_option_contract(instrument, spot, "CE", now=EXPIRY_DAY)
                pe_exp = fetcher._resolve_option_contract(instrument, spot, "PE", now=EXPIRY_DAY)
                self.assertEqual(ce_itm, ce_exp["atm_strike"])
                self.assertEqual(pe_itm, pe_exp["atm_strike"])

    def test_nifty50_stock_option_uses_listed_strike(self):
        strikes = [2900, 2920, 2940, 2960, 2980]
        fetcher = _fetcher(_option_rows("RELIANCE", strikes, "CE", exchange="NSE", inst_type="OPTSTK"))

        contract = fetcher._resolve_option_contract(
            _instrument("RELIANCE", "nifty50_stock_options"), 2936.0, "CE", now=NON_EXPIRY_DAY
        )

        self.assertEqual(2940, contract["atm_strike"])
        self.assertEqual("NSE_FNO", contract["exchange_segment"])


class PremiumThresholdGatingTests(unittest.TestCase):
    def test_low_or_high_premium_does_not_block_selection(self):
        strikes = [72000, 72500]
        fetcher = _fetcher(_option_rows("GOLD", strikes, "CE"))
        instrument = _instrument("GOLD")

        for premium in (0.05, 1.0, 99999.0):
            with self.subTest(premium=premium):
                candles = [{"open": premium, "high": premium, "low": premium, "close": premium, "timestamp": "t"}]
                with (
                    patch.object(fetcher.data_manager, "fetch_candles", return_value=[{"close": 72100.0}]),
                    patch.object(fetcher.data_manager, "_fetch_dhan_intraday_data", return_value=candles),
                    patch("atm_options_fetcher._apply_rate_limit"),
                    patch.object(ATMOptionsFetcher, "_ist_date", return_value=NON_EXPIRY_DAY.date()),
                ):
                    result, contract = fetcher.fetch_atm_premium_candles(instrument, "CE", "10min")

                self.assertEqual(candles, result)
                self.assertEqual(72000, contract["atm_strike"])
                self.assertEqual(round(premium, 2), contract["premium_ltp"])


if __name__ == "__main__":
    unittest.main()
