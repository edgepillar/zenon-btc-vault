"""Measure local regtest policy for CPFP and competing vault spends."""

from dataclasses import replace
from decimal import Decimal

from mock_release import ReleaseRefused, Withdrawal
from regtest import (
    COIN,
    RegtestError,
    VAULT_SATS,
    bitcoin_amount,
    fund_vault,
    main,
    mempool_result,
    mutate_recipient,
    presign,
    public_key,
    register_release,
    require,
    synthetic_wif,
    vault_address,
    vault_descriptor,
)


def sign_fee_child(rpc, release, destination, fee_sats):
    parent = rpc.call("decoderawtransaction", release)
    payout, = parent["vout"]
    outpoint = {"txid": parent["txid"], "vout": payout["n"]}
    amount_sats = int(payout["value"] * COIN)
    unsigned = rpc.call(
        "createrawtransaction",
        [outpoint],
        [{destination: bitcoin_amount(amount_sats - fee_sats)}],
    )
    prevout = {
        **outpoint,
        "scriptPubKey": payout["scriptPubKey"]["hex"],
        "amount": bitcoin_amount(amount_sats),
    }
    signed = rpc.call("signrawtransactionwithwallet", unsigned, [prevout], "ALL", wallet=True)
    require(signed["complete"], "The recipient could not sign the fee child")
    return signed["hex"]


def submit_release_package(rpc, release, child, fee_sats):
    result = rpc.call("submitpackage", [release, child])
    require(result["package_msg"] == "success", "Bitcoin Core rejected the release package")
    transactions = result["tx-results"].values()
    fees = sorted(tx["fees"]["base"] for tx in transactions)
    require(fees == [0, Decimal(fee_sats) / COIN], "The child must pay the full package fee")
    return {tx["txid"] for tx in transactions}


def require_mined(rpc, miner, expected_txids, excluded_txids):
    block_hash, = rpc.call("generatetoaddress", 1, miner)
    included = set(rpc.call("getblock", block_hash)["tx"])
    require(expected_txids <= included, "The winning transactions were not mined")
    require(included.isdisjoint(excluded_txids), "A conflicting transaction was mined")


def run_fee_controls(rpc):
    rpc.call("createwallet", "funding")
    miner, recipient, refund_recipient, child_destination = [
        rpc.call("getnewaddress", "", "bech32", wallet=True) for _ in range(4)
    ]
    rpc.call("generatetoaddress", 101, miner)
    refund_height = rpc.call("getblockcount") + 3

    release_wif, refund_wif = synthetic_wif(), synthetic_wif()
    release_pub, refund_pub = public_key(rpc, release_wif), public_key(rpc, refund_wif)
    descriptor = vault_descriptor(release_pub, refund_pub, refund_height)
    address = vault_address(rpc, descriptor)
    vault_script = rpc.call("validateaddress", address)["scriptPubKey"]
    recipient_script = rpc.call("validateaddress", recipient)["scriptPubKey"]
    child_script = rpc.call("validateaddress", child_destination)["scriptPubKey"]
    refund_wins = fund_vault(rpc, address)
    package_wins = fund_vault(rpc, address)
    rpc.call("generatetoaddress", 1, miner)

    release_descriptor = vault_descriptor(release_wif, refund_pub, refund_height)
    refund_descriptor = vault_descriptor(release_pub, refund_wif, refund_height)
    first_release = presign(
        rpc, refund_wins, recipient, release_descriptor,
        locktime=0, payout_sats=VAULT_SATS,
    )
    first_refund = presign(
        rpc, refund_wins, refund_recipient, refund_descriptor,
        locktime=refund_height, payout_sats=VAULT_SATS - 20_000,
    )
    second_release = presign(
        rpc, package_wins, recipient, release_descriptor,
        locktime=0, payout_sats=VAULT_SATS,
    )
    second_refund = presign(
        rpc, package_wins, refund_recipient, refund_descriptor,
        locktime=refund_height, payout_sats=VAULT_SATS - 1_500,
    )

    first_claim, second_claim = (
        Withdrawal(
            source_network="synthetic-zenon",
            domain_id="test-vault",
            outbox_id=outbox_id,
            amount_sats=VAULT_SATS,
            recipient_script_pubkey=recipient_script,
            **outpoint,
        )
        for outpoint, outbox_id in ((refund_wins, "first-release"), (package_wins, "second-release"))
    )
    tip = rpc.call("getbestblockhash")
    first_registration, second_registration = (
        register_release(
            rpc, claim, release, vault_script=vault_script,
            fee_child=sign_fee_child(rpc, release, child_destination, 1_000),
        )
        for claim, release in ((first_claim, first_release), (second_claim, second_release))
    )
    altered = mutate_recipient(first_release, recipient_script, child_script)
    try:
        register_release(
            rpc, replace(first_claim, recipient_script_pubkey=child_script), altered,
            vault_script=vault_script, fee_child=sign_fee_child(rpc, altered, child_destination, 1_000),
        )
    except RegtestError as exc:
        if str(exc) != "generateblock: RPC error -25":
            raise
    else:
        raise RegtestError("Candidate block validation accepted an altered release signature")
    require(rpc.call("getbestblockhash") == tip, "Package registration changed the chain tip")
    require(not rpc.call("getrawmempool"), "Package registration changed the mempool")
    try:
        first_registration.release_signed_transaction(second_claim)
    except ReleaseRefused:
        pass
    else:
        raise RegtestError("The mock released a packaged spend for another vault's claim")
    print("PASS: zero-fee parents register without broadcast and require the matching claim")
    print("PASS: unsubmitted block validation rejects a release with an altered payout")

    rpc.call("generatetoaddress", 1, miner)
    require(rpc.call("getblockcount") == refund_height - 1, "Unexpected pre-refund chain height")
    early_refund = mempool_result(rpc, first_refund)
    require(
        not early_refund["allowed"] and early_refund.get("reject-reason") == "non-final",
        "The refund was not rejected at the last pre-maturity height",
    )
    rpc.call("generatetoaddress", 1, miner)
    require(rpc.call("getblockcount") == refund_height, "Unexpected refund maturity height")
    require(mempool_result(rpc, first_refund)["allowed"], "The refund must be valid at maturity")
    first_opened = first_registration.release_signed_transaction(first_claim)
    require(first_opened == first_release, "The mock must return the first registered parent")
    standalone = mempool_result(rpc, first_opened)
    require(
        not standalone["allowed"] and standalone.get("reject-reason") == "min relay fee not met",
        "The zero-fee release must fail standalone admission",
    )

    first_child = sign_fee_child(rpc, first_opened, child_destination, 2_000)
    first_package = submit_release_package(rpc, first_opened, first_child, 2_000)
    require(set(rpc.call("getrawmempool")) == first_package, "Expected the release and fee child in the mempool")
    print("PASS: recipient CPFP admits the zero-fee release after standalone admission fails")

    refund_id = rpc.call("sendrawtransaction", first_refund)
    require(set(rpc.call("getrawmempool")) == {refund_id}, "The refund did not displace the release and fee child")
    require_mined(rpc, miner, {refund_id}, first_package)
    print("PASS: at maturity the refund displaced the release package for the same outpoint")

    refund_id = rpc.call("sendrawtransaction", second_refund)
    require(set(rpc.call("getrawmempool")) == {refund_id}, "Expected the second refund in the mempool")
    second_opened = second_registration.release_signed_transaction(second_claim)
    require(second_opened == second_release, "The mock must return the second registered parent")
    second_child = sign_fee_child(rpc, second_opened, child_destination, 10_000)
    second_package = submit_release_package(rpc, second_opened, second_child, 10_000)
    require(set(rpc.call("getrawmempool")) == second_package, "The package did not displace the refund")
    require_mined(rpc, miner, second_package, {refund_id})
    mined_child = rpc.call("decoderawtransaction", second_child)
    payout = rpc.call("gettxout", mined_child["txid"], 0, False)
    require(
        payout is not None and payout["scriptPubKey"]["hex"] == child_script
        and payout["value"] * COIN == VAULT_SATS - 10_000,
        "Unexpected confirmed payout after the child fee",
    )
    print("PASS: after maturity the release package displaced the refund for the same outpoint")
    print("PASS: the confirmed payout equals the registered gross amount minus the child fee")


if __name__ == "__main__":
    main(run_fee_controls, description=__doc__)
