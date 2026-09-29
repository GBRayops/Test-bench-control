from __future__ import annotations

import struct
from dataclasses import dataclass


SERVO_COB_ID = 0x300
CONTROL_COB_ID = 0x200
SDO_REQUEST_COB_ID = 0x600
SDO_RESPONSE_COB_ID = 0x580
SERVO_COMMAND = 0x31
STOP_COMMAND = 0x04
OFF_COMMAND = 0x06
RESET_COMMAND = 0x01


@dataclass(frozen=True)
class CanFrame:
    can_id: int
    payload: bytes


@dataclass(frozen=True)
class TelemetryUpdate:
    node_id: int
    channel: int
    values: dict[str, int | float]


@dataclass(frozen=True)
class SdoResponse:
    node_id: int
    index: int
    subindex: int
    command: int
    data: bytes
    abort_code: int | None = None


def make_servo_payload(position_counts: int) -> bytes:
    """Encode la commande Servo SC-series (0x31, position INT32 à l'octet 4)."""
    if not -(2**31) <= position_counts < 2**31:
        raise ValueError("La position dépasse la plage INT32")
    return bytes((SERVO_COMMAND, 0, 0, 0)) + struct.pack("<i", position_counts)


def make_simple_payload(command: int) -> bytes:
    if not 0 <= command <= 0xFF:
        raise ValueError("Code de commande invalide")
    return bytes((command, 0, 0, 0, 0, 0, 0, 0))


def encode_slcan(can_id: int, payload: bytes) -> bytes:
    """Encode une trame CAN 11 bits dans le format texte SLCAN."""
    if not 0 <= can_id <= 0x7FF:
        raise ValueError("CAN ID hors plage 11 bits")
    if len(payload) > 8:
        raise ValueError("Une trame CAN classique contient au plus 8 octets")
    text = f"t{can_id:03X}{len(payload):X}{payload.hex().upper()}\r"
    return text.encode("ascii")


def decode_slcan_line(line: bytes | str) -> CanFrame | None:
    """Décode une trame SLCAN CAN 11 bits; ignore les réponses de contrôle."""
    text = line.decode("ascii", errors="ignore") if isinstance(line, bytes) else line
    text = text.strip()
    if len(text) < 5 or text[0] != "t":
        return None
    try:
        can_id = int(text[1:4], 16)
        length = int(text[4], 16)
        data = text[5:5 + length * 2]
        if len(data) != length * 2 or len(text) != 5 + length * 2:
            return None
        return CanFrame(can_id, bytes.fromhex(data))
    except ValueError:
        return None


def _float16_servosila(data: bytes) -> float:
    """Format FLOAT16 Servosila: INT16 signé, pleine échelle 128."""
    return struct.unpack("<h", data)[0] * 128.0 / 32767.0


def decode_sc60r_tpdo(frame: CanFrame) -> TelemetryUpdate | None:
    """Décode les mappings TPDO par défaut 0x180/0x280/0x380 + Node ID."""
    for base, channel in ((0x180, 1), (0x280, 2), (0x380, 3)):
        node = frame.can_id - base
        if 1 <= node <= 126 and len(frame.payload) == 8:
            p = frame.payload
            if channel == 1:
                values = {
                    "fault_bits": struct.unpack("<H", p[0:2])[0],
                    "bus_voltage_v": _float16_servosila(p[2:4]),
                    "electrical_speed_hz": struct.unpack("<f", p[4:8])[0],
                }
            elif channel == 2:
                values = {
                    "operation_mode": struct.unpack("<H", p[0:2])[0],
                    "commutation_mode": struct.unpack("<H", p[2:4])[0],
                    "work_zone_count": struct.unpack("<i", p[4:8])[0],
                }
            else:
                values = {
                    "phase_a_current_a": struct.unpack("<f", p[0:4])[0],
                    "phase_b_current_a": struct.unpack("<f", p[4:8])[0],
                }
            return TelemetryUpdate(node, channel, values)
    return None


def _validate_object_address(index: int, subindex: int) -> None:
    if not 0 <= index <= 0xFFFF:
        raise ValueError("Index SDO hors plage")
    if not 0 <= subindex <= 0xFF:
        raise ValueError("Sous-index SDO hors plage")


def make_sdo_upload_payload(index: int, subindex: int) -> bytes:
    _validate_object_address(index, subindex)
    return bytes((0x40,)) + struct.pack("<H", index) + bytes((subindex, 0, 0, 0, 0))


def make_sdo_download_payload(index: int, subindex: int, data: bytes) -> bytes:
    _validate_object_address(index, subindex)
    if not 1 <= len(data) <= 4:
        raise ValueError("Une ecriture SDO expediee contient de 1 a 4 octets")
    command = {1: 0x2F, 2: 0x2B, 3: 0x27, 4: 0x23}[len(data)]
    return (
        bytes((command,))
        + struct.pack("<H", index)
        + bytes((subindex,))
        + data.ljust(4, b"\x00")
    )


def decode_sdo_response(frame: CanFrame) -> SdoResponse | None:
    node = frame.can_id - SDO_RESPONSE_COB_ID
    if not 1 <= node <= 126 or len(frame.payload) != 8:
        return None
    command = frame.payload[0]
    index = struct.unpack("<H", frame.payload[1:3])[0]
    subindex = frame.payload[3]
    if command == 0x80:
        return SdoResponse(
            node, index, subindex, command, b"",
            struct.unpack("<I", frame.payload[4:8])[0],
        )
    if command == 0x60:
        return SdoResponse(node, index, subindex, command, b"")
    length = {0x4F: 1, 0x4B: 2, 0x47: 3, 0x43: 4}.get(command)
    if length is None:
        return None
    return SdoResponse(node, index, subindex, command, frame.payload[4:4 + length])


def sdo_upload_frame(node_id: int, index: int, subindex: int) -> bytes:
    validate_node_id(node_id)
    return encode_slcan(
        SDO_REQUEST_COB_ID + node_id,
        make_sdo_upload_payload(index, subindex),
    )


def sdo_download_frame(
    node_id: int, index: int, subindex: int, data: bytes
) -> bytes:
    validate_node_id(node_id)
    return encode_slcan(
        SDO_REQUEST_COB_ID + node_id,
        make_sdo_download_payload(index, subindex, data),
    )


def servo_frame(node_id: int, position_counts: int) -> bytes:
    validate_node_id(node_id)
    return encode_slcan(SERVO_COB_ID + node_id, make_servo_payload(position_counts))


def control_frame(node_id: int, command: int) -> bytes:
    validate_node_id(node_id)
    return encode_slcan(CONTROL_COB_ID + node_id, make_simple_payload(command))


def validate_node_id(node_id: int) -> None:
    if not 1 <= node_id <= 126:
        raise ValueError("Le Node ID doit être compris entre 1 et 126")
