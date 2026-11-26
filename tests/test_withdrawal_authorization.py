from dataclasses import replace
from hashlib import sha3_256
import unittest

from mock_release import RegisteredRelease, ReleaseRefused, Withdrawal
from withdrawal_authorization import (
    REGTEST_GENESIS,
    AuthorizedRelease,
    WithdrawalSource,
    encode_authorization,
    outbox_root,
)


EMPTY_BRANCH = bytes(4)


class WithdrawalAuthorizationTests(unittest.TestCase):
    def setUp(self):
        self.source = WithdrawalSource(69, 7, 9, "1a" * 32)
        self.withdrawal = Withdrawal(
            source_network="zenon:69",
            domain_id="7",
            outbox_id="2b" * 32,
            txid=bytes(range(32)).hex(),
            vout=3,
            amount_sats=123_456,
            recipient_script_pubkey="0014" + "ef" * 20,
        )
        self.registration = RegisteredRelease(self.withdrawal, "01aB")
        self.message = encode_authorization(self.withdrawal, self.source, REGTEST_GENESIS)

    def open_release(self, gate, message, proof=EMPTY_BRANCH, observed_genesis=REGTEST_GENESIS):
        return gate.release_signed_transaction(message, proof, observed_bitcoin_genesis=observed_genesis)

    def gate_for(self, message):
        root = sha3_256(b"\x00" + message).digest()
        return AuthorizedRelease(self.registration, self.source, REGTEST_GENESIS, root)

    def test_has_a_stable_version_one_encoding(self):
        expected = bytes.fromhex(
            "7a656e6f6e2d6274632d7661756c742f7769746864726177616c0001"
            "0000000000000045"
            "00000007"
            "0000000000000009"
            + "1a" * 32
            + "2b" * 32
            + REGTEST_GENESIS
            + "000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f"
            "00000003"
            "000000000001e240"
            "0016"
            "0014"
            + "ef" * 20
        )
        self.assertEqual(self.message, expected)

    def test_opens_the_exact_registered_bytes_with_a_merkle_branch(self):
        left = sha3_256(b"\x00withdrawal before this one").digest()
        right = sha3_256(b"\x00withdrawal after this one").digest()
        leaf = sha3_256(b"\x00" + self.message).digest()
        intermediate = sha3_256(b"\x01" + left + leaf).digest()
        root = sha3_256(b"\x01" + intermediate + right).digest()
        proof = b"\x00\x00\x00\x02\x00" + left + b"\x01" + right
        gate = AuthorizedRelease(self.registration, self.source, REGTEST_GENESIS, root)
        self.assertEqual(self.open_release(gate, self.message, proof), "01aB")
        with self.assertRaisesRegex(ReleaseRefused, "trusted root"):
            self.open_release(gate, self.message, proof[:-1] + bytes([proof[-1] ^ 1]))

    def test_refuses_authenticated_changes_to_the_bitcoin_target(self):
        variants = {
            "txid": "55" * 32,
            "vout": 4,
            "amount_sats": 123_457,
            "recipient_script_pubkey": "0014" + "fa" * 20,
        }
        for name, value in variants.items():
            with self.subTest(field=name):
                changed = replace(self.withdrawal, **{name: value})
                message = encode_authorization(changed, self.source, REGTEST_GENESIS)
                with self.assertRaisesRegex(ReleaseRefused, "does not authorize"):
                    self.open_release(self.gate_for(message), message)
        message = encode_authorization(self.withdrawal, self.source, "44" * 32)
        with self.assertRaisesRegex(ReleaseRefused, "does not authorize"):
            self.open_release(self.gate_for(message), message)

    def test_refuses_authenticated_changes_to_the_source_context(self):
        alternatives = (
            (replace(self.source, chain_id=70), replace(self.withdrawal, source_network="zenon:70")),
            (replace(self.source, domain_id=8), replace(self.withdrawal, domain_id="8")),
            (replace(self.source, batch_id=10), self.withdrawal),
            (replace(self.source, contract_hash="cd" * 32), self.withdrawal),
            (self.source, replace(self.withdrawal, outbox_id="bc" * 32)),
        )
        for source, withdrawal in alternatives:
            with self.subTest(source=source, outbox_id=withdrawal.outbox_id):
                message = encode_authorization(withdrawal, source, REGTEST_GENESIS)
                with self.assertRaisesRegex(ReleaseRefused, "does not authorize"):
                    self.open_release(self.gate_for(message), message)

    def test_requires_matching_source_network_and_domain_in_setup(self):
        for changed in (replace(self.source, chain_id=70), replace(self.source, domain_id=8)):
            with self.subTest(source=changed), self.assertRaisesRegex(ReleaseRefused, "network and domain"):
                encode_authorization(self.withdrawal, changed, REGTEST_GENESIS)

    def test_refuses_a_different_bitcoin_node_network(self):
        with self.assertRaisesRegex(ReleaseRefused, "different network"):
            self.open_release(self.gate_for(self.message), self.message, observed_genesis="99" * 32)

    def test_requires_a_trusted_root_and_a_regtest_registration(self):
        with self.assertRaisesRegex(ReleaseRefused, "trusted 32-byte"):
            AuthorizedRelease(self.registration, self.source, REGTEST_GENESIS, b"")
        with self.assertRaisesRegex(ReleaseRefused, "requires Bitcoin regtest"):
            AuthorizedRelease(self.registration, self.source, "99" * 32, bytes(32))

    def test_refuses_malformed_merkle_proofs(self):
        cases = (
            (b"\x00", "step count"),
            (EMPTY_BRANCH + b"\x00", "length or depth"),
            ((33).to_bytes(4, "big") + bytes(33 * 33), "length or depth"),
            (b"\x00\x00\x00\x01\x02" + bytes(32), "sibling side"),
        )
        for proof, reason in cases:
            with self.subTest(reason=reason), self.assertRaisesRegex(ReleaseRefused, reason):
                self.open_release(self.gate_for(self.message), self.message, proof)

    def test_valid_devnet_znn_withdrawal_does_not_authorize_bitcoin(self):
        # Domain 1, batch 5, Momentum 115 in the source-pinned devnet capture.
        # The published replay verified the Settlement state record from which this root came.
        message = bytes.fromhex(
            "4dbe92ecf9f3a3a71d87f561a9f11cfe6b6ba0467d8103d004af0d258150a0e4"
            "000000000000000500000000010031ddd498e3d7d3c631b9a271d24a144642ca49"
            "0000000000000000000000000000000000000000000014e66318c6318c6318c6"
            "0000000000000000000000000000000000000000000000000000000002625a00"
            "00000000"
        )
        root = bytes.fromhex("1d00e2ae0658cc182510399132a97b7e8a0dc55aa23721d91346f8b863dc1328")
        source = WithdrawalSource(69, 1, 5, message[:32].hex())
        claim = replace(
            self.withdrawal, domain_id="1",
            outbox_id="1636921647869704a88d495c22f8dcae4b338690075a06de203452c9400a96d1",
        )
        gate = AuthorizedRelease(RegisteredRelease(claim, "01aB"), source, REGTEST_GENESIS, root)
        self.assertEqual(outbox_root(message, EMPTY_BRANCH), root)
        with self.assertRaisesRegex(ReleaseRefused, "does not authorize the registered Bitcoin withdrawal"):
            self.open_release(gate, message)
