"""Match a synthetic withdrawal against a trusted release registration."""

import re
from dataclasses import dataclass, field


MAX_BITCOIN_SATS = 21_000_000 * 100_000_000
MAX_OUTPUT_INDEX = (1 << 32) - 1


class ReleaseRefused(ValueError):
    """The mock release inputs did not satisfy the release policy."""


def _is_lowercase_hex(value: object) -> bool:
    return (
        type(value) is str
        and bool(value)
        and len(value) % 2 == 0
        and re.search(r"[^0-9a-f]", value) is None
    )


@dataclass(frozen=True)
class Withdrawal:
    """The synthetic fields that must agree for a mock release."""

    source_network: str
    domain_id: str
    outbox_id: str
    txid: str
    vout: int
    amount_sats: int
    recipient_script_pubkey: str

    def __post_init__(self) -> None:
        for name in ("source_network", "domain_id", "outbox_id"):
            value = getattr(self, name)
            if type(value) is not str or not value or value != value.strip():
                raise ReleaseRefused(
                    f"{name} must be a nonempty string without surrounding whitespace"
                )

        if not _is_lowercase_hex(self.txid) or len(self.txid) != 64:
            raise ReleaseRefused("txid must be 32 bytes of lowercase hexadecimal")
        if type(self.vout) is not int or not 0 <= self.vout <= MAX_OUTPUT_INDEX:
            raise ReleaseRefused("vout must be an unsigned 32-bit integer")
        if type(self.amount_sats) is not int or not 1 <= self.amount_sats <= MAX_BITCOIN_SATS:
            raise ReleaseRefused("amount_sats must be a positive integer within the Bitcoin money range")
        if not _is_lowercase_hex(self.recipient_script_pubkey):
            raise ReleaseRefused("recipient_script_pubkey must be nonempty lowercase hexadecimal bytes")


@dataclass(frozen=True)
class RegisteredRelease:
    """Hold trusted setup data for the mock. Bitcoin validation belongs to setup."""

    withdrawal: Withdrawal
    signed_transaction: str = field(repr=False)

    def __post_init__(self) -> None:
        if type(self.withdrawal) is not Withdrawal:
            raise ReleaseRefused("registration requires a Withdrawal record")
        if type(self.signed_transaction) is not str or not _is_lowercase_hex(self.signed_transaction.lower()):
            raise ReleaseRefused("signed_transaction must be nonempty hexadecimal bytes")

    def release_signed_transaction(self, supplied: Withdrawal) -> str:
        """Return the registered bytes for an exact withdrawal match."""
        if type(supplied) is not Withdrawal:
            raise ReleaseRefused("supplied withdrawal must be a Withdrawal record")
        if supplied != self.withdrawal:
            raise ReleaseRefused("supplied withdrawal does not match the registered withdrawal")
        return self.signed_transaction
