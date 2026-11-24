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

The fixture funds a P2WSH vault and registers one pre-signed release transaction. Setup checks the input outpoint, payout, immediate release branch, and `SIGHASH_ALL` signature, then asks Bitcoin Core to validate the spend against the current UTXO and mempool. The mock returns only the stored bytes for an exact synthetic withdrawal match. The run checks that registration rejects an otherwise valid spend of another vault. It also checks the confirmed payout, rejection of an altered output and rebroadcast of the confirmed transaction, and a separate vault's `OP_CHECKLOCKTIMEVERIFY` refund.

The separate fee fixture exercises recipient-paid child-pays-for-parent (CPFP) and two competing spends at refund maturity. A zero-fee release fails standalone mempool admission. A recipient-signed fee child pays for the package. In separate funded vaults, the fixture demonstrates each replacement order and checks which transactions are mined. These are local Bitcoin Core 32.1 policy observations in an isolated regtest node. Relay and confirmation on other networks depend on node and miner policy.

## Trust and scope

The mock holds an already signed transaction in cleartext and controls its release inside the test process. The registration uses trusted setup data and Bitcoin Core's local acceptance policy at the time of registration. The fixture exercises the release interface and Bitcoin transaction constraints. Witness encryption, confidential setup, and verification of Zenon state or consensus finality remain open. A future proof backend can implement the same release boundary with an authenticated, deposit-specific withdrawal statement.

The fixed-output spend covers a single known recipient. A transferable BTC peg would also need accounting, recipient selection and refund reconciliation. The test uses regtest fees and synthetic funds to exercise the transaction flow. Production proving, storage and fee costs remain unmeasured.

The vault is an independent experiment inspired by David Tse, Nikhil Vanjani, and Psi Vesely, *Praxis: Practical Witness Encryption for NP*, IACR ePrint 2026/2971. [Read the Praxis preprint](https://eprint.iacr.org/2026/2971)
