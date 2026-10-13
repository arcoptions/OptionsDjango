"""Decode Dhan CAS packets and produce a conservative Nifty close nowcast."""

from __future__ import annotations

import csv
import io
import json
import math
import os
import statistics
import struct
import threading
from collections import deque
from dataclasses import asdict, dataclass
from datetime import datetime, time as clock_time
from pathlib import Path
from typing import Callable
from urllib.parse import urlencode

import requests
import websocket
from django.utils import timezone

from .stock_data import equity_ids, load_master

CAS_SUBSCRIBE_CODE = 23
CAS_UNSUBSCRIBE_CODE = 24
CAS_DISCONNECT_CODE = 12
CAS_PACKET_SIZE = 128
CAS_INDEX_PACKET_SIZE = 33
NIFTY_50_CSV_URL = "https://nsearchives.nseindia.com/content/indices/ind_nifty50list.csv"
CAS_CONNECT_FROM = clock_time(15, 14)
CAS_CONNECT_UNTIL = clock_time(15, 35)
DEFAULT_NIFTY_SYMBOLS = (
    "ADANIENT", "ADANIPORTS", "APOLLOHOSP", "ASIANPAINT", "AXISBANK",
    "BAJAJ-AUTO", "BAJAJFINSV", "BAJFINANCE", "BEL", "BHARTIARTL",
    "CIPLA", "COALINDIA", "DRREDDY", "EICHERMOT", "ETERNAL",
    "GRASIM", "HCLTECH", "HDFCBANK", "HDFCLIFE", "HEROMOTOCO",
    "HINDALCO", "HINDUNILVR", "ICICIBANK", "INDUSINDBK", "INFY",
    "ITC", "JIOFIN", "JSWSTEEL", "KOTAKBANK", "LT", "M&M", "MARUTI",
    "NESTLEIND", "NTPC", "ONGC", "POWERGRID", "RELIANCE", "SBILIFE",
    "SBIN", "SHRIRAMFIN", "SUNPHARMA", "TATACONSUM", "TATAMOTORS",
    "TATASTEEL", "TCS", "TECHM", "TITAN", "TRENT", "ULTRACEMCO", "WIPRO",
)


def cas_connection_state(now: datetime) -> tuple[str, str]:
    """Describe whether a controlled collector should hold the CAS socket open."""
    local_now = timezone.localtime(now)
    if local_now.weekday() >= 5:
        return "ARMED", "Weekend: waiting for the next trading session"
    local_time = local_now.time().replace(tzinfo=None)
    if local_time < CAS_CONNECT_FROM:
        return "ARMED", "Collector armed; Dhan connection starts at 3:14 PM"
    if local_time <= CAS_CONNECT_UNTIL:
        return "OPEN", "CAS connection window is open"
    return "COMPLETE", "Today's CAS connection window has ended"


def reconnect_delay_seconds(
    error: object | None,
    *,
    received_packet: bool,
    configured_delay: float,
) -> float:
    """Choose a reconnect delay that cannot exhaust Dhan's socket quota."""
    status_code = getattr(error, "status_code", None)
    if status_code == 429 or "429 Too Many Requests" in str(error):
        return max(120.0, configured_delay)
    if not received_packet:
        return max(45.0, configured_delay)
    return max(10.0, configured_delay)


@dataclass(frozen=True)
class DepthLevel:
    bid_quantity: int
    ask_quantity: int
    bid_orders: int
    ask_orders: int
    bid_price: float
    ask_price: float


@dataclass(frozen=True)
class CasInstrumentPacket:
    response_code: int
    message_length: int
    exchange_segment: int
    security_id: int
    indicative_equilibrium_price: float
    total_buy_quantity: int
    total_sell_quantity: int
    depth: tuple[DepthLevel, ...]
    indicative_imbalance_quantity: int
    reference_price: float


@dataclass(frozen=True)
class CasIndexPacket:
    message_length: int
    response_code: int
    exchange_segment: int
    index_name: str
    indicative_close: float


def parse_cas_instrument_packet(data: bytes) -> CasInstrumentPacket:
    """Parse one documented 128-byte Dhan CAS instrument packet."""
    if len(data) != CAS_PACKET_SIZE:
        raise ValueError(f"CAS instrument packet must be 128 bytes, received {len(data)}")
    response_code, message_length, exchange_segment, security_id = struct.unpack_from(
        "<BHBI", data, 0
    )
    iep, total_buy, total_sell = struct.unpack_from("<fii", data, 8)
    depth = tuple(
        DepthLevel(*struct.unpack_from("<iihhff", data, 20 + index * 20))
        for index in range(5)
    )
    imbalance, reference_price = struct.unpack_from("<if", data, 120)
    return CasInstrumentPacket(
        response_code=response_code,
        message_length=message_length,
        exchange_segment=exchange_segment,
        security_id=security_id,
        indicative_equilibrium_price=iep,
        total_buy_quantity=total_buy,
        total_sell_quantity=total_sell,
        depth=depth,
        indicative_imbalance_quantity=imbalance,
        reference_price=reference_price,
    )


def parse_cas_index_packet(data: bytes) -> CasIndexPacket:
    """Parse the special 33-byte CAS index packet with length before code."""
    if len(data) != CAS_INDEX_PACKET_SIZE:
        raise ValueError(f"CAS index packet must be 33 bytes, received {len(data)}")
    message_length, response_code, exchange_segment = struct.unpack_from("<HBB", data, 0)
    raw_name = struct.unpack_from("<21s", data, 4)[0]
    index_name = raw_name.split(b"\0", 1)[0].decode("ascii", errors="replace").strip()
    indicative_close = struct.unpack_from("<d", data, 25)[0]
    return CasIndexPacket(
        message_length=message_length,
        response_code=response_code,
        exchange_segment=exchange_segment,
        index_name=index_name,
        indicative_close=indicative_close,
    )


def decode_cas_packet(data: bytes) -> CasInstrumentPacket | CasIndexPacket:
    """Decode a CAS packet by its documented fixed size."""
    if len(data) == CAS_PACKET_SIZE:
        return parse_cas_instrument_packet(data)
    if len(data) == CAS_INDEX_PACKET_SIZE:
        return parse_cas_index_packet(data)
    raise ValueError(f"Unsupported CAS packet length: {len(data)}")


class NiftyCloseNowcaster:
    """Nowcast the final close around the exchange's indicative index value."""

    def __init__(self, max_points: int = 600) -> None:
        self._index_points: deque[tuple[datetime, float]] = deque(maxlen=max_points)
        self._instruments: dict[int, CasInstrumentPacket] = {}

    def update_instrument(self, packet: CasInstrumentPacket) -> None:
        self._instruments[packet.security_id] = packet

    def update_index(self, packet: CasIndexPacket, observed_at: datetime) -> dict[str, object]:
        if not math.isfinite(packet.indicative_close) or packet.indicative_close <= 0:
            raise ValueError(f"Invalid indicative index close: {packet.indicative_close}")
        self._index_points.append((observed_at, packet.indicative_close))
        return self.snapshot(packet.index_name, observed_at)

    def snapshot(self, index_name: str, observed_at: datetime) -> dict[str, object]:
        current = self._index_points[-1][1]
        recent = [
            (stamp, value)
            for stamp, value in self._index_points
            if (observed_at - stamp).total_seconds() <= 60
        ]
        changes = [right[1] - left[1] for left, right in zip(recent, recent[1:])]
        noise = statistics.pstdev(changes) if len(changes) >= 2 else 0.0
        slope = self._slope_points_per_second(recent)

        # Without historical CAS sessions, persistence is the only defensible
        # production baseline. A tiny, bounded 10-second trend projection is
        # exposed separately so it can be evaluated without masquerading as a
        # trained model.
        momentum_adjustment = max(-5.0, min(5.0, slope * 10.0))
        momentum_nowcast = current + momentum_adjustment
        half_width = max(1.0, min(25.0, 2.5 * noise * math.sqrt(10.0)))
        pressure = self._cross_sectional_pressure()
        return {
            "observed_at": observed_at.isoformat(),
            "index_name": index_name,
            "indicative_close": round(current, 4),
            "predicted_close": round(current, 4),
            "method": "indicative_persistence_untrained",
            "experimental_momentum_close": round(momentum_nowcast, 4),
            "momentum_adjustment": round(momentum_adjustment, 4),
            "indicative_interval_low": round(current - half_width, 4),
            "indicative_interval_high": round(current + half_width, 4),
            "recent_noise_points": round(noise, 4),
            "constituent_pressure": round(pressure, 6) if pressure is not None else None,
            "constituents_seen": len(self._instruments),
            "samples_seen": len(self._index_points),
            "status": "LIVE_UNTRAINED_BASELINE",
        }

    @staticmethod
    def _slope_points_per_second(points: list[tuple[datetime, float]]) -> float:
        if len(points) < 3:
            return 0.0
        origin = points[0][0]
        xs = [(stamp - origin).total_seconds() for stamp, _ in points]
        ys = [value for _, value in points]
        x_mean = statistics.fmean(xs)
        y_mean = statistics.fmean(ys)
        denominator = sum((value - x_mean) ** 2 for value in xs)
        if denominator == 0:
            return 0.0
        return sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, ys)) / denominator

    def _cross_sectional_pressure(self) -> float | None:
        pressures = []
        for packet in self._instruments.values():
            total = packet.total_buy_quantity + packet.total_sell_quantity
            if total > 0:
                pressures.append(
                    (packet.total_buy_quantity - packet.total_sell_quantity) / total
                )
        return statistics.median(pressures) if pressures else None


class JsonlCasRecorder:
    """Append normalized CAS events and atomically publish the latest nowcast."""

    def __init__(self, output_dir: Path, session_date: str) -> None:
        output_dir.mkdir(parents=True, exist_ok=True)
        self.events_path = output_dir / f"{session_date}.jsonl"
        self.latest_path = output_dir / "latest.json"
        self._lock = threading.Lock()

    def record_packet(
        self,
        packet: CasInstrumentPacket | CasIndexPacket,
        observed_at: datetime,
        raw_data: bytes,
    ) -> None:
        event = {
            "type": "index" if isinstance(packet, CasIndexPacket) else "instrument",
            "observed_at": observed_at.isoformat(),
            "packet": asdict(packet),
            "raw_hex": raw_data.hex(),
        }
        self._append(event)

    def publish_nowcast(self, nowcast: dict[str, object]) -> None:
        self._append({"type": "nowcast", **nowcast})
        temporary = self.latest_path.with_suffix(".json.new")
        temporary.write_text(json.dumps(nowcast, indent=2) + "\n", encoding="utf-8")
        temporary.replace(self.latest_path)

    def record_error(self, message: str, observed_at: datetime) -> None:
        self._append({
            "type": "error",
            "observed_at": observed_at.isoformat(),
            "message": message,
        })

    def _append(self, event: dict[str, object]) -> None:
        line = json.dumps(event, separators=(",", ":"), default=str)
        with self._lock:
            with self.events_path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")


def load_current_nifty_symbols() -> tuple[list[str], str]:
    """Load current Nifty 50 symbols, with explicit configured and static fallbacks."""
    configured = [
        symbol.strip().upper()
        for symbol in os.getenv("NIFTY_CAS_SYMBOLS", "").split(",")
        if symbol.strip()
    ]
    if configured:
        return configured, "environment"
    try:
        response = requests.get(
            NIFTY_50_CSV_URL,
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=20,
        )
        response.raise_for_status()
        rows = csv.DictReader(io.StringIO(response.text))
        symbols = [str(row.get("Symbol", "")).strip().upper() for row in rows]
        symbols = [symbol for symbol in symbols if symbol]
        if len(symbols) < 40:
            raise ValueError(f"NSE constituent file contained only {len(symbols)} symbols")
        return symbols, "nse"
    except (requests.RequestException, ValueError):
        return list(DEFAULT_NIFTY_SYMBOLS), "static_fallback"


def resolve_nifty_security_ids(refresh_master: bool = False) -> tuple[list[str], list[str], str]:
    """Resolve point-in-time Nifty symbols against Dhan's NSE equity master."""
    symbols, source = load_current_nifty_symbols()
    security_by_symbol = equity_ids(load_master(refresh=refresh_master))
    resolved = [security_by_symbol[symbol] for symbol in symbols if security_by_symbol.get(symbol)]
    missing = [symbol for symbol in symbols if not security_by_symbol.get(symbol)]
    if not resolved:
        raise RuntimeError("No Nifty constituents resolved in the Dhan instrument master")
    return resolved, missing, source


class DhanCasClient:
    """Run the Dhan CAS WebSocket and deliver decoded binary packets."""

    def __init__(
        self,
        access_token: str,
        client_id: str,
        security_ids: list[str],
        on_packet: Callable[[CasInstrumentPacket | CasIndexPacket, datetime, bytes], None],
        on_error: Callable[[str, datetime], None],
        should_continue: Callable[[], bool] | None = None,
    ) -> None:
        if not access_token or not client_id:
            raise ValueError("Dhan access token and client ID are required")
        if not security_ids:
            raise ValueError("At least one NSE equity security ID is required")
        query = urlencode({
            "token": access_token,
            "clientId": client_id,
            "authType": 2,
        })
        self.url = f"wss://api-feed.dhan.co?{query}"
        self.security_ids = security_ids
        self.on_packet = on_packet
        self.on_error = on_error
        self.should_continue = should_continue or (lambda: True)
        self._opened = threading.Event()
        self._app: websocket.WebSocketApp | None = None
        self.last_error: object | None = None
        self.received_packet = False

    def run(self) -> None:
        self._opened.clear()
        self.last_error = None
        self.received_packet = False
        self._app = websocket.WebSocketApp(
            self.url,
            on_open=self._on_open,
            on_data=self._on_data,
            on_error=self._on_error,
            on_close=self._on_close,
            on_ping=self._on_ping,
        )
        self._app.run_forever(ping_timeout=40)
        if not self._opened.is_set():
            raise RuntimeError("Dhan CAS WebSocket closed before subscription was established")

    def close(self) -> None:
        if self._app and self._app.sock and self._app.sock.connected:
            self._app.send(json.dumps({"RequestCode": CAS_DISCONNECT_CODE}))
            self._app.close()

    def _on_open(self, app: websocket.WebSocketApp) -> None:
        for start in range(0, len(self.security_ids), 100):
            batch = self.security_ids[start:start + 100]
            app.send(json.dumps({
                "RequestCode": CAS_SUBSCRIBE_CODE,
                "InstrumentCount": len(batch),
                "InstrumentList": [
                    {"ExchangeSegment": "NSE_EQ", "SecurityId": security_id}
                    for security_id in batch
                ],
            }))
        self._opened.set()

    def _on_data(
        self,
        _app: websocket.WebSocketApp,
        data: bytes | str,
        data_type: int,
        _continue_flag: bool,
    ) -> None:
        observed_at = timezone.localtime()
        if data_type != websocket.ABNF.OPCODE_BINARY or not isinstance(data, bytes):
            self.on_error(f"Ignored non-binary CAS message of type {data_type}", observed_at)
            return
        try:
            packet = decode_cas_packet(data)
        except ValueError as error:
            self.on_error(str(error), observed_at)
            return
        self.received_packet = True
        self.on_packet(packet, observed_at, data)

    def _on_ping(self, app: websocket.WebSocketApp, _message: bytes) -> None:
        if not self.should_continue():
            app.close()

    def _on_error(self, _app: websocket.WebSocketApp, error: object) -> None:
        self.last_error = error
        self.on_error(f"WebSocket error: {error}", timezone.localtime())

    def _on_close(
        self,
        _app: websocket.WebSocketApp,
        status_code: int | None,
        message: str | None,
    ) -> None:
        if status_code is not None:
            self.on_error(
                f"WebSocket closed with status {status_code}: {message or ''}".strip(),
                timezone.localtime(),
            )
