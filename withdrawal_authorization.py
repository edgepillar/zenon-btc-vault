"""Bind an authenticated outbox message to a registered Bitcoin release."""

from dataclasses import dataclass, field
from hashlib import sha3_256
from struct import pack

from mock_release import RegisteredRelease, ReleaseRefused, Withdrawal, _is_lowercase_hex


WITHDRAWAL_TAG = b"zenon-btc-vault/withdrawal\x00\x01"
REGTEST_GENESIS = "0f9188f13cb7b2c71f2a335e3a4fc328bf5beb436012afca590b1a11466e2206"
MAX_SCRIPT_BYTES = (1 << 16) - 1
MAX_OUTBOX_MESSAGE_BYTES = len(WITHDRAWAL_TAG) + 20 + 4 * 32 + 14 + MAX_SCRIPT_BYTES
MAX_MERKLE_STEPS = 32


def _hash_bytes(value, field_name):
    if not _is_lowercase_hex(value) or len(value) != 64:
        raise ReleaseRefused(f"{field_name} must be a 32-byte lowercase hexadecimal hash")
    return bytes.fromhex(value)


def encode_bitcoin_target(withdrawal: Withdrawal, bitcoin_genesis: str) -> bytes:
    """Encode the Bitcoin fields shared by the two experimental proof paths."""
    if type(withdrawal) is not Withdrawal:
        raise ReleaseRefused("A Bitcoin target requires a withdrawal")
    if len(withdrawal.recipient_script_pubkey) > 2 * MAX_SCRIPT_BYTES:
        raise ReleaseRefused("Recipient script exceeds the authorization length limit")
    script = bytes.fromhex(withdrawal.recipient_script_pubkey)
    return b"".join((
        _hash_bytes(bitcoin_genesis, "Bitcoin genesis"),
        bytes.fromhex(withdrawal.txid),
        pack(">IQH", withdrawal.vout, withdrawal.amount_sats, len(script)),
        script,
    ))


@dataclass(frozen=True)
class WithdrawalSource:
    """Identify the source batch and the authorized withdrawal contract."""

    chain_id: int
    domain_id: int
    batch_id: int
    contract_hash: str

    def __post_init__(self):
        for name, limit in (("chain_id", 1 << 64), ("domain_id", 1 << 32), ("batch_id", 1 << 64)):
            value = getattr(self, name)
            if type(value) is not int or not 0 <= value < limit:
                raise ReleaseRefused(f"{name} is out of range")
        _hash_bytes(self.contract_hash, "source contract")


def encode_authorization(withdrawal: Withdrawal, source: WithdrawalSource, bitcoin_genesis: str) -> bytes:
    """Encode the version-one fixed-recipient authorization message."""
    if type(withdrawal) is not Withdrawal or type(source) is not WithdrawalSource:
        raise ReleaseRefused("An authorization requires a withdrawal and a source batch")
    if withdrawal.source_network != f"zenon:{source.chain_id}" or withdrawal.domain_id != str(source.domain_id):
        raise ReleaseRefused("Withdrawal network and domain must match the source batch")
    return b"".join((
        WITHDRAWAL_TAG,
        pack(">QIQ", source.chain_id, source.domain_id, source.batch_id),
        bytes.fromhex(source.contract_hash),
        _hash_bytes(withdrawal.outbox_id, "outbox ID"),
        encode_bitcoin_target(withdrawal, bitcoin_genesis),
    ))


def outbox_root(message: bytes, proof: bytes) -> bytes:
    """Recompute a Zeno binary outbox root from an encoded message and branch."""
    if type(message) is not bytes or not 0 < len(message) <= MAX_OUTBOX_MESSAGE_BYTES:
        raise ReleaseRefused("Outbox message length is invalid")
    if type(proof) is not bytes or len(proof) < 4:
        raise ReleaseRefused("Outbox proof is missing its step count")
    count = int.from_bytes(proof[:4], "big")
    if count > MAX_MERKLE_STEPS or len(proof) != 4 + count * 33:
        raise ReleaseRefused("Outbox proof length or depth is invalid")
    root = sha3_256(b"\x00" + message).digest()
    for offset in range(4, len(proof), 33):
        side, sibling = proof[offset], proof[offset + 1:offset + 33]
        if side == 0:
            root = sha3_256(b"\x01" + sibling + root).digest()
        elif side == 1:
            root = sha3_256(b"\x01" + root + sibling).digest()
        else:
            raise ReleaseRefused("Outbox proof has an invalid sibling side")
    return root


@dataclass(frozen=True)
class AuthorizedRelease:
    """Hold a validated registration and a root selected by trusted setup."""

    registration: RegisteredRelease
    source: WithdrawalSource
    bitcoin_genesis: str
    trusted_outbox_root: bytes
    _message: bytes = field(init=False, repr=False)

    def __post_init__(self):
        if type(self.registration) is not RegisteredRelease:
            raise ReleaseRefused("An authorization requires a release registration")
        if self.bitcoin_genesis != REGTEST_GENESIS:
            raise ReleaseRefused("The authorization fixture requires Bitcoin regtest")
        if type(self.trusted_outbox_root) is not bytes or len(self.trusted_outbox_root) != 32:
            raise ReleaseRefused("A trusted 32-byte outbox root is required")
        object.__setattr__(
            self, "_message",
            encode_authorization(self.registration.withdrawal, self.source, self.bitcoin_genesis),
        )

    def release_signed_transaction(self, message: bytes, proof: bytes, *, observed_bitcoin_genesis: str) -> str:
        if observed_bitcoin_genesis != self.bitcoin_genesis:
            raise ReleaseRefused("The Bitcoin node is on a different network")
        if outbox_root(message, proof) != self.trusted_outbox_root:
            raise ReleaseRefused("Outbox proof does not match the trusted root")
        if message != self._message:
            raise ReleaseRefused("Authenticated outbox message does not authorize the registered Bitcoin withdrawal")
        return self.registration.signed_transaction
