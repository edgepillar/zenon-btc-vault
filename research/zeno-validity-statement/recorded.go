//go:build !ziren

package main

import (
	"bytes"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"reflect"

	"github.com/Zeno-L2/zeno-executor/internal/codec"
	"github.com/Zeno-L2/zeno-executor/internal/ledger"
	"github.com/Zeno-L2/zeno-executor/internal/settlement"
	"github.com/Zeno-L2/zeno-executor/internal/znnd"
	"github.com/zenon-network/go-zenon/common/trie"
	"github.com/zenon-network/go-zenon/common/types"
	"github.com/zenon-network/go-zenon/common/vaultvalidity"
	"github.com/zenon-network/go-zenon/vm/embedded/definition"
)

func readWitnessFile(name string, value any) error {
	file, err := os.Open(name)
	if err != nil {
		return err
	}
	defer file.Close()
	raw, err := io.ReadAll(io.LimitReader(file, maxWitnessSize+1))
	if err != nil || len(raw) == 0 || len(raw) > maxWitnessSize {
		return errors.New("invalid witness file size")
	}
	return json.Unmarshal(raw, value)
}

func selectBatchEntry(id uint64, checkpointHeight uint64, proof znnd.Proof) (definition.BatchEntry, error) {
	var entry definition.BatchEntry
	key := (&definition.BatchEntryKey{DomainId: domainID, BatchId: id}).Key()
	key = append(append(append([]byte{0x03}, types.SettlementContract.Bytes()...), 0x04), key...)
	if ok, err := trie.VerifyProofByPath(proof.Root, trie.Path(types.NewHash(key)), proof.Value, proof.Proof); err != nil || !ok {
		return entry, errors.New("archived Settlement inclusion failed")
	}
	err := definition.ABISettlement.UnpackVariable(&entry, definition.BatchEntryVariableName, proof.Value)
	if err != nil || !entry.IsFinalized(checkpointHeight) {
		return entry, errors.New("archived Settlement entry is ineligible")
	}
	return entry, nil
}

func recordedProposal(batchPath, executionPath string) (proposalWitness, error) {
	var archived batchWitness
	var history struct {
		Schema int            `json:"schema"`
		Proofs []znnd.Proof   `json:"batchProofs"`
		Inputs []ledger.Input `json:"inputs"`
	}
	var w proposalWitness
	if err := readWitnessFile(batchPath, &archived); err != nil {
		return w, err
	}
	if err := readWitnessFile(executionPath, &history); err != nil {
		return w, err
	}
	if archived.Schema != 1 || history.Schema != 1 || archived.BatchID != 4 || len(history.Proofs) != 5 ||
		len(archived.Inputs) != callCount || len(history.Inputs) != 4+callCount ||
		!reflect.DeepEqual(history.Inputs[4:], archived.Inputs) {
		return w, errors.New("expected the three archived calls and their accepted prefix")
	}
	parentProof := history.Proofs[3]
	if parentProof.Root != archived.SettlementProof.Root ||
		!reflect.DeepEqual(history.Proofs[4], archived.SettlementProof) {
		return w, errors.New("archived checkpoints differ")
	}
	parent, err := selectBatchEntry(3, archived.CheckpointHeight, parentProof)
	if err != nil {
		return w, err
	}
	target, err := selectBatchEntry(4, archived.CheckpointHeight, archived.SettlementProof)
	if err != nil || target.PreStateRoot != parent.PostStateRoot || target.FirstInputSeq <= parent.LastInputSeq {
		return w, errors.New("archived parent differs from the proposal pre-state")
	}
	// Use a public fixture address as the candidate submitter. Hosted replay
	// has no transaction signer. The proof commits to these exact address bytes.
	proposal := codec.BatchCommitment{
		BatchID: archived.BatchID, DomainID: domainID, PreStateRoot: parent.PostStateRoot,
		FirstInputSeq: target.FirstInputSeq, LastInputSeq: target.LastInputSeq,
		ExecutorID: archived.Inputs[1].Ctx.L1Sender,
	}
	ordinal := uint64(len(history.Inputs) - callCount)
	w = proposalWitness{
		Schema: 1, Ordinal: ordinal, Start: statement.Seed(chainID, domainID, proposal.BatchID, ordinal, proposal.PreStateRoot),
		Contract: archived.ContractID, Module: archived.Module, Leaves: archived.Leaves, Inputs: archived.Inputs,
	}
	replayed, err := replayProposal(w, proposal)
	if err != nil {
		return w, err
	}
	if err := settlement.EntryMatches(&target, replayed); err != nil {
		return w, err
	}
	w.Commitment, err = replayed.Encode()
	return w, err
}

func runRecorded(args []string) error {
	if len(args) != 3 {
		return errors.New("expected ARCHIVED_BATCH ARCHIVED_EXECUTION PROPOSAL_OUTPUT")
	}
	w, err := recordedProposal(args[0], args[1])
	if err != nil {
		return err
	}
	journal, end, err := verifyProposal(w)
	if err != nil {
		return err
	}
	raw, err := json.Marshal(w)
	if err != nil || len(raw) > maxWitnessSize {
		return errors.New("invalid generated proposal witness")
	}
	file, err := os.OpenFile(args[2], os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0o600)
	if err != nil {
		return err
	}
	_, writeErr := io.Copy(file, bytes.NewReader(raw))
	if err := errors.Join(writeErr, file.Close()); err != nil {
		return err
	}
	fmt.Printf("PROPOSAL_JOURNAL %s\nPROPOSAL_CHAIN_END %s\n", hex.EncodeToString(journal[:]), hex.EncodeToString(end[:]))
	return nil
}

func main() {
	if err := runRecorded(os.Args[1:]); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
