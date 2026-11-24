import unittest
from dataclasses import FrozenInstanceError, replace

from mock_release import RegisteredRelease, ReleaseRefused, Withdrawal


class MockReleaseTests(unittest.TestCase):
    def setUp(self):
        self.withdrawal = Withdrawal(
            source_network="synthetic",
            domain_id="vault-demo",
            outbox_id="withdrawal-1",
            txid="12" * 32,
            vout=0,
            amount_sats=100_000,
            recipient_script_pubkey="51",
        )

    def test_accepts_equal_records_and_preserves_hex(self):
        registered = RegisteredRelease(self.withdrawal, "01aB")
        self.assertEqual(
            registered.release_signed_transaction(replace(self.withdrawal)),
            "01aB",
        )

    def test_withdrawal_is_immutable(self):
        with self.assertRaises(FrozenInstanceError):
            self.withdrawal.vout = 1

    def test_accepts_amount_and_vout_boundaries(self):
        for vout, amount in ((0, 1), (2**32 - 1, 2_100_000_000_000_000)):
            with self.subTest(vout=vout, amount=amount):
                claim = replace(self.withdrawal, vout=vout, amount_sats=amount)
                self.assertEqual(RegisteredRelease(claim, "00").release_signed_transaction(claim), "00")

    def test_refuses_a_mismatch_in_each_field(self):
        changes = {
            "source_network": "another-network",
            "domain_id": "another-domain",
            "outbox_id": "withdrawal-2",
            "txid": "34" * 32,
            "vout": 1,
            "amount_sats": 100_001,
            "recipient_script_pubkey": "52",
        }
        registered = RegisteredRelease(self.withdrawal, "01ab")
        for field, value in changes.items():
            with self.subTest(field=field), self.assertRaises(ReleaseRefused):
                supplied = replace(self.withdrawal, **{field: value})
                registered.release_signed_transaction(supplied)

    def test_refuses_noncanonical_withdrawal_fields(self):
        invalid_fields = {
            "source_network": ["", " demo", None],
            "domain_id": [" ", 3],
            "outbox_id": [False, "demo\n"],
            "txid": ["ab" * 31, "AB" * 32, "gg" * 32],
            "vout": [True, -1, 2**32, 1.0, "0"],
            "amount_sats": [True, 0, -1, 2_100_000_000_000_001, 1.0],
            "recipient_script_pubkey": ["", "0", "0A", "0g", "00 01"],
        }
        for field, values in invalid_fields.items():
            for value in values:
                with self.subTest(field=field, value=value), self.assertRaises(ReleaseRefused):
                    replace(self.withdrawal, **{field: value})

    def test_refuses_invalid_signed_transaction_hex(self):
        for value in ("", " ", "1", "0x01", "01gg", "01 02", "01\n", b"01", None):
            with self.subTest(value=value), self.assertRaises(ReleaseRefused):
                RegisteredRelease(self.withdrawal, value)

    def test_refuses_other_record_types(self):
        with self.assertRaises(ReleaseRefused):
            RegisteredRelease(vars(self.withdrawal), "01ab")
        registered = RegisteredRelease(self.withdrawal, "01ab")
        with self.assertRaises(ReleaseRefused):
            registered.release_signed_transaction(vars(self.withdrawal))

    def test_registered_transaction_is_immutable(self):
        registered = RegisteredRelease(self.withdrawal, "01ab")
        with self.assertRaises(FrozenInstanceError):
            registered.signed_transaction = "00"
        self.assertEqual(registered.release_signed_transaction(self.withdrawal), "01ab")
        self.assertNotIn("01ab", repr(registered))
