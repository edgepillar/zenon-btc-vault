"""Measure local regtest policy for CPFP and competing vault spends."""

from decimal import Decimal

from regtest import (
    COIN,
    VAULT_SATS,
    bitcoin_amount,
    fund_vault,
    main,
    mempool_result,
    presign,
    public_key,
    require,
    synthetic_wif,
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
    refund_wins = fund_vault(rpc, descriptor)
    package_wins = fund_vault(rpc, descriptor)
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
    standalone = mempool_result(rpc, first_release)
    require(
        not standalone["allowed"] and standalone.get("reject-reason") == "min relay fee not met",
        "The zero-fee release must fail standalone admission",
    )

    first_child = sign_fee_child(rpc, first_release, child_destination, 2_000)
    first_package = submit_release_package(rpc, first_release, first_child, 2_000)
    require(set(rpc.call("getrawmempool")) == first_package, "Expected the release and fee child in the mempool")
    print("PASS: recipient CPFP admits the zero-fee release after standalone admission fails")

    refund_id = rpc.call("sendrawtransaction", first_refund)
    require(set(rpc.call("getrawmempool")) == {refund_id}, "The refund did not displace the release and fee child")
    require_mined(rpc, miner, {refund_id}, first_package)
    print("PASS: at maturity the refund displaced the release package for the same outpoint")

    refund_id = rpc.call("sendrawtransaction", second_refund)
    require(set(rpc.call("getrawmempool")) == {refund_id}, "Expected the second refund in the mempool")
    second_child = sign_fee_child(rpc, second_release, child_destination, 10_000)
    second_package = submit_release_package(rpc, second_release, second_child, 10_000)
    require(set(rpc.call("getrawmempool")) == second_package, "The package did not displace the refund")
    require_mined(rpc, miner, second_package, {refund_id})
    print("PASS: after maturity the release package displaced the refund for the same outpoint")


if __name__ == "__main__":
    main(run_fee_controls, description=__doc__)
