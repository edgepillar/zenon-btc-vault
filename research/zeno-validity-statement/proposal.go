package main

import (
	"bytes"
	"errors"
	"fmt"
	"math"

	"github.com/Zeno-L2/zeno-executor/domain"
	"github.com/Zeno-L2/zeno-executor/internal/batch"
	"github.com/Zeno-L2/zeno-executor/internal/codec"
	"github.com/Zeno-L2/zeno-executor/internal/ledger"
	"github.com/Zeno-L2/zeno-executor/internal/state"
	"github.com/Zeno-L2/zeno-executor/plugins/wasm"
	"github.com/zenon-network/go-zenon/common/trie"
	"github.com/zenon-network/go-zenon/common/types"
	"github.com/zenon-network/go-zenon/common/vaultvalidity"
)

const (
	chainID        = 69
	domainID       = 1
	callCount      = 3
	maxWitnessSize = 49_152
	authorizerHash = "b372ca2a5ba902364c07af2e2a2719acf4a524fcaed11b130588a77fa9ac73bd"
)

type proposalWitness struct {
	Schema     int            `json:"schema"`
	Commitment []byte         `json:"commitment"`
	Ordinal    uint64         `json:"firstOrdinal"`
	Start      types.Hash     `json:"inputChainStart"`
	Contract   types.Hash     `json:"contract"`
	Module     []byte         `json:"module"`
	Leaves     []batchLeaf    `json:"leaves"`
	Inputs     []ledger.Input `json:"inputs"`
}

type proposalInputs struct {
	inputs []ledger.Input
	next   int
}

func (s *proposalInputs) Next() (ledger.Input, bool, error) {
	if s.next == len(s.inputs) {
		return ledger.Input{}, false, nil
	}
	input := s.inputs[s.next]
	s.next++
	return input, true, nil
}

type proposalBundle struct {
	hash types.Hash
	data []byte
}

func (s *proposalBundle) Put(hash types.Hash, raw []byte) error {
	s.hash, s.data = hash, bytes.Clone(raw)
	return nil
}

func (s *proposalBundle) Get(hash types.Hash) ([]byte, error) {
	if s.data == nil || s.hash != hash {
		return nil, errors.New("unknown proposal bundle")
	}
	return s.data, nil
}

type proposalReceipts struct{}

func (proposalReceipts) Put(_ types.Hash, root types.Hash, raw []byte) error {
	_, leaves, err := codec.DecodeReceiptSidecar(raw)
	if err != nil || codec.MerkleRoot(leaves) != root {
		return errors.New("proposal receipt sidecar differs from its root")
	}
	return nil
}

func rollInputChain(w proposalWitness, first, last uint64) (statement.Digest, error) {
	if len(w.Inputs) != callCount || last < first || w.Ordinal > math.MaxUint64-callCount ||
		w.Start.IsZero() || w.Contract.IsZero() {
		return statement.Digest{}, errors.New("invalid proposal range or chain start")
	}
	end := statement.Digest(w.Start)
	for i, input := range w.Inputs {
		ctx := input.Ctx
		if ctx.GlobalInputIndex < first || ctx.GlobalInputIndex > last ||
			(i > 0 && w.Inputs[i-1].Ctx.GlobalInputIndex >= ctx.GlobalInputIndex) ||
			ctx.MomentumHeight == 0 || ctx.MomentumTime == 0 || ctx.MomentumHash.IsZero() ||
			len(ctx.SourceData) != 0 || ctx.Caps != nil {
			return statement.Digest{}, fmt.Errorf("invalid context at proposal input %d", i)
		}
		decoded, err := codec.DecodeInput(input.Payload)
		if err != nil {
			return statement.Digest{}, err
		}
		encoded, err := decoded.Encode()
		if err != nil || !bytes.Equal(encoded, input.Payload) || decoded.SourceAccountBlockHash.IsZero() {
			return statement.Digest{}, fmt.Errorf("noncanonical source at proposal input %d", i)
		}
		call, ok := decoded.Args.(codec.CallL2Args)
		if !ok || call.TargetContract != w.Contract || call.DepositAmount != (codec.U256{}) ||
			call.DepositAsset != (codec.AssetID{}) {
			return statement.Digest{}, fmt.Errorf("proposal input %d must call the authorizer with zero deposit", i)
		}
		end = statement.Link(end, w.Ordinal+uint64(i), statement.Call{
			Index: ctx.GlobalInputIndex, SourceHash: decoded.SourceAccountBlockHash,
			Sender: ctx.L1Sender, MomentumHeight: ctx.MomentumHeight, MomentumTime: ctx.MomentumTime,
			MomentumHash: ctx.MomentumHash, Contract: call.TargetContract, PlasmaLimit: call.PlasmaLimit, Payload: call.Payload,
		})
	}
	return end, nil
}

// replayProposal authenticates the disclosed leaves against the expected
// pre-state root and reuses the executor's full batch driver.
func replayProposal(w proposalWitness, proposed codec.BatchCommitment) (codec.BatchCommitment, error) {
	var empty codec.BatchCommitment
	if len(w.Leaves) == 0 || len(w.Leaves) > 64 || types.NewHash(w.Module).String() != authorizerHash {
		return empty, errors.New("invalid proposal pre-state or authorizer module")
	}
	changes := make([]domain.StateDiffEntry, len(w.Leaves))
	for i, leaf := range w.Leaves {
		if leaf.Value == nil || (i > 0 && bytes.Compare(w.Leaves[i-1].Path[:], leaf.Path[:]) >= 0) {
			return empty, errors.New("expected ordered distinct pre-state leaves")
		}
		changes[i] = domain.StateDiffEntry{Path: trie.Path(leaf.Path), Value: leaf.Value}
	}
	store := state.OpenEmpty()
	root, err := store.Apply(changes)
	if err != nil || root != proposed.PreStateRoot {
		return empty, errors.New("proposal pre-state root differs")
	}

	module := types.NewHash(w.Module)
	plugin, err := wasm.New(wasm.Config{NetworkName: "devnet", FetchModule: func(hash types.Hash) ([]byte, bool, error) {
		return w.Module, hash == module, nil
	}})
	if err != nil {
		return empty, err
	}
	defer plugin.Close()
	stream := &proposalInputs{inputs: w.Inputs}
	driver, err := batch.New(batch.Config{
		DomainID: domainID, ExecutorID: proposed.ExecutorID, FirstBatchID: proposed.BatchID,
		Domain: plugin, Store: store, Inputs: stream, Bundles: &proposalBundle{}, Receipts: proposalReceipts{},
	})
	if err != nil {
		return empty, err
	}
	result, ok, err := driver.NextBatchTo(proposed.LastInputSeq)
	if err != nil || !ok || stream.next != callCount || len(result.Receipts) != callCount {
		return empty, fmt.Errorf("proposal did not re-execute exactly three calls: %v", err)
	}
	for _, receipt := range result.Receipts {
		if receipt.OutboxCount != 0 || receipt.ContractID != w.Contract {
			return empty, errors.New("proposal includes an out-of-profile receipt")
		}
	}
	return result.Commitment, nil
}

func verifyProposal(w proposalWitness) (statement.Digest, statement.Digest, error) {
	if w.Schema != 1 {
		return statement.Digest{}, statement.Digest{}, errors.New("invalid proposal witness schema")
	}
	proposed, err := codec.DecodeBatchCommitment(w.Commitment)
	if err != nil || proposed.DomainID != domainID || proposed.SubmittedAtHeight != 0 {
		return statement.Digest{}, statement.Digest{}, errors.New("invalid proposed commitment")
	}
	end, err := rollInputChain(w, proposed.FirstInputSeq, proposed.LastInputSeq)
	if err != nil {
		return statement.Digest{}, statement.Digest{}, err
	}
	computed, err := replayProposal(w, proposed)
	if err != nil {
		return statement.Digest{}, statement.Digest{}, err
	}
	bytesComputed, err := computed.Encode()
	if err != nil || !bytes.Equal(bytesComputed, w.Commitment) {
		return statement.Digest{}, statement.Digest{}, errors.New("proposed commitment differs from full re-execution")
	}
	return statement.Journal(chainID, w.Ordinal, callCount, w.Start, end, bytesComputed), end, nil
}
