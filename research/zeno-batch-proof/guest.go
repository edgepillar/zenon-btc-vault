//go:build ziren

package main

import (
	"bytes"
	"crypto/sha3"
	"encoding/json"
	"errors"
	"fmt"

	"github.com/ProjectZKM/Ziren/crates/go-runtime/zkvm_runtime"
	"github.com/Zeno-L2/zeno-executor/domain"
	"github.com/Zeno-L2/zeno-executor/internal/batch"
	"github.com/Zeno-L2/zeno-executor/internal/codec"
	"github.com/Zeno-L2/zeno-executor/internal/ledger"
	"github.com/Zeno-L2/zeno-executor/internal/settlement"
	"github.com/Zeno-L2/zeno-executor/internal/state"
	"github.com/Zeno-L2/zeno-executor/plugins/wasm"
	"github.com/zenon-network/go-zenon/common/crypto"
	"github.com/zenon-network/go-zenon/common/trie"
	"github.com/zenon-network/go-zenon/common/types"
	"github.com/zenon-network/go-zenon/vm/embedded/definition"
)

const (
	domainID     = 1
	inputCount   = 3
	witnessLimit = 49_152
)

type batchInputStream struct {
	inputs []ledger.Input
	next   int
}

func (s *batchInputStream) Next() (ledger.Input, bool, error) {
	if s.next == len(s.inputs) {
		return ledger.Input{}, false, nil
	}
	input := s.inputs[s.next]
	s.next++
	return input, true, nil
}

type memoryBundle struct {
	hash types.Hash
	data []byte
}

func (s *memoryBundle) Put(hash types.Hash, data []byte) error {
	s.hash, s.data = hash, bytes.Clone(data)
	return nil
}

func (s *memoryBundle) Get(hash types.Hash) ([]byte, error) {
	if s.data == nil || s.hash != hash {
		return nil, errors.New("missing in-memory DA bundle")
	}
	return s.data, nil
}

type receiptCheck struct{}

func (receiptCheck) Put(_ types.Hash, root types.Hash, sidecar []byte) error {
	_, leaves, err := codec.DecodeReceiptSidecar(sidecar)
	if err != nil || codec.MerkleRoot(leaves) != root {
		return errors.New("invalid receipt sidecar")
	}
	return nil
}

func verifySettlement(w batchWitness) (definition.BatchEntry, error) {
	var entry definition.BatchEntry
	p := w.SettlementProof
	key := (&definition.BatchEntryKey{DomainId: domainID, BatchId: w.BatchID}).Key()
	key = append(append(append([]byte{0x03}, types.SettlementContract.Bytes()...), 0x04), key...)
	path := trie.Path(types.NewHash(key))
	if w.CheckpointHash == (types.Hash{}) || len(p.Value) == 0 {
		return entry, errors.New("missing checkpoint or Settlement value")
	}
	if ok, err := trie.VerifyProofByPath(p.Root, path, p.Value, p.Proof); err != nil || !ok {
		return entry, errors.New("Settlement inclusion failed")
	}
	err := definition.ABISettlement.UnpackVariable(&entry, definition.BatchEntryVariableName, p.Value)
	if err != nil || entry.State != 0 || entry.FinalizeAtHeight == 0 ||
		entry.FinalizeAtHeight < entry.SubmittedAtHeight || !entry.IsFinalized(w.CheckpointHeight) {
		return entry, errors.New("ineligible Settlement entry")
	}
	return entry, nil
}

func openPreState(leaves []batchLeaf, want types.Hash) (*state.Store, error) {
	if len(leaves) == 0 || len(leaves) > 64 {
		return nil, errors.New("invalid pre-state leaf count")
	}
	diff := make([]domain.StateDiffEntry, len(leaves))
	for i, leaf := range leaves {
		if leaf.Value == nil || (i > 0 && bytes.Compare(leaves[i-1].Path[:], leaf.Path[:]) >= 0) {
			return nil, errors.New("pre-state leaves must have distinct ordered paths and values")
		}
		diff[i] = domain.StateDiffEntry{Path: trie.Path(leaf.Path), Value: leaf.Value}
	}
	store := state.OpenEmpty()
	root, err := store.Apply(diff)
	if err != nil || root != want {
		return nil, errors.New("pre-state root mismatch")
	}
	return store, nil
}

func verifyInputs(w batchWitness, entry definition.BatchEntry) error {
	if len(w.Inputs) != inputCount || entry.LastInputSeq < entry.FirstInputSeq ||
		entry.LastInputSeq-entry.FirstInputSeq != inputCount-1 {
		return errors.New("expected the full three-input range")
	}
	for i, input := range w.Inputs {
		ctx := input.Ctx
		if ctx.GlobalInputIndex != entry.FirstInputSeq+uint64(i) ||
			ctx.MomentumHeight == 0 || ctx.MomentumHeight > w.CheckpointHeight ||
			ctx.MomentumHash == (types.Hash{}) || len(ctx.SourceData) != 0 || ctx.Caps != nil {
			return errors.New("invalid selected input context")
		}
		decoded, err := codec.DecodeInput(input.Payload)
		if err != nil {
			return err
		}
		call, ok := decoded.Args.(codec.CallL2Args)
		if !ok || call.TargetContract != w.ContractID || call.DepositAmount != (codec.U256{}) {
			return errors.New("expected a zero-deposit call to the selected contract")
		}
	}
	return nil
}

func verifyBatch(raw []byte) error {
	if len(raw) > witnessLimit {
		return errors.New("witness exceeds the selected batch size limit")
	}
	var w batchWitness
	if err := json.Unmarshal(raw, &w); err != nil || w.Schema != 1 {
		return errors.New("invalid batch witness")
	}
	entry, err := verifySettlement(w)
	if err != nil {
		return err
	}
	if err := verifyInputs(w, entry); err != nil {
		return err
	}
	store, err := openPreState(w.Leaves, entry.PreStateRoot)
	if err != nil {
		return err
	}
	if len(w.Module) == 0 || types.NewHash(w.Module) != w.ModuleHash {
		return errors.New("WASM source hash mismatch")
	}
	plugin, err := wasm.New(wasm.Config{
		NetworkName: "devnet",
		FetchModule: func(hash types.Hash) ([]byte, bool, error) {
			if hash != w.ModuleHash {
				return nil, false, errors.New("unexpected WASM module")
			}
			return w.Module, true, nil
		},
	})
	if err != nil {
		return err
	}
	defer plugin.Close()

	stream := &batchInputStream{inputs: w.Inputs}
	driver, err := batch.New(batch.Config{
		DomainID: domainID, Domain: plugin, Store: store,
		Inputs: stream, Bundles: &memoryBundle{}, Receipts: receiptCheck{}, FirstBatchID: w.BatchID,
	})
	if err != nil {
		return err
	}
	result, ok, err := driver.NextBatchTo(entry.LastInputSeq)
	if err != nil || !ok || stream.next != len(w.Inputs) || len(result.Receipts) != inputCount {
		return fmt.Errorf("full batch re-execution failed: %v", err)
	}
	if err := settlement.EntryMatches(&entry, result.Commitment); err != nil {
		return err
	}
	statuses := [inputCount]codec.Status{codec.StatusRevert, codec.StatusSuccess, codec.StatusRevert}
	abortCodes := [inputCount]int32{1, 0, 2}
	for i, receipt := range result.Receipts {
		if receipt.Status != statuses[i] || receipt.AbortCode != abortCodes[i] ||
			receipt.ContractID != w.ContractID || receipt.OutboxCount != 0 {
			return fmt.Errorf("unexpected receipt at position %d", i)
		}
	}
	return nil
}

func verifyHashImplementation() error {
	sample := make([]byte, 1024)
	for i := range sample {
		sample[i] = byte(i * 13)
	}
	for _, n := range []int{0, 64, 135, 136, 137, len(sample)} {
		digest := sha3.Sum256(sample[:n])
		if !bytes.Equal(crypto.Hash(sample[:n/2], sample[n/2:n]), digest[:]) {
			return fmt.Errorf("SHA3-256 parity failed at length %d", n)
		}
	}
	return nil
}

func main() {
	raw := zkvm_runtime.Read[[]byte]()
	if len(raw) == 0 {
		// The host accepts the empty digest only in the execution smoke command.
		if err := verifyHashImplementation(); err != nil {
			fmt.Println(err)
			zkvm_runtime.RuntimeExit(1)
			return
		}
	} else if err := verifyBatch(raw); err != nil {
		fmt.Println(err)
		zkvm_runtime.RuntimeExit(1)
		return
	}
	zkvm_runtime.Commit(zkvm_runtime.Sha256(raw))
}
