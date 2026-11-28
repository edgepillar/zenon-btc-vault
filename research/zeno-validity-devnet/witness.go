package main

import (
	"bytes"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"slices"

	"github.com/Zeno-L2/zeno-executor/internal/batch"
	"github.com/Zeno-L2/zeno-executor/internal/codec"
	"github.com/Zeno-L2/zeno-executor/internal/ledger"
	"github.com/Zeno-L2/zeno-executor/internal/settlement"
	"github.com/Zeno-L2/zeno-executor/internal/state"
	"github.com/Zeno-L2/zeno-executor/internal/znnd"
	"github.com/Zeno-L2/zeno-executor/plugins/wasm"
	"github.com/zenon-network/go-zenon/common/trie"
	"github.com/zenon-network/go-zenon/common/types"
	"github.com/zenon-network/go-zenon/common/vaultvalidity"
)

const setupBatches = 4

type pendingCapture struct {
	Schema        uint64        `json:"schema"`
	ChainID       uint64        `json:"chainId"`
	Domain        uint32        `json:"domain"`
	Batch         uint64        `json:"batch"`
	Genesis       types.Hash    `json:"genesis"`
	Height        uint64        `json:"height"`
	Hash          types.Hash    `json:"hash"`
	Root          types.Hash    `json:"root"`
	Contract      types.Hash    `json:"contract"`
	ModuleHash    types.Hash    `json:"moduleHash"`
	Submitter     types.Address `json:"submitter"`
	Authorization *types.Hash   `json:"authorization"`
	Sends         []struct {
		Hash          types.Hash `json:"hash"`
		ReceiveHeight uint64     `json:"receiveHeight"`
	} `json:"sends"`
}

type selectedNode struct {
	*znnd.Client
	height uint64
}

func (s selectedNode) FrontierHeight() (uint64, error) { return s.height, nil }

func checkCheckpoint(client *znnd.Client, pending pendingCapture) error {
	genesis, err := client.DetailedMomentumsByHeight(1, 1)
	if err != nil || len(genesis) != 1 || genesis[0].Momentum == nil ||
		genesis[0].Momentum.ChainIdentifier != chainID || genesis[0].Momentum.Hash != pending.Genesis {
		return errors.New("devnet genesis differs")
	}
	checkpoints, err := client.DetailedMomentumsByHeight(pending.Height, 1)
	if err != nil || len(checkpoints) != 1 || checkpoints[0].Momentum == nil {
		return errors.New("pending input checkpoint is unavailable")
	}
	m := checkpoints[0].Momentum
	if m.ChainIdentifier != chainID || m.Version != 3 || m.Height != pending.Height ||
		m.Hash != pending.Hash || m.ComputeHash() != pending.Hash || m.StateRoot != pending.Root {
		return errors.New("pending input checkpoint differs")
	}
	return nil
}

func readInputs(pending pendingCapture, client *znnd.Client, reader *settlement.Reader, view *settlement.View) ([]ledger.Input, error) {
	const inputTotal = setupBatches + callCount
	last, ok, err := view.DomainInputOrdinal(domainID, inputTotal-1)
	if err != nil || !ok {
		return nil, errors.New("last admitted ordinal is unavailable")
	}
	follower, err := ledger.New(ledger.Config{
		DomainID: domainID, StartHeight: 1, ConfirmationDepth: 3, MaxBufferedInputs: 64,
		Source: selectedNode{client, pending.Height}, Assignments: reader,
	})
	if err != nil {
		return nil, err
	}
	follower.LimitReadAhead(last.GlobalInputIndex, true)
	inputs := make([]ledger.Input, 0, inputTotal)
	for i := 0; i < inputTotal; i++ {
		input, ok, err := follower.Next()
		if err != nil || !ok {
			return nil, fmt.Errorf("read accepted input %d: %v", i, err)
		}
		if i >= setupBatches {
			ordinal, ok, err := view.DomainInputOrdinal(domainID, uint64(i))
			if err != nil || !ok {
				return nil, fmt.Errorf("missing admitted ordinal %d", i)
			}
			decoded, err := codec.DecodeInput(input.Payload)
			if err != nil || decoded.SourceAccountBlockHash != ordinal.SourceAccountBlockHash ||
				decoded.SourceAccountBlockHash != pending.Sends[i-setupBatches].Hash || input.Ctx.GlobalInputIndex != ordinal.GlobalInputIndex {
				return nil, fmt.Errorf("selected send differs at ordinal %d", i)
			}
		}
		inputs = append(inputs, input)
	}
	return inputs, nil
}

func replaySetup(work string, view *settlement.View, inputs []ledger.Input, pending pendingCapture) ([]batchLeaf, types.Hash, error) {
	var parent types.Hash
	plugin, err := wasm.New(wasm.Config{NetworkName: "devnet", CodeStorePath: filepath.Join(work, "setup-code")})
	if err != nil {
		return nil, parent, err
	}
	defer plugin.Close()
	stream := &proposalInputs{inputs: inputs}
	store := state.OpenEmpty()
	driver, err := batch.New(batch.Config{
		DomainID: domainID, ExecutorID: pending.Submitter, Domain: plugin, Store: store, Inputs: stream,
		Bundles: &proposalBundle{}, Receipts: proposalReceipts{},
	})
	if err != nil {
		return nil, parent, err
	}
	leaves := make(map[types.Hash][]byte)
	for id := uint64(0); id < setupBatches; id++ {
		entry, ok, err := view.BatchEntry(domainID, id)
		if err != nil || !ok || entry.PreStateRoot != parent {
			return nil, parent, fmt.Errorf("missing or unchained setup batch %d", id)
		}
		result, ok, err := driver.NextBatchTo(entry.LastInputSeq)
		if err != nil || !ok || stream.next != int(id)+1 {
			return nil, parent, fmt.Errorf("replay setup batch %d: %v", id, err)
		}
		if err := settlement.EntryMatches(entry, result.Commitment); err != nil {
			return nil, parent, fmt.Errorf("setup batch %d: %w", id, err)
		}
		records, err := codec.DecodeBundle(result.Bundle)
		if err != nil || len(records) != 1 {
			return nil, parent, fmt.Errorf("decode setup batch %d: %v", id, err)
		}
		effects, err := codec.DecodeExecutionResult(records[0].ExecutionResult)
		if err != nil {
			return nil, parent, err
		}
		for _, change := range effects.StateDiff {
			key := types.Hash(change.Key)
			if change.Value == nil {
				delete(leaves, key)
			} else {
				leaves[key] = bytes.Clone(change.Value)
			}
		}
		parent = entry.PostStateRoot
	}
	ordered := make([]batchLeaf, 0, len(leaves))
	for path, value := range leaves {
		ordered = append(ordered, batchLeaf{path, value})
	}
	slices.SortFunc(ordered, func(a, b batchLeaf) int { return bytes.Compare(a.Path[:], b.Path[:]) })
	if pending.Authorization != nil {
		if err := proveAuthorization(work, pending, store, driver, inputs[len(inputs)-1].Ctx.GlobalInputIndex); err != nil {
			return nil, parent, err
		}
	}
	return ordered, parent, nil
}

func contractPath(contract types.Hash, key []byte) trie.Path {
	return trie.Path(types.NewHash(append(bytes.Clone(contract[:]), key...)))
}

func proveAuthorization(work string, pending pendingCapture, store *state.Store, driver *batch.Driver, last uint64) error {
	slot := contractPath(pending.Contract, []byte{1})
	present, _, emptySlot, err := store.Prove(slot)
	if err != nil || present {
		return errors.New("the prior state must have an unused authorization slot")
	}
	result, ok, err := driver.NextBatchTo(last)
	if err != nil || !ok || len(result.Receipts) != callCount {
		return fmt.Errorf("replay release authorization: %v", err)
	}
	type leaf struct {
		Value []byte `json:"value"`
		Proof []byte `json:"proof"`
	}
	proofs := make([]leaf, 0, 3)
	for _, field := range []struct {
		key  []byte
		want []byte
	}{
		{[]byte{0, 2}, pending.ModuleHash[:]},
		{[]byte{0, 3}, []byte{0}},
		{[]byte{1}, pending.Authorization[:]},
	} {
		present, value, proof, err := store.Prove(contractPath(pending.Contract, field.key))
		if err != nil || !present || !bytes.Equal(value, field.want) {
			return fmt.Errorf("release state leaf %x differs", field.key)
		}
		proofs = append(proofs, leaf{value, proof})
	}
	return writeOutput(work, "authorization.json", struct {
		Contract     types.Hash `json:"contract"`
		PreState     types.Hash `json:"preState"`
		PostState    types.Hash `json:"postState"`
		PriorAbsence []byte     `json:"priorAbsence"`
		Proofs       []leaf     `json:"proofs"`
	}{pending.Contract, result.Commitment.PreStateRoot, result.Commitment.PostStateRoot, emptySlot, proofs})
}

func writeOutput(work, name string, value any) error {
	raw, err := json.Marshal(value)
	if err != nil || len(raw) >= maxWitnessSize {
		return errors.New("bounded capture encoding failed")
	}
	file, err := os.OpenFile(filepath.Join(work, name), os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0o600)
	if err != nil {
		return err
	}
	_, writeErr := file.Write(append(raw, '\n'))
	return errors.Join(writeErr, file.Close())
}

func run(work string) error {
	file, err := os.Open(filepath.Join(work, "pending.json"))
	if err != nil {
		return err
	}
	raw, err := io.ReadAll(io.LimitReader(file, 8193))
	if err := errors.Join(err, file.Close()); err != nil {
		return err
	}
	var pending pendingCapture
	if len(raw) > 8192 || json.Unmarshal(raw, &pending) != nil || pending.Schema != 1 || pending.ChainID != chainID ||
		pending.Domain != domainID || pending.Batch != setupBatches || len(pending.Sends) != callCount ||
		pending.ModuleHash.String() != authorizerHash {
		return errors.New("unexpected pending capture")
	}
	client := znnd.NewClient("http://127.0.0.1:35997")
	if err := checkCheckpoint(client, pending); err != nil {
		return err
	}
	if err := checkCheckpoint(znnd.NewClient("http://127.0.0.1:35991"), pending); err != nil {
		return err
	}
	reader := settlement.NewReader(client)
	view, err := reader.At(pending.Height)
	if err != nil {
		return err
	}
	cursor, err := view.InputSequenceCursor(domainID)
	if err != nil || cursor.NextBatchId != setupBatches || cursor.ConsumedInputCount != setupBatches {
		return errors.New("expected the setup cursor to precede the proposed batch")
	}
	if _, present, err := view.BatchEntry(domainID, pending.Batch); err != nil || present {
		return errors.New("proposed batch is not absent at the selected checkpoint")
	}
	count, err := view.DomainInputCounter(domainID)
	if err != nil || count != setupBatches+callCount {
		return errors.New("expected exactly three outstanding inputs")
	}
	inputs, err := readInputs(pending, client, reader, view)
	if err != nil {
		return err
	}
	leaves, parent, err := replaySetup(work, view, inputs, pending)
	if err != nil {
		return err
	}
	var module []byte
	for _, input := range inputs[:setupBatches] {
		if data := input.Ctx.SourceData; len(data) != 0 && types.NewHash(data) == pending.ModuleHash {
			module = data
			break
		}
	}
	proposedInputs := inputs[setupBatches:]
	commitment := codec.BatchCommitment{
		DomainID: domainID, BatchID: pending.Batch, PreStateRoot: parent, ExecutorID: pending.Submitter,
		FirstInputSeq: proposedInputs[0].Ctx.GlobalInputIndex, LastInputSeq: proposedInputs[callCount-1].Ctx.GlobalInputIndex,
	}
	witness := proposalWitness{
		Schema: 1, Ordinal: cursor.ConsumedInputCount, Start: statement.Seed(chainID, domainID, pending.Batch, cursor.ConsumedInputCount, parent),
		Contract: pending.Contract, Module: module, Leaves: leaves, Inputs: proposedInputs,
	}
	commitment, err = replayProposal(witness, commitment)
	if err != nil {
		return err
	}
	witness.Commitment, err = commitment.Encode()
	if err != nil {
		return err
	}
	journal, end, err := verifyProposal(witness)
	if err != nil {
		return err
	}
	if err := writeOutput(work, "proposal.json", witness); err != nil {
		return err
	}
	return writeOutput(work, "statement.json", struct {
		Journal  string `json:"journal"`
		ChainEnd string `json:"chainEnd"`
	}{hex.EncodeToString(journal[:]), hex.EncodeToString(end[:])})
}

func main() {
	if len(os.Args) != 2 {
		fmt.Fprintln(os.Stderr, "expected the owned devnet work directory")
		os.Exit(2)
	}
	if err := run(os.Args[1]); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
