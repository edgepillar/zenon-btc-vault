# Fixed-recipient vault withdrawal policy

The fixture uses one confirmed P2WSH funding output and one predetermined Bitcoin recipient per withdrawal. Its mock release is a test interface for an exact authorized statement. The current Bitcoin checks run on a single Bitcoin Core 32.1 regtest node. Source-chain authorization and expiry enforcement are design requirements.

## Registration and publication

1. Pin the Bitcoin network, expected funding script and value, and source-chain and domain context independently of the signed transaction. Register the release against a confirmed UTXO that matches that vault. The fixture requires one funding confirmation.
2. Bind one immediate, signed release to that outpoint, the recipient script, and the gross amount. Check the selected witness branch and `SIGHASH_ALL` under the expected P2WSH commitment.
3. For a standalone release, check local admission through Bitcoin Core. For a zero-fee release, check a package containing the parent and a child that spends the payout and pays the entire fee. Store the parent only. Package admission at setup is a point-in-time observation.
4. At publication, use the exact registered parent. Check the current funding status and admission conditions. A recipient who funds a fresh child from the payout receives the gross amount minus the child fee. The Bitcoin fee fixture exercises a different child at publication and checks this net payout after mining.

## Withdrawal window and refund

The CLTV refund becomes eligible when the tip reaches the configured height and the next block can include it. The immediate release remains eligible while the funding output is unspent. At maturity both transactions can compete for the same output. The fixture deliberately opens releases in that window to observe both replacement orders.

For a future source-chain integration, set a cutoff for taking on new withdrawal obligations before the refund becomes eligible. Budget time for source finality, proof production, retrieval and release, Bitcoin propagation and confirmation, and an operating margin. Observe the chains separately and define how the cutoff is evaluated when their views disagree or progress stalls. Block height provides an ordering point. It cannot reserve a confirmation.

The source-side rule must account for already-issued authorizations and signed Bitcoin transactions when the cutoff is reached. A previously disclosed release remains usable until its input is spent, even after an off-chain cutoff. A mempool observation records an attempt. An accepted confirmation at the chosen depth resolves which branch spent the vault. Pending, replaced, or reorganized spends require reconciliation before another attempt can alter the original obligation. Any source debit or compensation policy needs to cover a valid authorization whose Bitcoin refund wins.

## Evidence required from the source chain

Bind an authenticated source network and anchor, a domain and protocol version, the accepted batch and withdrawal identity, and the Bitcoin network, vault outpoint, funding policy, gross amount and recipient script. Verify both the batch record under the accepted state root and the withdrawal under the batch's outbox commitment. Record the finality rule and the trust inputs it uses. Test that an unexpected root, domain, withdrawal, or finality result refuses release. The mock exercises field matching only.
