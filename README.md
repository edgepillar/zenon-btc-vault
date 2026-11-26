# Zenon Bitcoin Vault

A Bitcoin Core regtest fixture for a vault with a fixed withdrawal destination. It exercises the proposed boundary between a Zenon withdrawal proof and a Bitcoin spend. All keys and funds in the fixture are synthetic.

[Public research brief and roadmap](https://edgepillar.com/zenon-btc-vault-research/)

## Run locally

The unit tests require Python 3.9 or later. The integration runner uses Bitcoin Core 32.1 with wallet support and an existing empty data directory. It starts a regtest node with loopback RPC and peer networking disabled. Hosted CI runs the integration on Linux with the same pinned [Bitcoin Core release](https://bitcoincore.org/en/releases/32.1/).

```sh
python3 -B -m unittest discover -s tests -v
python3 -B regtest.py --bitcoind path/to/bitcoind --datadir path/to/empty-directory
python3 -B regtest_fee_paths.py --bitcoind path/to/bitcoind --datadir path/to/second-empty-directory
```

The fixture funds a P2WSH vault and registers one pre-signed release transaction. Setup checks the confirmed outpoint, the expected P2WSH commitment and funding value, the payout, and the immediate release branch and `SIGHASH_ALL` signature. Bitcoin Core checks the proposed spend without broadcasting it. The mock returns only the stored bytes for an exact synthetic withdrawal match. The run checks that registration rejects otherwise admissible spends of an unconfirmed output and another vault. It also checks the confirmed payout, rejection of an altered output and rebroadcast of the confirmed transaction, and a separate vault's `OP_CHECKLOCKTIMEVERIFY` refund.

The fee fixture checks a zero-fee parent and recipient-signed child in an unsubmitted candidate block at registration and stores only the parent. It opens that parent using an inclusion proof for the exact Bitcoin authorization under a synthetic outbox root, then submits it with a fresh fee child. The run observes standalone rejection, package acceptance, both replacement orders at or after refund maturity, and the confirmed payout after the child fee. These are single-node Bitcoin Core 32.1 regtest observations with peer networking disabled. [Authorization encoding and evidence](docs/withdrawal-authorization.md) · [Withdrawal policy](docs/withdrawal-policy.md)

The [Zeno contract authorization fixture](research/zeno-contract-authorization/README.md) uses a deployed immutable WASM contract on an isolated devnet. It verifies the stored Bitcoin target commitment under a selected Momentum checkpoint before mining the exact registered release on Bitcoin regtest.

The execution replay fixture adds a second check before release. It rebuilds the devnet batches from their L1 inputs and compares the computed commitments with the proven Settlement entries. [Execution replay evidence](research/zeno-execution-replay/README.md)

The Groth16 experiment proves the selected authorization call and its two state writes against a public statement derived from the recorded replay. [Authorization proof experiment](research/authorization-proof/README.md)

## Trust and scope

The mock holds an already signed transaction in cleartext and controls its release inside the test process. The registration uses trusted setup data and Bitcoin Core's local acceptance policy at the time of registration. The outbox fixture trusts its synthetic batch root. The contract fixture trusts the isolated devnet's checkpoint selection and executor attestation. Zenon mainnet consensus finality, asset-backed authorization, confidential setup, and witness encryption remain open.

The fixed-output spend covers a single known recipient. A transferable BTC peg would also need accounting, recipient selection and refund reconciliation. The test uses regtest fees and synthetic funds to exercise the transaction flow. Production proving, storage and fee costs remain unmeasured.

The vault is an independent experiment inspired by David Tse, Nikhil Vanjani, and Psi Vesely, *Praxis: Practical Witness Encryption for NP*, IACR ePrint 2026/2971. [Read the Praxis preprint](https://eprint.iacr.org/2026/2971)
