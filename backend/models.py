import json
from typing import Optional
from pydantic import BaseModel, ConfigDict


class Spot(BaseModel):
    model_config = ConfigDict(extra="ignore")

    sq: int           # sequence number (global unique)
    f: int            # frequency Hz
    md: str           # mode
    rp: int           # SNR dB
    t: int            # Unix timestamp
    sc: str           # sender (TX) callsign
    sl: Optional[str] = None  # sender locator (Maidenhead)
    rc: str           # receiver (RX) callsign
    rl: Optional[str] = None  # receiver locator
    sa: Optional[int] = None  # sender ADIF DXCC code
    ra: Optional[int] = None  # receiver ADIF DXCC code
    b: str            # band

    @classmethod
    def from_payload(cls, payload: bytes) -> Optional["Spot"]:
        try:
            return cls(**json.loads(payload))
        except Exception:
            return None

    def to_wire(self) -> dict:
        return {
            "seq": self.sq,
            "freq": self.f,
            "mode": self.md,
            "snr": self.rp,
            "ts": self.t,
            "tx_call": self.sc,
            "tx_grid": self.sl,
            "rx_call": self.rc,
            "rx_grid": self.rl,
            "tx_dxcc": self.sa,
            "rx_dxcc": self.ra,
            "band": self.b,
        }
