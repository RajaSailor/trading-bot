import logging
import unittest
from datetime import datetime

from atm_options_fetcher import BAND_ATM, BAND_ITM_PLUS_1, BAND_OTM_PLUS_1, REASON_ATM, ATMOptionsFetcher
from data_manager import IST, DataManager, Instrument
from security_master import SecurityMasterCache


NON_EXPIRY_DAY = datetime(2026, 10, 1, 11, 0, tzinfo=IST)
EXPIRY = "2026-10-27 14:30:00"
NEXT_EXPIRY = "2026-11-24 14:30:00"
EXPIRY_DAY = datetime(2026, 10, 27, 9, 0, tzinfo=IST)


def _instrument(symbol, category="commodity_options"):
    if category == "commodity_options":
        exchange, segment, inst_type = "MCX", "MCX_COMM", "FUTCOM"
    elif category == "index_options":
        exchange, segment, inst_type = ("BSE" if symbol == "SENSEX" else "NSE"), "IDX_I", "INDEX"
    else:
        exchange, segment, inst_type = "NSE", "NSE_EQ", "EQUITY"
    return Instrument(
        symbol=symbol, security_id=None, exchange=exchange, exchange_segment=segment,
        instrument_type=inst_type, category=category, data_source="dhan_primary",
    )


def _option_rows(root, strikes, side, expiry=EXPIRY, exchange="MCX", instrument="OPTFUT", start_id=1000,
                 sm_symbol_name=""):
    """Rows shaped like the real Dhan compact CSV (NSE F&O rows have empty SM_SYMBOL_NAME)."""
    expiry_date = datetime.strptime(expiry[:10], "%Y-%m-%d")
    rows = []
    for offset, strike in enumerate(strikes):
        rows.append(
            {
                "SEM_EXM_EXCH_ID": exchange,
                "SEM_SEGMENT": "M" if exchange == "MCX" else "D",
                "SEM_SMST_SECURITY_ID": str(start_id + offset),
                "SEM_INSTRUMENT_NAME": instrument,
                "SEM_TRADING_SYMBOL": f"{root}-{expiry_date.strftime('%d%b%Y')}-{strike:g}-{side}",
                "SEM_LOT_UNITS": "75",
                "SEM_EXPIRY_DATE": expiry,
                "SEM_STRIKE_PRICE": f"{float(strike):.5f}",
                "SEM_OPTION_TYPE": side,
                "SM_SYMBOL_NAME": sm_symbol_name,
            }
        )
    return rows


def _both_sides(root, strikes, **kwargs):
    start = kwargs.pop("start_id", 1000)
    return _option_rows(root, strikes, "CE", start_id=start, **kwargs) + _option_rows(
        root, strikes, "PE", start_id=start + 500, **kwargs
    )


def _fetcher(rows):
    cache = SecurityMasterCache(row_source=lambda: iter(rows))
    return ATMOptionsFetcher(DataManager(security_master=cache))


class ContractDrivenStrikeSelectionTests(unittest.TestCase):
    def test_atm_uses_nearest_listed_strike_not_fixed_rounding(self):
        # GOLD lists strikes every 500; fixed 100-step rounding would give 72100 (not listed).
        strikes = [71000, 71500, 72000, 72500, 73000]
        fetcher = _fetcher(_both_sides("GOLD", strikes))

        ce = fetcher._resolve_option_contract(_instrument("GOLD"), 72140.0, "CE", now=NON_EXPIRY_DAY)
        pe = fetcher._resolve_option_contract(_instrument("GOLD"), 72140.0, "PE", now=NON_EXPIRY_DAY)

        self.assertEqual(72000, ce["strike"])
        self.assertEqual(REASON_ATM, ce["strike_reason"])
        self.assertEqual("CE", ce["option_type"])
        self.assertEqual("GOLD-27Oct2026-72000-CE", ce["option_symbol"])
        self.assertEqual(72000, pe["strike"])
        self.assertEqual("PE", pe["option_type"])

    def test_ce_and_pe_strike_band_itm_atm_otm(self):
        strikes = [24300, 24350, 24400, 24450, 24500, 24550]
        rows = _both_sides("NIFTY", strikes, exchange="NSE", instrument="OPTIDX")
        rows.append({"SEM_EXM_EXCH_ID": "NSE", "SEM_SMST_SECURITY_ID": "13", "SEM_INSTRUMENT_NAME": "INDEX",
                     "SEM_TRADING_SYMBOL": "NIFTY", "SM_SYMBOL_NAME": "Nifty 50"})
        fetcher = _fetcher(rows)
        instrument = _instrument("NIFTY", "index_options")

        ce = fetcher.resolve_strike_band(instrument, 24460.0, "CE", now=NON_EXPIRY_DAY)
        pe = fetcher.resolve_strike_band(instrument, 24460.0, "PE", now=NON_EXPIRY_DAY)

        self.assertEqual(
            [(BAND_ITM_PLUS_1, 24400), (BAND_ATM, 24450), (BAND_OTM_PLUS_1, 24500)],
            [(c["strike_band"], c["strike"]) for c in ce],
        )
        self.assertEqual(
            [(BAND_ITM_PLUS_1, 24500), (BAND_ATM, 24450), (BAND_OTM_PLUS_1, 24400)],
            [(c["strike_band"], c["strike"]) for c in pe],
        )
        self.assertTrue(all(c["exchange_segment"] == "NSE_FNO" for c in ce + pe))
        self.assertTrue(all(c["expiry"] == "27OCT2026" for c in ce + pe))
        self.assertEqual({"CE"}, {c["option_type"] for c in ce})
        self.assertEqual({"PE"}, {c["option_type"] for c in pe})
        self.assertEqual(13, fetcher.data_manager.resolve_underlying("NIFTY", NON_EXPIRY_DAY.date()).security_id)

    def test_band_uses_listed_uneven_strikes(self):
        strikes = [2900, 2950, 3000, 3100, 3200]
        fetcher = _fetcher(_both_sides("RELIANCE", strikes, exchange="NSE", instrument="OPTSTK"))
        band = fetcher.resolve_strike_band(
            _instrument("RELIANCE", "nifty50_stock_options"), 3040.0, "CE", now=NON_EXPIRY_DAY
        )
        self.assertEqual([2950, 3000, 3100], [c["strike"] for c in band])

    def test_entry_universe_can_resolve_only_the_listed_itm_plus_one(self):
        strikes = [24300, 24350, 24400, 24450, 24500, 24550]
        fetcher = _fetcher(_both_sides("NIFTY", strikes, exchange="NSE", instrument="OPTIDX"))
        instrument = _instrument("NIFTY", "index_options")

        ce = fetcher.resolve_strike_band(
            instrument, 24460.0, "CE", now=NON_EXPIRY_DAY, bands=(BAND_ITM_PLUS_1,)
        )
        pe = fetcher.resolve_strike_band(
            instrument, 24460.0, "PE", now=NON_EXPIRY_DAY, bands=(BAND_ITM_PLUS_1,)
        )

        assert [(contract["strike_band"], contract["strike"]) for contract in ce] == [(BAND_ITM_PLUS_1, 24400)]
        assert [(contract["strike_band"], contract["strike"]) for contract in pe] == [(BAND_ITM_PLUS_1, 24500)]

    def test_band_skips_missing_neighbour_at_chain_edge(self):
        fetcher = _fetcher(_both_sides("GOLD", [72000, 72500]))
        band = fetcher.resolve_strike_band(_instrument("GOLD"), 71900.0, "CE", now=NON_EXPIRY_DAY)
        self.assertEqual([(BAND_ATM, 72000), (BAND_OTM_PLUS_1, 72500)], [(c["strike_band"], c["strike"]) for c in band])
        self.assertEqual(
            [],
            fetcher.resolve_strike_band(
                _instrument("GOLD"), 71900.0, "CE", now=NON_EXPIRY_DAY, bands=(BAND_ITM_PLUS_1,)
            ),
        )

    def test_selects_nearest_active_expiry_and_skips_expired_contracts(self):
        rows = (
            _both_sides("GOLD", [72000], expiry="2026-09-25 00:00:00", start_id=1)
            + _both_sides("GOLD", [72000], expiry=NEXT_EXPIRY, start_id=100)
            + _both_sides("GOLD", [72000], expiry=EXPIRY, start_id=200)
        )
        contract = _fetcher(rows)._resolve_option_contract(_instrument("GOLD"), 72000.0, "CE", now=NON_EXPIRY_DAY)
        self.assertEqual("27OCT2026", contract["expiry"])
        self.assertEqual(200, contract["security_id"])

    def test_expiry_day_skips_same_day_contract_and_uses_next_expiry(self):
        strikes = [24400, 24450, 24500]
        rows = (
            _both_sides("NIFTY", strikes, exchange="NSE", instrument="OPTIDX", expiry=EXPIRY, start_id=1)
            + _both_sides("NIFTY", strikes, exchange="NSE", instrument="OPTIDX", expiry=NEXT_EXPIRY, start_id=5000)
        )
        fetcher = _fetcher(rows)
        instrument = _instrument("NIFTY", "index_options")

        for side in ("CE", "PE"):
            with self.subTest(side=side):
                band = fetcher.resolve_strike_band(instrument, 24450.0, side, now=EXPIRY_DAY)
                self.assertEqual(3, len(band))
                self.assertEqual({"24NOV2026"}, {c["expiry"] for c in band})
                self.assertEqual(BAND_ATM, band[1]["strike_band"])
                self.assertEqual(24450, band[1]["strike"])

        before = fetcher.resolve_strike_band(instrument, 24450.0, "CE", now=datetime(2026, 10, 26, 15, 0, tzinfo=IST))
        self.assertEqual({"27OCT2026"}, {c["expiry"] for c in before})

    def test_expiry_day_uses_ist_date(self):
        # 2026-10-26 19:00 UTC is already 27-Oct 00:30 IST (expiry day) -> next expiry.
        from datetime import timezone

        strikes = [72000]
        rows = _both_sides("GOLD", strikes, expiry=EXPIRY, start_id=1) + _both_sides(
            "GOLD", strikes, expiry=NEXT_EXPIRY, start_id=100
        )
        fetcher = _fetcher(rows)
        utc_now = datetime(2026, 10, 26, 19, 0, tzinfo=timezone.utc)
        contract = fetcher._resolve_option_contract(_instrument("GOLD"), 72000.0, "CE", now=utc_now)
        self.assertEqual("24NOV2026", contract["expiry"])

    def test_commodity_root_matching_is_exact(self):
        rows = _both_sides("GOLDM", [72000], start_id=1) + _both_sides("GOLD", [72000], start_id=100)
        contract = _fetcher(rows)._resolve_option_contract(_instrument("GOLD"), 72000.0, "CE", now=NON_EXPIRY_DAY)
        self.assertEqual(100, contract["security_id"])

    def test_crude_oil_resolves_from_custom_symbol(self):
        rows = _both_sides("CRUDEOIL", [8800, 8850, 8900])
        contract = _fetcher(rows)._resolve_option_contract(_instrument("CRUDE OIL"), 8849.0, "CE", now=NON_EXPIRY_DAY)
        self.assertEqual("CRUDEOIL-27Oct2026-8850-CE", contract["option_symbol"])

    def test_sensex_resolves_on_bse(self):
        rows = _both_sides("SENSEX", [81100, 81200, 81300], exchange="BSE", instrument="OPTIDX",
                           sm_symbol_name="BSXOPT")
        contract = _fetcher(rows)._resolve_option_contract(
            _instrument("SENSEX", "index_options"), 81234.0, "PE", now=NON_EXPIRY_DAY
        )
        self.assertEqual(81200, contract["strike"])
        self.assertEqual("BSE_FNO", contract["exchange_segment"])

    def test_band_logs_selection(self):
        fetcher = _fetcher(_both_sides("GOLD", [71500, 72000, 72500]))
        with self.assertLogs("atm_options_fetcher", level=logging.INFO) as logs:
            fetcher.resolve_strike_band(_instrument("GOLD"), 72000.0, "CE", now=NON_EXPIRY_DAY)
        self.assertIn("[GOLD] CE band", "\n".join(logs.output))
        self.assertIn("ITM+1=71500 ATM=72000 OTM+1=72500", "\n".join(logs.output))

    def test_unknown_symbol_returns_no_contracts(self):
        fetcher = _fetcher(_both_sides("GOLD", [72000]))
        self.assertEqual([], fetcher.resolve_strike_band(_instrument("PLATINUM"), 100.0, "CE", now=NON_EXPIRY_DAY))


if __name__ == "__main__":
    unittest.main()
