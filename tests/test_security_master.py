import io
import unittest
from datetime import date
from unittest.mock import MagicMock, patch

from security_master import (
    NEEDED_COLUMNS,
    KIND_COMMODITY,
    KIND_INDEX,
    KIND_STOCK,
    SecurityMasterCache,
    SecurityMasterIndex,
    UniverseEntry,
    UniverseSpec,
    iter_needed_columns,
    stream_compact_security_master,
)

HEADER = (
    "SEM_EXM_EXCH_ID,SEM_SEGMENT,SEM_SMST_SECURITY_ID,SEM_INSTRUMENT_NAME,SEM_EXPIRY_CODE,SEM_TRADING_SYMBOL,"
    "SEM_LOT_UNITS,SEM_CUSTOM_SYMBOL,SEM_EXPIRY_DATE,SEM_STRIKE_PRICE,SEM_OPTION_TYPE,SEM_TICK_SIZE,"
    "SEM_EXPIRY_FLAG,SEM_EXCH_INSTRUMENT_TYPE,SEM_SERIES,SM_SYMBOL_NAME"
)
ROWS = [
    "NSE,I,13,INDEX,0,NIFTY,1,Nifty 50,,0,XX,0,NA,INDEX,X,Nifty 50",
    "NSE,E,2885,EQUITY,0,RELIANCE,1,Reliance,,0,XX,0.05,NA,ES,EQ,RELIANCE INDUSTRIES LTD",
    "NSE,E,9999,EQUITY,0,RPOWER,1,Reliance Power,,0,XX,0.05,NA,ES,EQ,RELIANCE POWER LTD",
    "NSE,D,100,OPTIDX,0,NIFTY-Oct2026-24400-CE,75,NIFTY 27 OCT 24400 CALL,2026-10-27 14:30:00,24400.00000,CE,5,M,OP,NA,",
    "NSE,D,101,OPTIDX,0,NIFTY-Oct2026-24450-CE,75,NIFTY 27 OCT 24450 CALL,2026-10-27 14:30:00,24450.00000,CE,5,M,OP,NA,",
    "NSE,D,102,OPTIDX,0,NIFTY-Oct2026-24450-PE,75,NIFTY 27 OCT 24450 PUT,2026-10-27 14:30:00,24450.00000,PE,5,M,OP,NA,",
    "NSE,D,103,OPTIDX,0,NIFTY-Nov2026-24450-CE,75,NIFTY 24 NOV 24450 CALL,2026-11-24 14:30:00,24450.00000,CE,5,M,OP,NA,",
    "NSE,D,104,OPTIDX,0,NIFTY-Oct2026-24450-CE,75,NIFTY 06 OCT 24450 CALL,2026-10-06 14:30:00,24450.00000,CE,5,W,OP,NA,",
    "NSE,D,200,OPTSTK,0,RELIANCE-Oct2026-3000-CE,500,RELIANCE 27 OCT 3000 CALL,2026-10-27 14:30:00,3000.00000,CE,5,M,OP,NA,",
    "NSE,D,300,OPTIDX,0,FINNIFTY-Oct2026-23000-CE,65,FINNIFTY,2026-10-27 14:30:00,23000.00000,CE,5,M,OP,NA,",
    "NSE,D,400,FUTIDX,0,NIFTY-Oct2026-FUT,75,NIFTY OCT FUT,2026-10-27 14:30:00,-0.01000,XX,0.1,M,FUT,NA,",
    "MCX,M,500,FUTCOM,0,GOLD-04Dec2026-FUT,1,GOLD DEC FUT,2026-12-04 23:59:00,-0.01000,XX,1,M,FUT,NA,GOLD",
    "MCX,M,501,FUTCOM,0,GOLD-05Oct2026-FUT,1,GOLD OCT FUT,2026-10-05 23:59:00,-0.01000,XX,1,M,FUT,NA,GOLD",
    "MCX,M,502,OPTFUT,0,GOLD-24Nov2026-72000-CE,1,GOLD 24 NOV 72000 CALL,2026-11-24 23:59:00,72000.00000,CE,1,M,OP,NA,GOLD",
    "MCX,M,503,OPTFUT,0,GOLDM-24Nov2026-72000-CE,1,GOLDM,2026-11-24 23:59:00,72000.00000,CE,1,M,OP,NA,GOLDM",
]


def _universe():
    return UniverseSpec(
        [
            UniverseEntry("NIFTY", "NIFTY", "NSE", KIND_INDEX),
            UniverseEntry("RELIANCE", "RELIANCE", "NSE", KIND_STOCK),
            UniverseEntry("GOLD", "GOLD", "MCX", KIND_COMMODITY),
        ]
    )


def _rows():
    return iter_needed_columns(iter([HEADER, *ROWS]))


class SecurityMasterFilterTests(unittest.TestCase):
    def test_only_needed_columns_are_kept(self):
        row = next(_rows())
        self.assertEqual(set(NEEDED_COLUMNS), set(row))
        self.assertNotIn("SEM_TICK_SIZE", row)

    def test_index_keeps_only_universe_nearest_expiry_contracts(self):
        index = SecurityMasterIndex.build(_rows(), _universe(), date(2026, 10, 5))
        self.assertEqual(
            {("NIFTY", "CE"), ("NIFTY", "PE"), ("RELIANCE", "CE"), ("GOLD", "CE")}, set(index.chains)
        )
        expiry, contracts = index.option_chain("NIFTY", "CE")
        # Weekly 06-Oct is nearest (and not today) -> only that expiry is kept.
        self.assertEqual(date(2026, 10, 6), expiry)
        self.assertEqual([104], [c.security_id for c in contracts])
        self.assertEqual(4, index.contract_count)
        self.assertEqual([502], [c.security_id for c in index.option_chain("GOLD", "CE")[1]])
        self.assertEqual(13, index.underlying("NIFTY").security_id)
        self.assertEqual("IDX_I", index.underlying("NIFTY").exchange_segment)
        self.assertEqual(2885, index.underlying("RELIANCE").security_id)
        # MCX option settles into the nearest future expiring on/after the option expiry.
        self.assertEqual(500, index.underlying("GOLD").security_id)
        self.assertEqual(len(ROWS), index.rows_scanned)

    def test_expiry_day_drops_same_day_contracts(self):
        index = SecurityMasterIndex.build(_rows(), _universe(), date(2026, 10, 6))
        expiry, contracts = index.option_chain("NIFTY", "CE")
        self.assertEqual(date(2026, 10, 27), expiry)
        self.assertEqual([100, 101], [c.security_id for c in contracts])
        on_monthly_expiry = SecurityMasterIndex.build(_rows(), _universe(), date(2026, 10, 27))
        self.assertEqual(date(2026, 11, 24), on_monthly_expiry.option_chain("NIFTY", "CE")[0])


class SecurityMasterCacheTests(unittest.TestCase):
    def test_loads_once_per_day_and_shares_index(self):
        source = MagicMock(side_effect=lambda: _rows())
        cache = SecurityMasterCache(row_source=source)
        first = cache.get_index(_universe(), date(2026, 10, 5))
        self.assertIs(first, cache.get_index(_universe(), date(2026, 10, 5)))
        self.assertEqual(1, source.call_count)
        self.assertEqual(1, cache.loads)

        second = cache.get_index(_universe(), date(2026, 10, 6))
        self.assertIsNot(first, second)
        self.assertEqual(2, source.call_count)

    def test_release_frees_index_and_reloads_on_demand(self):
        cache = SecurityMasterCache(row_source=lambda: _rows())
        cache.get_index(_universe(), date(2026, 10, 5))
        self.assertTrue(cache.status()["loaded"])
        self.assertTrue(cache.release())
        self.assertFalse(cache.status()["loaded"])
        self.assertFalse(cache.release())
        self.assertIsNotNone(cache.get_index(_universe(), date(2026, 10, 5)))

    def test_failed_load_backs_off_instead_of_retrying_every_call(self):
        source = MagicMock(side_effect=OSError("network down"))
        cache = SecurityMasterCache(row_source=source, retry_seconds=300)
        self.assertIsNone(cache.get_index(_universe(), date(2026, 10, 5)))
        self.assertIsNone(cache.get_index(_universe(), date(2026, 10, 5)))
        self.assertEqual(1, source.call_count)

    def test_streaming_loader_never_buffers_whole_file(self):
        response = MagicMock()
        response.__enter__.return_value = response
        response.iter_lines.return_value = iter([HEADER, *ROWS])
        with patch("security_master.requests.get", return_value=response) as get:
            rows = list(stream_compact_security_master("https://example.invalid/master.csv"))
        self.assertTrue(get.call_args.kwargs["stream"])
        response.iter_lines.assert_called_once()
        self.assertEqual(len(ROWS), len(rows))


if __name__ == "__main__":
    unittest.main()
