# Bitcoin withdrawal authorization

The fixed-recipient regtest checks a versioned Bitcoin withdrawal message against a trusted outbox root before opening a registered transaction. The Bitcoin registration has already checked the funded UTXO, payout, sequence, witness, and signature. At release, the caller supplies the outbox message and its Merkle branch. Setup supplies the expected source batch, emitting contract, Bitcoin genesis hash, and trusted root independently.

## Message encoding

The following fields are concatenated in order. Integers are unsigned and big-endian. Hexadecimal identifiers are decoded to bytes. The transaction ID uses the byte order displayed by Bitcoin Core RPC.

| Field | Encoding |
| --- | --- |
| Protocol and version | ASCII `zenon-btc-vault/withdrawal`, followed by bytes `00 01` |
| Source chain, domain, and batch | `u64`, `u32`, and `u64` |
| Source contract and outbox identifier | Two 32-byte values |
| Bitcoin network | 32-byte genesis block hash |
| Vault outpoint | 32-byte transaction ID and `u32` output index |
| Gross payout | `u64` satoshis |
| Recipient | `u16` script length followed by the exact script bytes |

The outbox leaf hashes byte `00` followed by the message with SHA3-256. A branch node hashes byte `01` followed by its left and right hashes. The branch encodes the step count as `u32` and each step as a one-byte sibling side and a 32-byte sibling. The Bitcoin node's observed genesis must match the registered network, the branch must reach the trusted root, and the authenticated message must equal the whole expected encoding. The gate then returns the original signed transaction bytes. The recipient can fund a different CPFP child without changing those bytes. [Zeno Merkle proof rules](https://github.com/digitalSloth/go-zenon/blob/2835c436b197fece5c76adf4ba8dc1decca2e3ea/vm/embedded/implementation/settlement.go#L1601-L1629)

## Tested evidence

The Bitcoin fee fixture creates two authorized Bitcoin withdrawals under one synthetic outbox root. Each branch is valid. The gate refuses the other vault's message and opens only the parent registered for its own message. Both release and refund orders still run on the isolated Bitcoin node.

The refusal test also reuses the actual outbox message and proof from Zeno devnet domain 1, batch 5. It uses the outbox root previously verified through the Settlement record at Momentum 115. The Merkle branch passes. The native withdrawal carries a Zenon recipient and 0.4 synthetic ZNN, so its bytes do not authorize the registered Bitcoin network, outpoint, script, or value. The original capture has SHA-256 `81c5739a3632cea328626b5ddde0732f2324d444800c2b532f8583770538bb21`. [Devnet capture](https://github.com/edgepillar/zenon-btc-vault/blob/8b47e415553a3cde37b6524b21ab0833ac11479c/research/zeno-withdrawal/evidence.json) · [Checkpoint replay](https://github.com/edgepillar/zenon-btc-vault/actions/runs/45747504011)

## Source and release assumptions

This wire format belongs to the Bitcoin vault experiment. A future source adapter must establish that the correct Zenon contract issued the Bitcoin authorization and that a checkpoint acceptable under the chosen consensus policy commits to it. The bytes inside an outbox leaf identify the requested contract. Contract execution and provenance must also be checked by the source adapter. The recorded devnet checkpoint supplies evidence for the native ZNN receipt.

The gate runs on Bitcoin regtest and holds the parent's signed bytes in cleartext inside the test process. Replaying the same authorization returns the same parent for the same input. The existing [withdrawal policy](withdrawal-policy.md) describes responsibilities for publication, the cutoff, and settlement after a competing refund.
