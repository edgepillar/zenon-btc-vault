//go:build devnet

package implementation

import (
	"bytes"
	"encoding/binary"
	"encoding/hex"
	"encoding/json"
	"math/big"
	"os"
	"testing"

	"github.com/zenon-network/go-zenon/chain/nom"
	"github.com/zenon-network/go-zenon/chain/store"
	"github.com/zenon-network/go-zenon/common"
	"github.com/zenon-network/go-zenon/common/db"
	"github.com/zenon-network/go-zenon/common/types"
	"github.com/zenon-network/go-zenon/vm/embedded/definition"
	"github.com/zenon-network/go-zenon/vm/vm_context"
)

type validityTestLedger struct {
	store.Momentum
	sends   map[types.Hash]*nom.AccountBlock
	heights map[types.Hash]uint64
	headers map[uint64]*nom.Momentum
}

func (m *validityTestLedger) GetAccountBlockByHash(hash types.Hash) (*nom.AccountBlock, error) {
	return m.sends[hash], nil
}

func (m *validityTestLedger) GetBlockConfirmationHeight(hash types.Hash) (uint64, error) {
	return m.heights[hash], nil
}

func (m *validityTestLedger) GetMomentumByHeight(height uint64) (*nom.Momentum, error) {
	return m.headers[height], nil
}

type validityTestContext struct {
	vm_context.AccountVmContext
	storage db.DB
	ledger  *validityTestLedger
}

func (c validityTestContext) Storage() db.DB { return c.storage }

func (c validityTestContext) MomentumStore() store.Momentum { return c.ledger }

func validityTestProposal(t *testing.T) (validityTestContext, *nom.AccountBlock, types.Hash) {
	t.Helper()
	var vector struct {
		Commitment []byte `json:"commitment"`
		Ordinal    uint64 `json:"firstOrdinal"`
		Inputs     []struct {
			Payload []byte
			Ctx     struct {
				GlobalInputIndex uint64
				L1Sender         types.Address
				MomentumHeight   uint64
				MomentumTime     uint64
				MomentumHash     types.Hash
			}
		} `json:"inputs"`
	}
	file := os.Getenv("VAULT_PROPOSAL_WITNESS")
	if file == "" {
		t.Skip("supply the checked proposed-batch vector")
	}
	raw, err := os.ReadFile(file)
	common.FailIfErr(t, err)
	common.FailIfErr(t, json.Unmarshal(raw, &vector))
	entry, executor, _, err := decodeBatchCommitment(vector.Commitment)
	common.FailIfErr(t, err)
	if entry.DomainId != validityDomainID || entry.BatchId != validityStartBatch || len(vector.Inputs) != validityCallCount {
		t.Fatal("the vector does not describe the first validity batch")
	}
	storage := batchStorage(t)
	common.FailIfErr(t, (&definition.InputSequenceCursor{
		DomainId: entry.DomainId, NextBatchId: entry.BatchId, ConsumedInputCount: vector.Ordinal,
		RecordsSweptThrough: entry.BatchId - 1, SweptInputCount: vector.Ordinal,
	}).Save(storage))
	common.FailIfErr(t, (&definition.BatchEntry{
		BatchEntryKey: definition.BatchEntryKey{DomainId: entry.DomainId, BatchId: entry.BatchId - 1},
		PostStateRoot: entry.PreStateRoot, AssetFlowSummary: make([]byte, 4), FinalizeAtHeight: 999,
	}).Save(storage))
	common.FailIfErr(t, (&definition.DomainInputCounter{
		DomainId: entry.DomainId, Count: vector.Ordinal + validityCallCount,
	}).Save(storage))
	ledger := &validityTestLedger{
		sends: make(map[types.Hash]*nom.AccountBlock), heights: make(map[types.Hash]uint64), headers: make(map[uint64]*nom.Momentum),
	}
	for i, input := range vector.Inputs {
		wire, ctx := input.Payload, input.Ctx
		if len(wire) < 141 || wire[0] != 0 || int(binary.BigEndian.Uint32(wire[137:141])) != len(wire)-141 {
			t.Fatal("invalid conformance input bytes")
		}
		hash, target := types.Hash(wire[1:33]), types.Hash(wire[33:65])
		call, err := definition.ABISettlement.PackMethod(definition.SettlementCallL2MethodName,
			entry.DomainId, target, binary.BigEndian.Uint64(wire[65:73]), wire[141:])
		common.FailIfErr(t, err)
		ledger.sends[hash] = &nom.AccountBlock{
			ChainIdentifier: validityChainID, BlockType: nom.BlockTypeUserSend, Hash: hash,
			Address: ctx.L1Sender, ToAddress: types.SettlementContract, Data: call, Amount: new(big.Int),
		}
		ledger.heights[hash] = ctx.MomentumHeight
		ledger.headers[ctx.MomentumHeight] = &nom.Momentum{
			ChainIdentifier: validityChainID, Height: ctx.MomentumHeight, TimestampUnix: ctx.MomentumTime, Hash: ctx.MomentumHash,
		}
		common.FailIfErr(t, (&definition.DomainInputOrdinal{
			DomainId: entry.DomainId, Ordinal: vector.Ordinal + uint64(i),
			GlobalInputIndex: ctx.GlobalInputIndex, SourceAccountBlockHash: hash,
		}).Save(storage))
		common.FailIfErr(t, (&definition.AssignedIndexRecord{
			AssignedIndexRecordKey: definition.AssignedIndexRecordKey{DomainId: entry.DomainId, GlobalInputIndex: ctx.GlobalInputIndex},
			Amount: new(big.Int),
		}).Save(storage))
	}
	call, err := definition.ABISettlement.PackMethod(definition.SettlementSubmitBatchMethodName, vector.Commitment)
	common.FailIfErr(t, err)
	block := &nom.AccountBlock{
		ChainIdentifier: validityChainID, Address: executor, Amount: new(big.Int), Data: call,
		BlockType: nom.BlockTypeUserSend, ToAddress: types.SettlementContract,
	}
	journal := decodeRecordedValue(os.Getenv("VAULT_PROPOSAL_JOURNAL"))
	return validityTestContext{storage: storage, ledger: ledger}, block, journal
}

func TestVaultValidityDerivesJournalFromChainRecords(t *testing.T) {
	ctx, block, expected := validityTestProposal(t)
	method := &SettlementValiditySubmitBatchMethod{MethodName: definition.SettlementSubmitBatchMethodName}
	params, err := method.decode(block)
	common.FailIfErr(t, err)
	journal, anchor, err := deriveValidityJournal(ctx.storage, ctx.ledger, params)
	common.FailIfErr(t, err)
	if journal != expected || len(anchor) != 48 || binary.BigEndian.Uint64(anchor[:8]) != params.entry.BatchId+1 ||
		hex.EncodeToString(anchor[16:]) != os.Getenv("VAULT_PROPOSAL_CHAIN_END") {
		t.Fatal("node-generated journal or successor anchor differs from the guest vector")
	}

	// The send and proposal remain exact while the confirmation context changes.
	first, err := definition.GetDomainInputOrdinal(ctx.storage, params.entry.DomainId, 4)
	common.FailIfErr(t, err)
	confirmed := ctx.ledger.headers[ctx.ledger.heights[first.SourceAccountBlockHash]]
	confirmed.TimestampUnix++
	changed, _, err := deriveValidityJournal(ctx.storage, ctx.ledger, params)
	common.FailIfErr(t, err)
	if changed == journal {
		t.Fatal("node omitted the confirmed source timestamp from the statement")
	}
	delete(ctx.ledger.heights, first.SourceAccountBlockHash)
	if _, _, err := deriveValidityJournal(ctx.storage, ctx.ledger, params); err == nil {
		t.Fatal("proposal without a confirmed source hash produced a journal")
	}
}

func TestVaultValidityRefusesMissingAndHistoricalProofs(t *testing.T) {
	ctx, block, _ := validityTestProposal(t)
	method := &SettlementValiditySubmitBatchMethod{MethodName: definition.SettlementSubmitBatchMethodName}
	params, err := method.decode(block)
	common.FailIfErr(t, err)
	cursorBefore := cursorOf(t, ctx.storage, params.entry.DomainId)
	if _, err := method.ReceiveBlock(ctx, block); err == nil {
		t.Fatal("proofless SubmitBatch passed the validity gate")
	}

	var historical struct {
		Proof   string
		Journal string
	}
	raw, err := os.ReadFile(os.Getenv("VAULT_RECORDED_GROTH16"))
	common.FailIfErr(t, err)
	common.FailIfErr(t, json.Unmarshal(raw, &historical))
	proof, err := hex.DecodeString(historical.Proof)
	common.FailIfErr(t, err)
	if decodeRecordedValue(historical.Journal) != recordedJournal {
		t.Fatal("historical proof fixture differs")
	}
	common.FailIfErr(t, verifyZirenProof(proof, recordedInputs))
	commitment := binary.BigEndian.AppendUint32(bytes.Clone(params.commitment[:len(params.commitment)-4]), uint32(len(proof)))
	block.Data, err = definition.ABISettlement.PackMethod(method.MethodName, append(commitment, proof...))
	common.FailIfErr(t, err)
	common.FailIfErr(t, method.ValidateSendBlock(block))
	if _, err := method.ReceiveBlock(ctx, block); err == nil {
		t.Fatal("historical proof passed under the proposed batch statement")
	}
	if cursorOf(t, ctx.storage, params.entry.DomainId) != cursorBefore {
		t.Fatal("a failed proof advanced the Settlement cursor")
	}
	if _, err := definition.GetBatchEntry(ctx.storage, params.entry.DomainId, params.entry.BatchId); err == nil {
		t.Fatal("a failed proof created a Settlement batch")
	}
	anchor, err := ctx.storage.Get(validityAnchorKey(params.entry.DomainId))
	common.FailIfErr(t, err)
	if len(anchor) != 0 {
		t.Fatal("a failed proof advanced the input hash chain")
	}
}
