# Groth16 authorization statement for the Zeno vault fixture

This experiment proves the successful authorization call in the recorded
[Zeno execution replay](../zeno-execution-replay/README.md). The verifier selects
its expected statement by re-executing the digest-pinned devnet capture and
matching its Settlement batch entries. The probe checks the pre-state witnesses
for an empty authorization slot and a purchased, unused 96-byte quota.

The BN254 circuit takes the fixed-width Bitcoin target as a witness and computes
three values with SHA3-256. It reconstructs the regtest target commitment with
the existing authorization encoding, the canonical zero-deposit `CallL2`
input hash for the `authorize` selector, and the hash of the canonical state
diff. The two sorted writes store the target commitment in slot `01` and update
the quota from 96 purchased and zero used to 96 purchased and 96 used. The
circuit enforces the fixture's authorized caller, contract, domain, Bitcoin
genesis and P2WPKH output format.

Its public statement is a SHA3-256 digest of the
`zenon-btc-vault/groth16-authorization\x00\x01` tag, the capture context
digest, contract ID, caller address, source send hash, target commitment,
input hash and state-diff hash in that order. The capture context digest is
SHA-256 over the concatenated 32-byte authorization and execution capture
digests. The verifier computes the expected public digest from the selected
execution record and the existing Bitcoin target binding.

The [pinned gnark release](https://github.com/Consensys-Incorporated/gnark/releases/tag/v0.16.5)
supplies the Keccak-f permutation and the Groth16 prover and verifier. Fixed
SHA3 padding and bitwise absorption keep the circuit in ordinary R1CS. The
tool requires zero additional proof commitments. The hosted experiment uses
an ephemeral circuit-specific setup and the public synthetic capture. The
retained bundle holds the verifying key and proof.

## Recorded run

The [hosted proof run](https://github.com/edgepillar/zenon-btc-vault/actions/runs/45850625634)
at `f11b33217abf1a28da5964bd721fe38cbcb8845d` used Linux amd64, Go 1.26.8
and gnark 0.16.5. It selected the successful call from devnet batch 5 and
proved the authorization statement with 663,415 constraints and zero
additional commitments. The compressed gnark proof is 164 bytes and the
compressed verifying key is 1,356 bytes. The proof stage took about 114
seconds and peaked at 1.58 GiB of resident memory. This measurement includes
compilation of the circuit, setup, proving and both positive and negative
verification controls.

The circuit refused modified caller and recipient witnesses. The existing
proof failed verification after a change to the selected capture context or
state-diff hash. The [retained proof bundle](proof.json) matches its
[SHA-256 selection](proof.sha256). Subsequent pushes verify the retained
proof under the expected statement from a fresh execution replay of the
same captured inputs.

## Proof boundary

The circuit covers the authorization rule and its two writes. The external
replay establishes the selected checkpoint, input context, pre-state reads,
contract identity and batch commitments. The Zeno Phase 2 outline calls for
a full STF guest, an L1-authenticated input chain and Settlement verification
under the domain registry key. Those requirements and independently verified
mainnet finality remain open. [Phase 2 outline](https://github.com/Zeno-L2/zeno-docs/blob/d74ba1fc4b01583af0daf33a2dedfc84ea2b9419/phase-2/SPEC.md)
