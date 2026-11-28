# Release statement for a Praxis experiment

The recorded batch proof becomes available to the prover before Settlement
receives it. The confirmed devnet `SubmitBatch` send contains that same proof.
In the validity-backed regtest run, Settlement accepted at Momentum 120 and
the withdrawal delay reached eligibility at Momentum 149.
[Proof-bearing receive](../research/zeno-validity-devnet/submit.ts) · [Recorded payout](../research/zeno-validity-devnet/README.md#verified-run)

Praxis's Groth16 construction fixes the instance before encryption and accepts
a proof for that instance as the decryption witness. Encoding the current
batch verifier would therefore make a decryption witness available during
batch submission. A release instance has to express the later authorization
condition inside the verified statement. [Praxis v1, Sections 1.1 and 6.1](https://arxiv.org/pdf/2611.28552v1)

## Public instance and release witness

Set the public instance before protecting the signed payout. It commits to
the source chain and genesis, Settlement instance and domain, approved
authorizer and module, and the checkpoint and refund rules. It also
commits to the Bitcoin target. The existing encoding supplies the Bitcoin
genesis, funding outpoint, payout amount and recipient script. The verifier
key and program identifier belong to the versioned release policy.
[Target encoding](../withdrawal_authorization.py)

Include the signed transaction digest in the fixed instance. After
encryption, bind the instance digest and ciphertext digest in the same
authenticated registration record. The ciphertext digest is computed after
the instance is fixed. At release, check the retrieved ciphertext and the
decrypted transaction against their registered digests.

A release witness supplies the future batch, its validity proof and an
authenticated source checkpoint. The release verifier must bind the exact
batch and target to that checkpoint, check the stored withdrawal deadline,
and prove the selected checkpoint meets the source-chain rule. Actual
Momentum hashes and input sequences become known during execution and
belong to that witness.

The current devnet selector reads two pinned full nodes and checks elected
producers and the branch they store. A portable verifier needs evidence the
circuit can check. An initial test can use explicitly identified checkpoint
authorities with a fixed verification policy. Early release in that test
depends on those authorities withholding their authorization until the
checkpoint is eligible. An independently verified
version needs a source-chain trust anchor and proofs for authorized producers,
branch selection and eligible Settlement state. Each variant carries its
own trust assumptions. [Devnet checkpoint qualification](../research/zeno-native-checkpoint/README.md)

## Bitcoin cutoff and reconciliation

At intake, confirm the registered funding outpoint and budget time for the
source delay, proving, ciphertext retrieval and Bitcoin confirmation. A
closed refund window or spent outpoint holds a new release. The native
regtest guard checks the original Bitcoin tip and registration again after
the source wait.

A disclosed witness can still open its ciphertext. A disclosed signed
transaction can compete for the funding output until a confirmed spend
consumes it. That spend determines whether payout or refund won under
the selected Bitcoin confirmation policy.
Reconcile an outstanding authorization with that outcome before reusing
any associated obligation. [Fixed-recipient withdrawal policy](withdrawal-policy.md)

## Setup and cost qualification

The pinned Ziren build path calls Groth16 setup when it generates a proving
and verification key. The regtest run binds the published key bytes by hash.
For encrypted release, qualify the setup provenance for the exact new
statement and key pair independently. The preprint's security argument
assumes an honest setup. [Ziren Groth16 build](https://github.com/ProjectZKM/Ziren/blob/c9988a098d92bc55a85ca9307c470d1f97be3337/crates/recursion/gnark-ffi/go/zkm/build.go#L258-L289) · [Praxis v1](https://arxiv.org/pdf/2611.28552v1)

At the preprint's stated 100-bit parameters, the authors estimate a 7.88 GiB
ciphertext, about 20 billion base-field multiplications to encrypt and
8.6 billion on average to decrypt. Measure the circuit encoding, actual
streamed ciphertext, encryption, decryption and retrieval for one synthetic
registration before choosing an implementation. Curve library throughput
alone gives a lower-level measurement for these estimates.
[Praxis v1, Section 6.3](https://arxiv.org/pdf/2611.28552v1)
