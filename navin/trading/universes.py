"""Static universes the scanner can walk without a paid data feed."""

from __future__ import annotations

# Nasdaq-100 constituents (common shares). Kept local so a scan still works
# when a directory API is down. Refresh periodically from public lists.
NASDAQ100: tuple[str, ...] = (
    "AAPL", "ABNB", "ADBE", "ADI", "ADP", "ADSK", "AEP", "AMAT", "AMD", "AMGN",
    "AMZN", "ANSS", "APP", "ARM", "ASML", "AVGO", "AXON", "AZN", "BIIB", "BKNG",
    "BKR", "CCEP", "CDNS", "CDW", "CEG", "CHTR", "CMCSA", "COST", "CPRT", "CRWD",
    "CSCO", "CSGP", "CSX", "CTAS", "CTSH", "DASH", "DDOG", "DXCM", "EA", "EXC",
    "FANG", "FAST", "FTNT", "GEHC", "GFS", "GILD", "GOOG", "GOOGL", "HON", "IDXX",
    "INTC", "INTU", "ISRG", "KDP", "KHC", "KLAC", "LIN", "LRCX", "LULU", "MAR",
    "MCHP", "MDB", "MDLZ", "MELI", "META", "MNST", "MRVL", "MSFT", "MSTR", "MU",
    "NFLX", "NVDA", "NXPI", "ODFL", "ON", "ORLY", "PANW", "PAYX", "PCAR", "PDD",
    "PEP", "PLTR", "PYPL", "QCOM", "REGN", "ROP", "ROST", "SBUX", "SNPS", "TEAM",
    "TMUS", "TSLA", "TTD", "TTWO", "TXN", "VRSK", "VRTX", "WBD", "WDAY", "XEL",
    "ZS",
)

CRYPTO_MAJORS: tuple[str, ...] = (
    "BTC-USD", "ETH-USD", "SOL-USD", "BNB-USD", "XRP-USD", "ADA-USD",
    "AVAX-USD", "DOGE-USD", "DOT-USD", "LINK-USD", "MATIC-USD", "ATOM-USD",
    "LTC-USD", "UNI-USD", "NEAR-USD", "APT-USD", "SUI-USD", "ARB-USD",
)

# Public CoinGecko NFT ids. Floor is fetched live; never invented.
NFT_COLLECTIONS: tuple[dict[str, str], ...] = (
    {"symbol": "NFT-BAYC", "id": "bored-ape-yacht-club", "name": "Bored Ape Yacht Club"},
    {"symbol": "NFT-PUNKS", "id": "cryptopunks", "name": "CryptoPunks"},
    {"symbol": "NFT-PUDGY", "id": "pudgy-penguins", "name": "Pudgy Penguins"},
    {"symbol": "NFT-AZUKI", "id": "azuki", "name": "Azuki"},
    {"symbol": "NFT-CLONE", "id": "clone-x-x-takashi-murakami", "name": "CloneX"},
    {"symbol": "NFT-DOODLE", "id": "doodles-official", "name": "Doodles"},
    {"symbol": "NFT-MOON", "id": "moonbirds", "name": "Moonbirds"},
    {"symbol": "NFT-OTHERDEED", "id": "otherdeed-for-otherside", "name": "Otherdeed"},
)

# Listed property tape (REITs / foncières). Direct deeds are out of V1.
REALESTATE_GLOBAL: tuple[str, ...] = (
    "O", "PLD", "AMT", "EQIX", "SPG", "WELL", "VNQ",
    "LI.PA", "COV.PA", "LAND.L", "SGRO.L", "VNA.DE",
    "8951.T", "GMG.AX", "C38U.SI", "REI-UN.TO", "0823.HK",
)

COUNTRY_EQUITIES: dict[str, tuple[str, ...]] = {
    "US": NASDAQ100[:40],
    "CA": ("RY.TO", "TD.TO", "SHOP", "ENB.TO", "CNR.TO", "EWC"),
    "BR": ("PBR", "VALE", "ITUB", "EWZ"),
    "MX": ("AMX", "WALMEX.MX", "EWW"),
    "AR": ("ARGT", "MELI"),
    "CL": ("ECH",),
    "CO": ("GXG",),
    "PE": ("EPU",),
    "GB": ("SHEL.L", "AZN.L", "HSBA.L", "ULVR.L", "BP.L", "GSK.L", "EWU"),
    "FR": ("MC.PA", "OR.PA", "AIR.PA", "SAN.PA", "TTE.PA", "SU.PA", "BNP.PA", "AI.PA", "EWQ"),
    "DE": ("SAP.DE", "SIE.DE", "ALV.DE", "DTE.DE", "BMW.DE", "BAS.DE", "EWG"),
    "NL": ("ASML", "INGA.AS", "AD.AS", "EWN"),
    "BE": ("ABI.BR", "EWK"),
    "ES": ("SAN.MC", "ITX.MC", "EWP"),
    "IT": ("ENEL.MI", "ISP.MI", "EWI"),
    "CH": ("NESN.SW", "ROG.SW", "NOVN.SW", "EWL"),
    "SE": ("ERIC", "VOLV-B.ST", "EWD"),
    "NO": ("EQNR.OL", "ENOR"),
    "DK": ("NOVO-B.CO", "EDEN"),
    "FI": ("NOKIA.HE", "EFNL"),
    "AT": ("EWO",),
    "PT": ("PGAL",),
    "IE": ("ICLR", "EIRL"),
    "PL": ("EPOL",),
    "CZ": ("EZJ",),
    "GR": ("GREK",),
    "TR": ("TUR",),
    "RU": ("ERUS",),
    "JP": ("7203.T", "6758.T", "9984.T", "8306.T", "6861.T", "EWJ"),
    "CN": ("BABA", "PDD", "JD", "BIDU", "MCHI"),
    "HK": ("0700.HK", "9988.HK", "0941.HK", "EWH"),
    "TW": ("TSM", "2330.TW", "EWT"),
    "KR": ("005930.KS", "000660.KS", "EWY"),
    "IN": ("RELIANCE.NS", "TCS.NS", "INFY", "HDB", "INDA"),
    "SG": ("D05.SI", "EWS"),
    "AU": ("BHP.AX", "CBA.AX", "CSL.AX", "EWA"),
    "NZ": ("ENZL",),
    "ID": ("BBCA.JK", "EIDO"),
    "TH": ("THD",),
    "MY": ("EWM",),
    "PH": ("EPHE",),
    "VN": ("VNM",),
    "PK": ("PAK",),
    "AE": ("UAE",),
    "SA": ("2222.SR", "KSA"),
    "QA": ("QAT",),
    "KW": ("KWT",),
    "IL": ("TEVA", "CHKP", "EIS"),
    "EG": ("EGPT",),
    "ZA": ("NPN.JO", "EZA"),
    "NG": ("NGE",),
    "KE": ("KEF",),
}

COUNTRY_REITS: dict[str, tuple[str, ...]] = {
    "US": ("O", "PLD", "AMT", "EQIX", "VNQ"),
    "FR": ("LI.PA", "COV.PA"),
    "GB": ("LAND.L", "SGRO.L"),
    "DE": ("VNA.DE",),
    "JP": ("8951.T",),
    "AU": ("GMG.AX",),
    "SG": ("C38U.SI",),
    "CA": ("REI-UN.TO",),
    "HK": ("0823.HK",),
    "NL": ("ECMPA.AS",),
}

SECTOR_HINTS: dict[str, str] = {
    "AAPL": "tech", "MSFT": "tech", "NVDA": "semis", "AMD": "semis", "AVGO": "semis",
    "AMAT": "semis", "LRCX": "semis", "KLAC": "semis", "QCOM": "semis", "MU": "semis",
    "INTC": "semis", "TSM": "semis", "ASML": "semis", "ARM": "semis", "MRVL": "semis",
    "AMZN": "consumer", "TSLA": "auto", "META": "tech", "GOOG": "tech", "GOOGL": "tech",
    "NFLX": "media", "COST": "retail", "PEP": "staples", "SBUX": "consumer",
    "ADBE": "software", "CRM": "software", "NOW": "software", "INTU": "software",
    "PANW": "software", "CRWD": "software", "FTNT": "software", "ZS": "software",
    "DDOG": "software", "TEAM": "software", "WDAY": "software", "SNPS": "software",
    "CDNS": "software", "PLTR": "software", "APP": "software", "MDB": "software",
    "AMGN": "biotech", "GILD": "biotech", "VRTX": "biotech", "REGN": "biotech",
    "ISRG": "health", "DXCM": "health", "IDXX": "health", "GEHC": "health",
    "BKNG": "travel", "MAR": "travel", "ABNB": "travel", "MELI": "consumer",
    "PYPL": "fintech", "COIN": "fintech", "MSTR": "fintech",
    "BTC-USD": "crypto", "ETH-USD": "crypto", "SOL-USD": "crypto",
    "BNB-USD": "crypto", "XRP-USD": "crypto", "ADA-USD": "crypto",
    "AVAX-USD": "crypto", "DOGE-USD": "crypto", "DOT-USD": "crypto",
    "LINK-USD": "crypto", "MATIC-USD": "crypto", "ATOM-USD": "crypto",
    "LTC-USD": "crypto", "UNI-USD": "crypto", "NEAR-USD": "crypto",
    "O": "realestate", "PLD": "realestate", "AMT": "realestate", "EQIX": "realestate",
    "SPG": "realestate", "WELL": "realestate", "VNQ": "realestate",
    "LI.PA": "realestate", "COV.PA": "realestate", "LAND.L": "realestate",
    "SGRO.L": "realestate", "VNA.DE": "realestate", "8951.T": "realestate",
    "GMG.AX": "realestate", "C38U.SI": "realestate", "REI-UN.TO": "realestate",
}

UNIVERSES: dict[str, tuple[str, ...]] = {
    "NASDAQ100": NASDAQ100,
    "CRYPTO": CRYPTO_MAJORS,
    "CRYPTO_MAJORS": CRYPTO_MAJORS,
    "REALESTATE": REALESTATE_GLOBAL,
    "NFT": tuple(row["symbol"] for row in NFT_COLLECTIONS),
}


def sector_of(symbol: str) -> str:
    key = (symbol or "").strip().upper()
    if key.startswith("NFT-"):
        return "nft"
    return SECTOR_HINTS.get(key, "other")


def asset_kind(symbol: str) -> str:
    key = (symbol or "").strip().upper()
    if key.startswith("NFT-"):
        return "nft"
    if key.endswith("-USD") or key in CRYPTO_MAJORS:
        return "crypto"
    if sector_of(key) == "realestate":
        return "realestate"
    return "equities"


def nft_id_for(symbol: str) -> str | None:
    key = (symbol or "").strip().upper()
    for row in NFT_COLLECTIONS:
        if row["symbol"] == key:
            return row["id"]
    return None


def expand_universe(name: str, extra: list[str] | None = None) -> list[str]:
    key = (name or "").strip().upper().replace(" ", "")
    aliases = {
        "NASDAQ": "NASDAQ100",
        "NDX": "NASDAQ100",
        "US": "NASDAQ100",
        "CRYPTO": "CRYPTO_MAJORS",
        "BTC": "CRYPTO_MAJORS",
        "IMMO": "REALESTATE",
        "REIT": "REALESTATE",
        "MIXED": "NASDAQ100",
    }
    key = aliases.get(key, key)
    base = list(UNIVERSES.get(key, ()))
    seen = {item.upper() for item in base}
    for raw in extra or []:
        symbol = str(raw or "").strip().upper()
        if not symbol or symbol in seen:
            continue
        seen.add(symbol)
        base.append(symbol)
    return base


def symbols_for_mandate(
    domains: list[str],
    countries: list[str],
    extra: list[str] | None = None,
    tapes: list[dict[str, str]] | None = None,
) -> list[str]:
    """Build the scan tape from the active mandate. No invented names."""
    from navin.trading.mandate import DOMAINS

    if tapes:
        out: list[str] = []
        seen: set[str] = set()
        for tape in tapes:
            domain = str((tape or {}).get("domain") or "").strip().lower()
            country = str((tape or {}).get("country") or "").strip().upper()
            for symbol in symbols_for_mandate([domain], [country] if country else [], None):
                key = symbol.strip().upper()
                if key and key not in seen:
                    seen.add(key)
                    out.append(key)
        for raw in extra or []:
            key = str(raw or "").strip().upper()
            if key and key not in seen:
                seen.add(key)
                out.append(key)
        return out

    wanted = [item for item in domains if item in DOMAINS] or ["equities"]
    places = [code.upper() for code in countries if code]
    out: list[str] = []
    seen: set[str] = set()

    def _add(symbol: str) -> None:
        key = (symbol or "").strip().upper()
        if not key or key in seen:
            return
        seen.add(key)
        out.append(key)

    if "equities" in wanted:
        if not places:
            for symbol in NASDAQ100[:40]:
                _add(symbol)
        else:
            per = 2 if len(places) > 12 else 4 if len(places) > 6 else 40
            buckets = [list(COUNTRY_EQUITIES.get(code, ())[:per]) for code in places]
            index = 0
            while True:
                added = False
                for bucket in buckets:
                    if index < len(bucket):
                        _add(bucket[index])
                        added = True
                if not added:
                    break
                index += 1
    if "crypto" in wanted:
        for symbol in CRYPTO_MAJORS:
            _add(symbol)
    if "nft" in wanted:
        for row in NFT_COLLECTIONS:
            _add(row["symbol"])
    if "realestate" in wanted:
        if places:
            per = 1 if len(places) > 8 else 3
            for code in places:
                rows = COUNTRY_REITS.get(code) or (("VNQ",) if "equities" not in wanted else ())
                for symbol in rows[:per]:
                    _add(symbol)
            if not any(asset_kind(item) == "realestate" or item in REALESTATE_GLOBAL for item in out):
                _add("VNQ")
        else:
            for symbol in REALESTATE_GLOBAL:
                _add(symbol)
    for raw in extra or []:
        _add(str(raw))
    return out
