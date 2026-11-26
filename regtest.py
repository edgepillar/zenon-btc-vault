"""Exercise a fixed-output mock release and a CLTV refund on fresh regtest."""

import argparse
import base64
from contextlib import contextmanager
from dataclasses import replace
from decimal import Decimal
import hashlib
from http.client import HTTPConnection
import json
from pathlib import Path
import secrets
import socket
import subprocess
import time

from mock_release import RegisteredRelease, ReleaseRefused, Withdrawal


COIN = 100_000_000
VAULT_SATS = 1_000_000
PAYMENT_SATS = VAULT_SATS - 1_000
REFUND_SEQUENCE = 0xFFFFFFFE
RPC_LIMIT = 2 * 1024 * 1024
CURVE_ORDER = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141
BASE58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


class RegtestError(RuntimeError):
    """A failed regtest control."""


def require(condition, message):
    if not condition:
        raise RegtestError(message)


def bitcoin_amount(satoshis):
    return format(Decimal(satoshis) / COIN, ".8f")


class BitcoinRPC:
    def __init__(self, port, cookie):
        self.port = port
        self.cookie = cookie

    def call(self, method, *params, wallet=False):
        auth = base64.b64encode(self.cookie.read_bytes().strip()).decode("ascii")
        body = json.dumps({"jsonrpc": "2.0", "id": "vault", "method": method, "params": params})
        connection = HTTPConnection("127.0.0.1", self.port, timeout=15)
        try:
            connection.request(
                "POST",
                "/wallet/funding" if wallet else "/",
                body,
                {"Authorization": "Basic " + auth, "Content-Type": "application/json"},
            )
            response = connection.getresponse()
            if response.status != 200:
                raise RegtestError(f"{method}: HTTP {response.status}")
            payload = response.read(RPC_LIMIT + 1)
            require(len(payload) <= RPC_LIMIT, f"{method}: RPC result exceeded its byte limit")
            result = json.loads(payload, parse_float=Decimal)
        finally:
            connection.close()
        if result.get("error") is not None:
            # RPC error details can include supplied descriptors or raw transactions.
            raise RegtestError(f"{method}: RPC error {result['error']['code']}")
        return result["result"]


@contextmanager
def start_regtest(bitcoind, datadir):
    require(datadir.is_dir() and not any(datadir.iterdir()), "Use an existing empty data directory")
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as port_probe:
        port_probe.bind(("127.0.0.1", 0))
        port = port_probe.getsockname()[1]

    node = subprocess.Popen(
        [
            str(bitcoind), "-regtest", f"-datadir={datadir}", "-server",
            "-networkactive=0", "-listen=0", "-dnsseed=0", "-fixedseeds=0",
            "-rpcbind=127.0.0.1", "-rpcallowip=127.0.0.1", f"-rpcport={port}",
            "-fallbackfee=0.0002", "-printtoconsole=0",
        ],
        cwd=datadir,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    rpc = BitcoinRPC(port, datadir / "regtest" / ".cookie")
    try:
        deadline = time.monotonic() + 25
        while True:
            require(node.poll() is None, "Bitcoin Core exited during startup; check the local debug log")
            try:
                chain = rpc.call("getblockchaininfo")["chain"]
                break
            except (OSError, RegtestError):
                require(time.monotonic() < deadline, "Bitcoin Core RPC did not become ready")
                time.sleep(0.1)
        require(chain == "regtest", "The harness requires Bitcoin regtest")
        require(rpc.call("getnetworkinfo")["networkactive"] is False, "Peer networking must be off")
        yield rpc
    finally:
        if node.poll() is None:
            node.terminate()
        try:
            node.wait(timeout=15)
        except subprocess.TimeoutExpired:
            node.kill()
            node.wait(timeout=5)


def synthetic_wif():
    """Return a fresh compressed regtest-only signing key for this run."""
    scalar = secrets.randbelow(CURVE_ORDER - 1) + 1
    payload = b"\xef" + scalar.to_bytes(32, "big") + b"\x01"
    checksum = hashlib.sha256(hashlib.sha256(payload).digest()).digest()[:4]
    number = int.from_bytes(payload + checksum, "big")
    encoded = []
    while number:
        number, digit = divmod(number, 58)
        encoded.append(BASE58[digit])
    return "".join(reversed(encoded))


def public_key(rpc, wif):
    descriptor = rpc.call("getdescriptorinfo", f"wpkh({wif})")["descriptor"]
    return descriptor.split("#", 1)[0][len("wpkh("):-1]


def vault_descriptor(release_key, refund_key, refund_height):
    return f"wsh(or_i(pk({release_key}),and_v(v:pk({refund_key}),after({refund_height}))))"


def vault_address(rpc, descriptor):
    checksummed = rpc.call("getdescriptorinfo", descriptor)["descriptor"]
    address, = rpc.call("deriveaddresses", checksummed)
    return address


def fund_vault(rpc, address):
    txid = rpc.call("sendtoaddress", address, bitcoin_amount(VAULT_SATS), wallet=True)
    raw = rpc.call("gettransaction", txid, wallet=True)["hex"]
    decoded = rpc.call("decoderawtransaction", raw)
    outputs = [output for output in decoded["vout"] if output["scriptPubKey"].get("address") == address]
    require(len(outputs) == 1, "Funding must create exactly one output at the vault address")
    output = outputs[0]
    require(output["value"] * COIN == VAULT_SATS, "Unexpected vault funding amount")
    return {"txid": txid, "vout": output["n"]}


def presign(rpc, outpoint, recipient, private_descriptor, locktime, *, payout_sats=PAYMENT_SATS):
    source = {**outpoint, "sequence": REFUND_SEQUENCE}
    payout = bitcoin_amount(payout_sats)
    psbt = rpc.call("createpsbt", [source], [{recipient: payout}], locktime, False)
    checksum = rpc.call("getdescriptorinfo", private_descriptor)["checksum"]
    signed = rpc.call(
        "descriptorprocesspsbt", psbt, [f"{private_descriptor}#{checksum}"], "ALL", False, True,
    )
    require(signed["complete"], "The selected vault branch could not be signed")
    raw = signed["hex"]
    decoded = rpc.call("decoderawtransaction", raw)
    tx_input, = decoded["vin"]
    check_vault_witness(tx_input, "" if locktime else "01")
    return raw


def check_vault_witness(tx_input, selector):
    witness = tx_input.get("txinwitness", [])
    require(
        len(witness) == 3 and witness[1] == selector,
        "The transaction must select the expected vault branch",
    )
    require(witness[0].endswith("01"), "The vault signature must use SIGHASH_ALL")
    return bytes.fromhex(witness[2])


def mempool_result(rpc, raw):
    result, = rpc.call("testmempoolaccept", [raw])
    return result


def check_release_package(rpc, parent, parent_txid, fee_child):
    child = rpc.call("decoderawtransaction", fee_child)
    require(len(child["vin"]) == 1, "The fee child must spend only the release payout")
    child_input = child["vin"][0]
    require(
        (child_input.get("txid"), child_input.get("vout")) == (parent_txid, 0),
        "The fee child must spend the registered release payout",
    )
    require(
        sum(output["value"] for output in child["vout"]) * COIN < VAULT_SATS,
        "The fee child must pay a positive fee",
    )
    # Core 32.1's dry-run mempool RPC checks fees per transaction. An unsubmitted
    # regtest block checks the signatures and UTXOs without applying relay fees.
    block = rpc.call("generateblock", "raw(51)", [parent, fee_child], False)
    require(bool(block.get("hex")), "Bitcoin Core did not return an unsubmitted candidate block")


def register_release(rpc, withdrawal, signed_transaction, *, vault_script, fee_child=None):
    """Check the funded vault and the signed spend, then register the parent."""
    candidate = RegisteredRelease(withdrawal, signed_transaction)
    decoded = rpc.call("decoderawtransaction", candidate.signed_transaction)
    require(
        len(decoded["vin"]) == len(decoded["vout"]) == 1,
        "Release must have one input and one output",
    )
    tx_input = decoded["vin"][0]
    payout = decoded["vout"][0]
    require(
        (tx_input.get("txid"), tx_input.get("vout")) == (withdrawal.txid, withdrawal.vout),
        "Release outpoint does not match the withdrawal",
    )
    require(
        payout["value"] * COIN == withdrawal.amount_sats,
        "Release amount does not match the withdrawal",
    )
    require(
        payout["scriptPubKey"]["hex"] == withdrawal.recipient_script_pubkey,
        "Release recipient does not match the withdrawal",
    )
    require(decoded["locktime"] == 0, "Release must be immediately spendable")
    require(tx_input.get("sequence") == REFUND_SEQUENCE, "Unexpected release input sequence")
    witness_script = check_vault_witness(tx_input, "01")
    require(
        "0020" + hashlib.sha256(witness_script).hexdigest() == vault_script,
        "Release witness does not match the expected vault script",
    )
    funding = rpc.call("gettxout", withdrawal.txid, withdrawal.vout, False)
    require(
        funding is not None and funding["confirmations"] >= 1,
        "Release requires a confirmed funding output",
    )
    require(
        funding["scriptPubKey"]["hex"] == vault_script,
        "Funding output does not match the expected vault script",
    )
    require(funding["value"] * COIN == VAULT_SATS, "Unexpected confirmed funding amount")
    if fee_child is None:
        require(
            mempool_result(rpc, candidate.signed_transaction)["allowed"],
            "Bitcoin Core rejected the registered spend",
        )
    else:
        require(withdrawal.amount_sats == VAULT_SATS, "A package release must have a zero-fee parent")
        check_release_package(rpc, candidate.signed_transaction, decoded["txid"], fee_child)
    return candidate


def mutate_recipient(raw, original_script, replacement_script):
    original = bytes.fromhex(original_script)
    replacement = bytes.fromhex(replacement_script)
    require(len(original) == len(replacement) == 22, "Expected two P2WPKH output scripts")
    marker = bytes([len(original)]) + original
    transaction = bytes.fromhex(raw)
    require(transaction.count(marker) == 1, "Could not identify the single signed output")
    return transaction.replace(marker, bytes([len(replacement)]) + replacement, 1).hex()


def confirm_spend(rpc, raw, source, expected_script, miner):
    txid = rpc.call("sendrawtransaction", raw)
    rpc.call("generatetoaddress", 1, miner)
    spent = rpc.call("gettxout", source["txid"], source["vout"], False)
    received = rpc.call("gettxout", txid, 0, False)
    require(spent is None, "The intended vault output was not consumed")
    require(received is not None, "The payout was not confirmed")
    require(received["scriptPubKey"]["hex"] == expected_script, "Unexpected payout script")
    require(received["value"] * COIN == PAYMENT_SATS, "Unexpected payout amount")


def run_controls(rpc):
    rpc.call("createwallet", "funding")
    miner, recipient, alternate = [
        rpc.call("getnewaddress", "", "bech32", wallet=True) for _ in range(3)
    ]
    recipient_script = rpc.call("validateaddress", recipient)["scriptPubKey"]
    alternate_script = rpc.call("validateaddress", alternate)["scriptPubKey"]
    rpc.call("generatetoaddress", 101, miner)
    refund_height = rpc.call("getblockcount") + 5
    release_wif, refund_wif = synthetic_wif(), synthetic_wif()
    release_pub = public_key(rpc, release_wif)
    refund_pub = public_key(rpc, refund_wif)
    public_descriptor = vault_descriptor(release_pub, refund_pub, refund_height)
    address = vault_address(rpc, public_descriptor)
    vault_script = rpc.call("validateaddress", address)["scriptPubKey"]
    release_outpoint = fund_vault(rpc, address)
    refund_outpoint = fund_vault(rpc, address)

    release_raw = presign(
        rpc, release_outpoint, recipient,
        vault_descriptor(release_wif, refund_pub, refund_height), locktime=0,
    )
    expected = Withdrawal(
        source_network="synthetic-zenon",
        domain_id="test-vault",
        outbox_id="test-release",
        amount_sats=PAYMENT_SATS,
        recipient_script_pubkey=recipient_script,
        **release_outpoint,
    )
    require(
        mempool_result(rpc, release_raw)["allowed"],
        "The unconfirmed-vault spend must be independently admissible",
    )
    try:
        register_release(rpc, expected, release_raw, vault_script=vault_script)
    except RegtestError as exc:
        if str(exc) != "Release requires a confirmed funding output":
            raise
    else:
        raise RegtestError("The registry accepted unconfirmed funding")
    print("PASS: registration rejects an admissible spend of unconfirmed funding")

    rpc.call("generatetoaddress", 1, miner)
    registered = register_release(rpc, expected, release_raw, vault_script=vault_script)
    other_raw = presign(
        rpc, refund_outpoint, recipient,
        vault_descriptor(release_wif, refund_pub, refund_height), locktime=0,
    )
    require(
        mempool_result(rpc, other_raw)["allowed"],
        "The other vault's release must be independently valid",
    )
    try:
        register_release(rpc, expected, other_raw, vault_script=vault_script)
    except RegtestError as exc:
        if str(exc) != "Release outpoint does not match the withdrawal":
            raise
    else:
        raise RegtestError("The registry accepted a valid spend of the wrong vault")
    print("PASS: registration rejects an independently valid spend of the wrong vault")

    for wrong in (
        replace(expected, amount_sats=PAYMENT_SATS - 1),
        replace(expected, recipient_script_pubkey=alternate_script),
        replace(expected, **refund_outpoint),
    ):
        try:
            registered.release_signed_transaction(wrong)
        except ReleaseRefused:
            continue
        raise RegtestError("The mock gate disclosed the signature for a mismatched claim")
    print("PASS: mock gate rejects a mismatched amount, recipient, or outpoint")

    opened = registered.release_signed_transaction(expected)
    require(opened == release_raw, "The gate must return the originally registered transaction")
    require(mempool_result(rpc, opened)["allowed"], "The fixed-output release was rejected")
    modified = mutate_recipient(opened, recipient_script, alternate_script)
    require(not mempool_result(rpc, modified)["allowed"], "The altered recipient was accepted")
    confirm_spend(rpc, opened, release_outpoint, recipient_script, miner)
    require(not mempool_result(rpc, opened)["allowed"], "A confirmed vault spend was accepted again")
    print("PASS: fixed-output payout confirmed and altered output and rebroadcast rejected")

    refund_raw = presign(
        rpc, refund_outpoint, alternate,
        vault_descriptor(release_pub, refund_wif, refund_height), locktime=refund_height,
    )
    early = mempool_result(rpc, refund_raw)
    require(not early["allowed"] and early.get("reject-reason") == "non-final", "Early refund was not blocked by locktime")
    rpc.call("generatetoaddress", refund_height - rpc.call("getblockcount"), miner)
    require(mempool_result(rpc, refund_raw)["allowed"], "Mature refund was rejected")
    confirm_spend(rpc, refund_raw, refund_outpoint, alternate_script, miner)
    print("PASS: CLTV refund rejected before maturity and confirmed after maturity")


def main(controls=run_controls, description=__doc__):
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--bitcoind", type=Path, required=True)
    parser.add_argument("--datadir", type=Path, required=True)
    args = parser.parse_args()
    try:
        with start_regtest(args.bitcoind.resolve(), args.datadir.resolve()) as rpc:
            controls(rpc)
    except (RegtestError, OSError, ValueError) as exc:
        # Keep local paths and RPC inputs out of CI logs.
        parser.exit(1, f"Regtest failed: {exc if isinstance(exc, RegtestError) else type(exc).__name__}\n")


if __name__ == "__main__":
    main()
