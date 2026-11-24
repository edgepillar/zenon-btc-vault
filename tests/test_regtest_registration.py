import unittest
from decimal import Decimal
from unittest.mock import Mock, call

from mock_release import Withdrawal
from regtest import BitcoinRPC, RegtestError, register_release


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
            "txinwitness": ["3001", "01", "51"],
        }
        self.payout = {
            "value": Decimal("0.00100000"),
            "scriptPubKey": {"hex": self.withdrawal.recipient_script_pubkey},
        }
        self.decoded = {
            "vin": [self.tx_input],
            "vout": [self.payout],
            "locktime": 0,
        }
        self.rpc = Mock(spec=BitcoinRPC)
        self.rpc.call.side_effect = [self.decoded, [{"allowed": True}]]

    def assert_rejected_before_mempool_check(self, reason):
        with self.assertRaisesRegex(RegtestError, reason):
            register_release(self.rpc, self.withdrawal, self.raw)
        self.rpc.call.assert_called_once_with("decoderawtransaction", self.raw)

    def test_registers_exact_bytes_after_bitcoin_validation(self):
        registered = register_release(self.rpc, self.withdrawal, self.raw)
        self.assertEqual(registered.release_signed_transaction(self.withdrawal), self.raw)
        self.assertEqual(
            self.rpc.call.call_args_list,
            [call("decoderawtransaction", self.raw), call("testmempoolaccept", [self.raw])],
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

    def test_rejects_a_spend_bitcoin_core_will_not_accept(self):
        self.rpc.call.side_effect = [self.decoded, [{"allowed": False}]]
        with self.assertRaisesRegex(RegtestError, "Bitcoin Core rejected"):
            register_release(self.rpc, self.withdrawal, self.raw)
