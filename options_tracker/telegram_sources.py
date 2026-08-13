from dataclasses import dataclass


@dataclass(frozen=True)
class TelegramSource:
    ref: int | str
    name: str
    category: str


SOURCES = (
    TelegramSource(-1003141350480, "Derivates F&O Investology", "TIPS"),
    TelegramSource(-1004353079522, "Charts Premium RJ Trader", "TIPS"),
    TelegramSource(-1002394235269, "SRA360 stocks", "TIPS"),
    TelegramSource(-1002426679615, "SRA360 Index FNO", "TIPS"),
    TelegramSource(-1003858490010, "Elephant PRO", "TIPS"),
    TelegramSource(-1003770810999, "Family May 2026", "TIPS"),
    TelegramSource("indiantraderxp", "IndianTraderXP", "TIPS"),
    TelegramSource(-1003121140019, "Investology Equities Long", "TIPS"),
    TelegramSource(-1003109328674, "Investology Automated Alerts", "TIPS"),
    TelegramSource(-1003148687413, "Investology Equity Intra", "TIPS"),
    TelegramSource(-1003101198634, "Investology Commodity", "TIPS"),
    TelegramSource("ramdhs8", "RamDHS", "TIPS"),
    TelegramSource("Shortterm01", "ShortTerm", "DISCUSSION"),
    TelegramSource(-1004439083422, "RJ Trader Discussion group", "DISCUSSION"),
    TelegramSource(-1001320942683, "Sunil V Tilani", "DISCUSSION"),
    TelegramSource("TradeTheTrend_99", "MFS", "DISCUSSION"),
    TelegramSource("EquiAlpha_stocks", "EquiAlpha", "DISCUSSION"),
    TelegramSource(-1003170347221, "Investology Discussion", "DISCUSSION"),
    TelegramSource("SwingWisely", "SwingWise", "DISCUSSION"),
    TelegramSource("The_ChartWizard", "ChartWizard", "DISCUSSION"),
    TelegramSource("BeatTheStreetnews", "BeatTheStreet News", "NEWS"),
    TelegramSource("earnings_pulse", "Earnings Pulse", "NEWS"),
)