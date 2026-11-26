// This verifier builds inside a disposable checkout of the pinned executor.
package main

import (
	"bytes"
	"crypto/sha256"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"slices"
	"strings"

	"github.com/zenon-network/go-zenon/common/trie"
	"github.com/zenon-network/go-zenon/common/types"
	"github.com/zenon-network/go-zenon/vm/embedded/definition"

	"github.com/Zeno-L2/zeno-executor/internal/batch"
	"github.com/Zeno-L2/zeno-executor/internal/codec"
	"github.com/Zeno-L2/zeno-executor/internal/ledger"
	"github.com/Zeno-L2/zeno-executor/internal/recovery"
	"github.com/Zeno-L2/zeno-executor/internal/settlement"
	"github.com/Zeno-L2/zeno-executor/internal/state"
	"github.com/Zeno-L2/zeno-executor/internal/znnd"
	"github.com/Zeno-L2/zeno-executor/plugins/wasm"
)

const (
	devnetRPC     = "http://127.0.0.1:35997"
	devnetChainID = 69
	domainID      = 1
	maxBatches    = 16
	maxInputs     = 64
	captureLimit  = 49_152
	authLimit     = 65_536
)

var errCommitmentMismatch = errors.New("re-executed batch differs from Settlement")

type authorizationEvidence struct {
	SchemaVersion int `json:"schemaVersion"`
	Selection struct {
		ContractID types.Hash `json:"contractId"`
		BatchID    uint64     `json:"batchId"`
		BitcoinTarget string  `json:"bitcoinTarget"`
		SourceCodeHash types.Hash `json:"sourceCodeHash"`
		Checkpoint struct {
			ChainID uint64     `json:"chainIdentifier"`
			Version uint64     `json:"version"`
			Height  uint64     `json:"height"`
			Hash    types.Hash `json:"hash"`
			Root    types.Hash `json:"root"`
		} `json:"checkpoint"`
	} `json:"selection"`
	Storage struct {
		Root types.Hash `json:"postStateRoot"`
		Result struct {
			Value types.Hash `json:"value"`
		} `json:"result"`
	} `json:"storage"`
	Sends struct {
		Unauthorized types.Hash `json:"unauthorized"`
		Authorize    types.Hash `json:"authorize"`
		Overwrite    types.Hash `json:"overwrite"`
	} `json:"sends"`
}

type executionCapture struct {
	Schema              int            `json:"schema"`
	AuthorizationSHA256 string         `json:"authorizationSha256"`
	Proofs              []znnd.Proof   `json:"batchProofs"`
	Inputs              []ledger.Input `json:"inputs"`
}

func readBounded(name string, limit int64) ([]byte, error) {
	file, err := os.Open(name)
	if err != nil {
		return nil, errors.New("could not open evidence")
	}
	defer file.Close()
	raw, err := io.ReadAll(io.LimitReader(file, limit+1))
	if err != nil || int64(len(raw)) > limit {
		return nil, errors.New("could not read evidence within its byte limit")
	}
	return raw, nil
}

func digest(raw []byte) string {
	return fmt.Sprintf("%x", sha256.Sum256(raw))
}

func loadAuthorization(name string) (authorizationEvidence, string, error) {
	raw, err := readBounded(name, authLimit)
	if err != nil {
		return authorizationEvidence{}, "", err
	}
	var proof authorizationEvidence
	if err := json.Unmarshal(raw, &proof); err != nil {
		return proof, "", errors.New("invalid contract authorization evidence")
	}
	s := proof.Selection
	if proof.SchemaVersion != 1 || s.BatchID >= maxBatches || s.Checkpoint.ChainID != devnetChainID ||
		s.Checkpoint.Version != 3 || s.Checkpoint.Height == 0 || s.Checkpoint.Hash.IsZero() ||
		s.Checkpoint.Root.IsZero() || proof.Storage.Root.IsZero() || s.ContractID.IsZero() {
		return proof, "", errors.New("invalid devnet authorization selection")
	}
	h := proof.Sends
	if h.Unauthorized.IsZero() || h.Authorize.IsZero() || h.Overwrite.IsZero() ||
		h.Unauthorized == h.Authorize || h.Unauthorized == h.Overwrite || h.Authorize == h.Overwrite {
		return proof, "", errors.New("expected three distinct authorization calls")
	}
	return proof, digest(raw), nil
}

func batchKey(id uint64) []byte {
	key := (&definition.BatchEntryKey{DomainId: domainID, BatchId: id}).Key()
	namespace := append(append([]byte{0x03}, types.SettlementContract.Bytes()...), 0x04)
	return append(namespace, key...)
}

func proveBatches(auth authorizationEvidence, proofs []znnd.Proof) ([]definition.BatchEntry, error) {
	selected := auth.Selection
	if len(proofs) != int(selected.BatchID)+1 {
		return nil, errors.New("expected every batch proof from domain genesis through the selected batch")
	}
	entries := make([]definition.BatchEntry, 0, len(proofs))
	for id, p := range proofs {
		root := selected.Checkpoint.Root
		if p.Root != root || len(p.Value) == 0 {
			return nil, fmt.Errorf("batch %d proof lacks a value under the selected L1 root", id)
		}
		path := trie.Path(types.NewHash(batchKey(uint64(id))))
		if ok, err := trie.VerifyProofByPath(root, path, p.Value, p.Proof); err != nil || !ok {
			return nil, fmt.Errorf("batch %d Settlement proof refused", id)
		}
		var entry definition.BatchEntry
		if err := definition.ABISettlement.UnpackVariable(&entry, definition.BatchEntryVariableName, p.Value); err != nil {
			return nil, fmt.Errorf("batch %d Settlement value does not decode", id)
		}
		if entry.State != 0 || entry.FinalizeAtHeight == 0 || entry.FinalizeAtHeight < entry.SubmittedAtHeight ||
			!entry.IsFinalized(selected.Checkpoint.Height) {
			return nil, fmt.Errorf("batch %d is ineligible at the selected checkpoint", id)
		}
		entries = append(entries, entry)
	}
	if entries[len(entries)-1].PostStateRoot != auth.Storage.Root {
		return nil, errors.New("selected batch root differs from the contract proof")
	}
	return entries, nil
}

type checkpointSource struct {
	*znnd.Client
	height uint64
}

func (s checkpointSource) FrontierHeight() (uint64, error) { return s.height, nil }

func collect(auth authorizationEvidence, sha string) (executionCapture, error) {
	out := executionCapture{Schema: 1, AuthorizationSHA256: sha}
	checkpoint := auth.Selection.Checkpoint
	client := znnd.NewClient(devnetRPC)
	momenta, err := client.DetailedMomentumsByHeight(checkpoint.Height, 1)
	if err != nil || len(momenta) != 1 {
		return out, errors.New("could not read the selected devnet Momentum")
	}
	header := momenta[0].Momentum
	if header.ChainIdentifier != checkpoint.ChainID || header.Version != checkpoint.Version ||
		header.Height != checkpoint.Height || header.Hash != checkpoint.Hash || header.StateRoot != checkpoint.Root ||
		header.ComputeHash() != checkpoint.Hash {
		return out, errors.New("loopback node does not match the selected v3 checkpoint")
	}
	for id := uint64(0); id <= auth.Selection.BatchID; id++ {
		p, err := client.Proof(checkpoint.Height, batchKey(id))
		if err != nil {
			return out, fmt.Errorf("could not read Settlement proof for batch %d: %w", id, err)
		}
		out.Proofs = append(out.Proofs, p)
	}
	entries, err := proveBatches(auth, out.Proofs)
	if err != nil {
		return out, err
	}
	follower, err := ledger.New(ledger.Config{
		DomainID: domainID, StartHeight: 1, ConfirmationDepth: 3, MaxBufferedInputs: maxInputs,
		Source: checkpointSource{Client: client, height: checkpoint.Height}, Assignments: settlement.NewReader(client),
	})
	if err != nil {
		return out, err
	}
	last := entries[len(entries)-1].LastInputSeq
	follower.LimitReadAhead(last, true)
	for len(out.Inputs) < maxInputs {
		input, ok, err := follower.Next()
		if err != nil {
			return out, fmt.Errorf("read assigned devnet input: %w", err)
		}
		if !ok || input.Ctx.GlobalInputIndex > last {
			break
		}
		out.Inputs = append(out.Inputs, input)
		if input.Ctx.GlobalInputIndex == last {
			return out, nil
		}
	}
	return out, errors.New("assigned devnet input stream did not reach the last proven batch index within its limit")
}

type inputStream struct {
	inputs []ledger.Input
	next   int
}

func (s *inputStream) Next() (ledger.Input, bool, error) {
	if s.next == len(s.inputs) {
		return ledger.Input{}, false, nil
	}
	input := s.inputs[s.next]
	s.next++
	return input, true, nil
}

type callExpectation struct {
	hash      types.Hash
	label     string
	status    codec.Status
	abortCode int32
}

func expectedCalls(auth authorizationEvidence) []callExpectation {
	return []callExpectation{
		{auth.Sends.Unauthorized, "non-authorizer", codec.StatusRevert, 1},
		{auth.Sends.Authorize, "authorization", codec.StatusSuccess, 0},
		{auth.Sends.Overwrite, "second write", codec.StatusRevert, 2},
	}
}

type authorizationStatement struct {
	AuthorizationSHA256 string     `json:"authorizationSha256"`
	ExecutionSHA256     string     `json:"executionSha256"`
	Caller              string     `json:"caller"`
	CanonicalInput      string     `json:"canonicalInput"`
	BitcoinTarget       string     `json:"bitcoinTarget"`
	Commitment          types.Hash `json:"commitment"`
	StateDiffHash       types.Hash `json:"stateDiffHash"`
}

func verifyAuthorizationPreconditions(record codec.BundleInputRecord, contract types.Hash) error {
	result, err := codec.DecodeExecutionResult(record.ExecutionResult)
	if err != nil {
		return err
	}
	slot := trie.Path(types.NewHash(append(contract.Bytes(), byte(0x01))))
	quota := trie.Path(types.NewHash(append(contract.Bytes(), 0x00, 0x05)))
	quotaBefore := [16]byte{7: 96}
	var slotAbsent, quotaAvailable bool
	for _, witness := range record.Witnesses {
		switch witness.DerivedKey {
		case slot:
			ok, err := trie.VerifyAbsenceByPath(result.PreStateRoot, slot, witness.Proof)
			slotAbsent = err == nil && ok && witness.Value == nil
		case quota:
			ok, err := trie.VerifyProofByPath(result.PreStateRoot, quota, quotaBefore[:], witness.Proof)
			quotaAvailable = err == nil && ok && bytes.Equal(witness.Value, quotaBefore[:])
		}
	}
	if !slotAbsent || !quotaAvailable {
		return errors.New("authorization pre-state lacks an absent slot and an unused 96-byte quota")
	}
	return nil
}

func verifyExecution(scratch string, auth authorizationEvidence, proved []definition.BatchEntry, inputs []ledger.Input, statement *authorizationStatement, witness *batchWitness) error {
	if len(inputs) == 0 || len(inputs) > maxInputs {
		return errors.New("invalid devnet input count")
	}
	dir, err := os.MkdirTemp(scratch, "execution.")
	if err != nil {
		return errors.New("could not allocate the replay working directory")
	}
	plugin, err := wasm.New(wasm.Config{NetworkName: "devnet", CodeStorePath: filepath.Join(dir, "code")})
	if err != nil {
		return err
	}
	defer plugin.Close()
	if genesis := plugin.Genesis(); len(genesis.Paths) != 0 || len(genesis.Values) != 0 {
		return errors.New("expected an empty WASM domain genesis")
	}
	bundles, err := recovery.NewBundleStore(filepath.Join(dir, "bundles"))
	if err != nil {
		return err
	}
	sidecars, err := recovery.NewReceiptStore(filepath.Join(dir, "receipts"))
	if err != nil {
		return err
	}
	stream := &inputStream{inputs: inputs}
	driver, err := batch.New(batch.Config{
		DomainID: domainID, Domain: plugin, Store: state.OpenEmpty(), Inputs: stream,
		Bundles: bundles, Receipts: sidecars, FirstBatchID: 0,
	})
	if err != nil {
		return err
	}
	wanted := expectedCalls(auth)
	found := make(map[types.Hash]uint64, len(wanted))
	leaves := make(map[types.Hash][]byte)
	for id, entry := range proved {
		replayed, ok, err := driver.NextBatchTo(entry.LastInputSeq)
		if err != nil || !ok {
			return fmt.Errorf("execute batch %d to its proven end: produced=%t, error=%v", id, ok, err)
		}
		if err := settlement.EntryMatches(&entry, replayed.Commitment); err != nil {
			return fmt.Errorf("%w at batch %d: %v", errCommitmentMismatch, id, err)
		}
		records, err := codec.DecodeBundle(replayed.Bundle)
		if err != nil || len(records) != len(replayed.Receipts) {
			return fmt.Errorf("replayed bundle/receipt count differs for batch %d", id)
		}
		if witness != nil {
			if uint64(id) == auth.Selection.BatchID {
				first := stream.next - len(records)
				if first < 0 || len(records) != 3 {
					return errors.New("expected exactly three inputs in the selected batch")
				}
				witness.Inputs = inputs[first:stream.next]
				if err := selectBatchState(witness, leaves, inputs[:first], entry.PreStateRoot); err != nil {
					return err
				}
			} else {
				for _, rec := range records {
					effects, err := codec.DecodeExecutionResult(rec.ExecutionResult)
					if err != nil {
						return err
					}
					for _, change := range effects.StateDiff {
						key := types.Hash(change.Key)
						if change.Value == nil {
							delete(leaves, key)
						} else {
							leaves[key] = bytes.Clone(change.Value)
						}
					}
				}
			}
		}
		for i, r := range replayed.Receipts {
			in, err := codec.DecodeInput(records[i].InputData)
			if err != nil || r.OutboxCount != 0 {
				return fmt.Errorf("batch %d contains an out-of-scope input or outbox message", id)
			}
			for _, w := range wanted {
				if in.SourceAccountBlockHash != w.hash {
					continue
				}
				if _, exists := found[w.hash]; exists || r.Status != w.status || r.AbortCode != w.abortCode ||
					r.ContractID != auth.Selection.ContractID {
					return fmt.Errorf("replayed %s receipt differs from the selected authorization flow", w.label)
				}
				found[w.hash] = uint64(id)
				if statement != nil && w.hash == auth.Sends.Authorize {
					if err := verifyAuthorizationPreconditions(records[i], r.ContractID); err != nil {
						return err
					}
					first := stream.next - len(records)
					if first < 0 {
						return errors.New("replayed statements exceed the consumed input stream")
					}
					source := inputs[first+i]
					if source.Ctx.GlobalInputIndex != r.InputIndex {
						return errors.New("authorization input does not match its replayed index")
					}
					statement.Caller = fmt.Sprintf("%x", source.Ctx.L1Sender.Bytes())
					statement.CanonicalInput = fmt.Sprintf("%x", records[i].InputData)
					statement.StateDiffHash = r.StateDiffHash
				}
			}
		}
	}
	if stream.next != len(inputs) || len(found) != len(wanted) || found[auth.Sends.Overwrite] != auth.Selection.BatchID {
		return errors.New("replay did not consume exactly the selected batch sequence and calls")
	}
	if witness != nil && (found[auth.Sends.Unauthorized] != witness.BatchID || found[auth.Sends.Authorize] != witness.BatchID) {
		return errors.New("the selected batch does not contain all three authorization calls")
	}
	fmt.Printf("PASS: re-executed %d batches and %d L1-derived inputs from empty WASM genesis\n", len(proved), len(inputs))
	fmt.Printf("PASS: matched Settlement pre/post-state, input, receipt, event and outbox roots, DA hash and flow summary\n")
	fmt.Printf("AUTHORIZATION_BATCHES %d %d %d\n", found[auth.Sends.Unauthorized], found[auth.Sends.Authorize], found[auth.Sends.Overwrite])
	return nil
}

func selectBatchState(witness *batchWitness, leaves map[types.Hash][]byte, prefix []ledger.Input, wantRoot types.Hash) error {
	for path, value := range leaves {
		witness.Leaves = append(witness.Leaves, batchLeaf{path, value})
	}
	slices.SortFunc(witness.Leaves, func(a, b batchLeaf) int { return bytes.Compare(a.Path[:], b.Path[:]) })
	paths := make([]trie.Path, len(witness.Leaves))
	values := make([][]byte, len(witness.Leaves))
	for i, leaf := range witness.Leaves {
		paths[i], values[i] = trie.Path(leaf.Path), leaf.Value
	}
	root, err := trie.RootOfLeaves(paths, values)
	if err != nil || root != wantRoot {
		return errors.New("the pre-state snapshot does not reproduce the proven batch root")
	}
	for _, input := range prefix {
		module := input.Ctx.SourceData
		if len(module) > 0 && types.NewHash(module) == witness.ModuleHash {
			witness.Module = module
			return nil
		}
	}
	return errors.New("could not identify the authorizer module in its deployment input")
}

func checkRefusals(scratch string, auth authorizationEvidence, data executionCapture, entries []definition.BatchEntry) error {
	badProofs := append([]znnd.Proof(nil), data.Proofs...)
	badProofs[0].Value = append([]byte(nil), badProofs[0].Value...)
	badProofs[0].Value[0] ^= 1
	if _, err := proveBatches(auth, badProofs); err == nil {
		return errors.New("a changed Settlement proof value was accepted")
	}
	var authorizeIndex, unauthorizedIndex = -1, -1
	for i, input := range data.Inputs {
		payload, err := codec.CanonicalInput(input.Payload)
		if err != nil {
			return err
		}
		decoded, err := codec.DecodeInput(payload)
		if err != nil {
			return err
		}
		switch decoded.SourceAccountBlockHash {
		case auth.Sends.Authorize:
			authorizeIndex = i
		case auth.Sends.Unauthorized:
			unauthorizedIndex = i
		}
	}
	if authorizeIndex < 0 || unauthorizedIndex < 0 {
		return errors.New("missing caller-substitution controls")
	}
	changed := append([]ledger.Input(nil), data.Inputs...)
	changed[authorizeIndex].Ctx.L1Sender = changed[unauthorizedIndex].Ctx.L1Sender
	if err := verifyExecution(scratch, auth, entries, changed, nil, nil); !errors.Is(err, errCommitmentMismatch) {
		return fmt.Errorf("caller substitution did not cause a commitment mismatch: %v", err)
	}
	fmt.Println("PASS: refused a modified Settlement proof value and a substituted authorization sender")
	return nil
}

func writeCapture(name string, data any) error {
	raw, err := json.Marshal(data)
	if err != nil || len(raw)+1 > captureLimit {
		return errors.New("execution capture exceeds its byte limit or could not be encoded")
	}
	file, err := os.OpenFile(name, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0o600)
	if err != nil {
		return errors.New("could not open a new execution capture file")
	}
	if _, err := file.Write(append(raw, '\n')); err != nil {
		file.Close()
		return err
	}
	return file.Close()
}

func run(args []string) error {
	const usage = "use capture <authorization> <execution-output> <scratch>, capture-batch with an additional <batch-output>, or replay/statement <authorization> <execution-capture> <scratch> <sha256>"
	if len(args) < 4 || (args[0] == "capture" && len(args) != 4) ||
		(args[0] != "capture" && (len(args) != 5 || (args[0] != "replay" && args[0] != "statement" && args[0] != "capture-batch"))) {
		return errors.New(usage)
	}
	auth, sha, err := loadAuthorization(args[1])
	if err != nil {
		return err
	}
	var data executionCapture
	if args[0] == "capture" || args[0] == "capture-batch" {
		data, err = collect(auth, sha)
		if err != nil {
			return err
		}
		if err := writeCapture(args[2], data); err != nil {
			return err
		}
	} else {
		raw, err := readBounded(args[2], captureLimit)
		if err != nil {
			return err
		}
		if digest(raw) != strings.TrimSpace(args[4]) {
			return errors.New("execution capture differs from its selected SHA-256")
		}
		if err := json.Unmarshal(raw, &data); err != nil {
			return errors.New("invalid execution capture")
		}
	}
	if data.Schema != 1 || sha != data.AuthorizationSHA256 {
		return errors.New("execution capture does not match the selected authorization evidence")
	}
	entries, err := proveBatches(auth, data.Proofs)
	if err != nil {
		return err
	}
	var statement *authorizationStatement
	var witness *batchWitness
	if args[0] == "statement" {
		statement = &authorizationStatement{
			AuthorizationSHA256: sha, ExecutionSHA256: strings.TrimSpace(args[4]),
			BitcoinTarget: auth.Selection.BitcoinTarget, Commitment: auth.Storage.Result.Value,
		}
	}
	if args[0] == "capture-batch" {
		checkpoint := auth.Selection.Checkpoint
		witness = &batchWitness{
			Schema: 1, BatchID: auth.Selection.BatchID,
			CheckpointHash: checkpoint.Hash, CheckpointHeight: checkpoint.Height,
			SettlementProof: data.Proofs[auth.Selection.BatchID],
			ContractID: auth.Selection.ContractID, ModuleHash: auth.Selection.SourceCodeHash,
		}
	}
	if err := verifyExecution(args[3], auth, entries, data.Inputs, statement, witness); err != nil {
		return err
	}
	if err := checkRefusals(args[3], auth, data, entries); err != nil {
		return err
	}
	if statement != nil {
		if statement.Caller == "" || statement.StateDiffHash.IsZero() || statement.BitcoinTarget == "" || statement.Commitment.IsZero() {
			return errors.New("missing authorization statement fields")
		}
		raw, err := json.Marshal(statement)
		if err != nil {
			return err
		}
		fmt.Printf("AUTHORIZATION_STATEMENT_JSON %s\n", raw)
	}
	if witness != nil {
		if err := writeCapture(args[4], witness); err != nil {
			return err
		}
		fmt.Printf("PASS: captured batch %d with %d inputs and %d authenticated pre-state leaves\n",
			witness.BatchID, len(witness.Inputs), len(witness.Leaves))
	}
	return nil
}

func main() {
	if err := run(os.Args[1:]); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
