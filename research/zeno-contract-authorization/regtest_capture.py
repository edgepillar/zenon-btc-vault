"""Authorize and mine one previously registered release on isolated networks."""

import argparse
from hashlib import sha3_256
import json
from pathlib import Path
from struct import pack
import subprocess

from mock_release import Withdrawal
from regtest import (
    PAYMENT_SATS,
    RegtestError,
    confirm_spend,
    fund_vault,
    presign,
    public_key,
    register_release,
    require,
    start_regtest,
    synthetic_wif,
    vault_address,
    vault_descriptor,
)
from withdrawal_authorization import REGTEST_GENESIS, encode_bitcoin_target


def run_capture(rpc, devnet, artifact, target_file, evidence_file, replay_binary, execution_file):
    rpc.call("createwallet", "funding")
    miner, recipient = [rpc.call("getnewaddress", "", "bech32", wallet=True) for _ in range(2)]
    script = rpc.call("validateaddress", recipient)["scriptPubKey"]
    rpc.call("generatetoaddress", 101, miner)
    refund_height = rpc.call("getblockcount") + 5
    release_key = synthetic_wif()
    release_public = public_key(rpc, release_key)
    refund_public = public_key(rpc, synthetic_wif())
    vault = vault_address(rpc, vault_descriptor(release_public, refund_public, refund_height))
    outpoint = fund_vault(rpc, vault)
    rpc.call("generatetoaddress", 1, miner)
    withdrawal = Withdrawal("zenon:69", "1", "single-contract-slot", amount_sats=PAYMENT_SATS,
                            recipient_script_pubkey=script, **outpoint)
    signed = presign(rpc, outpoint, recipient, vault_descriptor(release_key, refund_public, refund_height), 0)
    registered = register_release(rpc, withdrawal, signed, vault_script=rpc.call("validateaddress", vault)["scriptPubKey"])
    genesis = rpc.call("getblockhash", 0)
    require(genesis == REGTEST_GENESIS, "Expected the Bitcoin regtest genesis")
    tip = rpc.call("getbestblockhash")
    require(not rpc.call("getrawmempool"), "Registration admitted a transaction to the mempool")
    target = encode_bitcoin_target(registered.withdrawal, genesis)
    with target_file.open("x", encoding="ascii") as output:
        output.write(target.hex() + "\n")
    print("PASS: Bitcoin Core registered an exact signed release before Zeno authorization", flush=True)

    # Exit success from the pinned local verifier is required before using the captured result.
    subprocess.run([
        "npm", "exec", "--offline", "--", "tsx", "flows/vault-authorizer.ts", "capture",
        artifact, target_file, evidence_file,
    ], cwd=devnet, check=True, timeout=29 * 60)
    with evidence_file.open("rb") as captured:
        raw = captured.read(65_537)
    require(len(raw) <= 65_536, "Authorization evidence exceeds its byte limit")
    evidence = json.loads(raw)
    selection = evidence["selection"]
    contract_id = bytes.fromhex(selection["contractId"])
    require(len(contract_id) == 32, "Expected a 32-byte contract ID")
    commitment = sha3_256(
        b"zenon-btc-vault/contract-authorization\x00\x01" + pack(">QI", 69, 1) + contract_id + target
    ).hexdigest()
    require(selection["bitcoinTarget"] == target.hex(), "The verified target differs from registration")
    require(evidence["storage"]["result"]["value"] == commitment, "The proof did not bind the registered target")
    subprocess.run([
        replay_binary, "capture", evidence_file, execution_file, target_file.parent,
    ], check=True, timeout=120)
    require(rpc.call("getbestblockhash") == tip, "The Bitcoin tip changed while verifying Zeno")
    require(not rpc.call("getrawmempool"), "A transaction appeared in the mempool before authorization")
    require(rpc.call("getblockhash", 0) == genesis, "The Bitcoin network changed before release")
    release = registered.release_signed_transaction(withdrawal)
    require(release == signed, "The registration did not return the original bytes")
    confirm_spend(rpc, release, outpoint, script, miner)
    print("PASS: mined the exact registered Bitcoin release after verifying Zeno state and re-executing the batches", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("bitcoind", "datadir", "devnet", "artifact", "target", "evidence", "execution-replay", "execution-evidence"):
        parser.add_argument(f"--{name}", required=True, type=Path)
    args = parser.parse_args()
    try:
        with start_regtest(args.bitcoind.resolve(), args.datadir.resolve()) as rpc:
            run_capture(
                rpc, args.devnet.resolve(), args.artifact.resolve(), args.target.resolve(), args.evidence.resolve(),
                args.execution_replay.resolve(), args.execution_evidence.resolve(),
            )
    except (RegtestError, OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        reason = str(exc) if isinstance(exc, RegtestError) else type(exc).__name__
        parser.exit(1, f"Contract-authorized regtest failed: {reason}\n")


if __name__ == "__main__":
    main()
