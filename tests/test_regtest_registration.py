from dataclasses import replace
from decimal import Decimal
import hashlib
import unittest
from unittest.mock import Mock, call

from mock_release import Withdrawal
from regtest import (
    BitcoinRPC,
    REFUND_SEQUENCE,
    RegtestError,
    VAULT_SATS,
    register_release,
)


class RegisterReleaseTests(unittest.TestCase):
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
        self.raw = "01ab"
        self.tx_input = {
            "txid": self.withdrawal.txid,
            "vout": self.withdrawal.vout,
            "sequence": REFUND_SEQUENCE,
            "txinwitness": ["3001", "01", "51"],
        }
        self.payout = {
            "value": Decimal("0.00100000"),
            "scriptPubKey": {"hex": self.withdrawal.recipient_script_pubkey},
        }
        self.decoded = {
            "txid": "34" * 32,
            "vin": [self.tx_input],
            "vout": [self.payout],
            "locktime": 0,
        }
        self.vault_script = "0020" + hashlib.sha256(bytes.fromhex("51")).hexdigest()
        self.funding = {
            "confirmations": 1,
            "scriptPubKey": {"hex": self.vault_script},
            "value": Decimal("0.01000000"),
        }
        self.fee_child = "02ab"
        self.decoded_child = {
            "vin": [{"txid": self.decoded["txid"], "vout": 0}],
            "vout": [{"value": Decimal("0.00999000")}],
        }
        self.rpc = Mock(spec=BitcoinRPC)
        self.rpc.call.side_effect = [self.decoded, self.funding, [{"allowed": True}]]

    def register(self, *, fee_child=None):
        return register_release(
            self.rpc, self.withdrawal, self.raw, vault_script=self.vault_script, fee_child=fee_child,
        )

    def assert_rejected_before_mempool_check(self, reason):
        with self.assertRaisesRegex(RegtestError, reason):
            self.register()
        self.rpc.call.assert_called_once_with("decoderawtransaction", self.raw)

    def test_registers_exact_bytes_after_bitcoin_validation(self):
        registered = self.register()
        self.assertEqual(registered.release_signed_transaction(self.withdrawal), self.raw)
        self.assertEqual(
            self.rpc.call.call_args_list,
            [
                call("decoderawtransaction", self.raw),
                call("gettxout", self.withdrawal.txid, self.withdrawal.vout, False),
                call("testmempoolaccept", [self.raw]),
            ],
        )

    def test_rejects_wrong_outpoint(self):
        self.tx_input["vout"] = 1
        self.assert_rejected_before_mempool_check("Release outpoint")

    def test_rejects_wrong_amount(self):
        self.payout["value"] -= Decimal("0.00000001")
        self.assert_rejected_before_mempool_check("Release amount")

    def test_rejects_wrong_recipient(self):
        self.payout["scriptPubKey"]["hex"] = "52"
        self.assert_rejected_before_mempool_check("Release recipient")

    def test_rejects_additional_output(self):
        self.decoded["vout"].append(self.payout)
        self.assert_rejected_before_mempool_check("one input and one output")

    def test_rejects_refund_branch(self):
        self.tx_input["txinwitness"][1] = ""
        self.assert_rejected_before_mempool_check("expected vault branch")

    def test_rejects_wrong_sighash(self):
        self.tx_input["txinwitness"][0] = "3002"
        self.assert_rejected_before_mempool_check("SIGHASH_ALL")

    def test_rejects_delayed_release(self):
        self.decoded["locktime"] = 2
        self.assert_rejected_before_mempool_check("immediately spendable")

    def test_rejects_wrong_sequence(self):
        self.tx_input["sequence"] = 1
        self.assert_rejected_before_mempool_check("input sequence")

    def test_rejects_wrong_witness_script(self):
        self.tx_input["txinwitness"][2] = "52"
        self.assert_rejected_before_mempool_check("witness does not match")

    def test_rejects_unconfirmed_or_missing_funding(self):
        self.funding["confirmations"] = 0
        for funding in (None, self.funding):
            with self.subTest(funding=funding):
                self.rpc.call.side_effect = [self.decoded, funding]
                with self.assertRaisesRegex(RegtestError, "confirmed funding output"):
                    self.register()

    def test_rejects_wrong_funding_script(self):
        self.funding["scriptPubKey"]["hex"] = "0020" + "00" * 32
        with self.assertRaisesRegex(RegtestError, "Funding output does not match"):
            self.register()

    def test_rejects_wrong_funding_amount(self):
        self.funding["value"] += Decimal("0.00000001")
        with self.assertRaisesRegex(RegtestError, "confirmed funding amount"):
            self.register()

    def test_rejects_a_spend_bitcoin_core_will_not_accept(self):
        self.rpc.call.side_effect = [self.decoded, self.funding, [{"allowed": False}]]
        with self.assertRaisesRegex(RegtestError, "Bitcoin Core rejected"):
            self.register()

    def configure_package(self, candidate_block):
        self.withdrawal = replace(self.withdrawal, amount_sats=VAULT_SATS)
        self.payout["value"] = self.funding["value"]
        self.rpc.call.side_effect = [self.decoded, self.funding, self.decoded_child, candidate_block]

    def test_registers_only_the_zero_fee_parent_after_block_validation(self):
        self.configure_package({"hex": "01"})
        registered = self.register(fee_child=self.fee_child)
        self.assertEqual(registered.release_signed_transaction(self.withdrawal), self.raw)
        self.rpc.call.assert_called_with("generateblock", "raw(51)", [self.raw, self.fee_child], False)

    def test_rejects_package_with_unrelated_child(self):
        self.configure_package({})
        self.decoded_child["vin"][0]["txid"] = "35" * 32
        with self.assertRaisesRegex(RegtestError, "must spend the registered release payout"):
            self.register(fee_child=self.fee_child)
        self.rpc.call.assert_called_with("decoderawtransaction", self.fee_child)

    def test_rejects_package_for_a_child_without_a_fee(self):
        self.configure_package({})
        self.decoded_child["vout"][0]["value"] = self.funding["value"]
        with self.assertRaisesRegex(RegtestError, "must pay a positive fee"):
            self.register(fee_child=self.fee_child)

    def test_requires_an_unsubmitted_candidate_block(self):
        self.configure_package({})
        with self.assertRaisesRegex(RegtestError, "unsubmitted candidate block"):
            self.register(fee_child=self.fee_child)

    def test_rejects_package_for_parent_with_a_fee(self):
        with self.assertRaisesRegex(RegtestError, "zero-fee parent"):
            self.register(fee_child=self.fee_child)
