//go:build devnet

package implementation

import (
	"bytes"
	_ "embed"
	"encoding/binary"
	"errors"
	"fmt"
	"math"

	"github.com/zenon-network/go-zenon/chain/nom"
	"github.com/zenon-network/go-zenon/common/db"
	"github.com/zenon-network/go-zenon/common/types"
	"github.com/zenon-network/go-zenon/common/vaultvalidity"
	"github.com/zenon-network/go-zenon/vm/constants"
	"github.com/zenon-network/go-zenon/vm/embedded/definition"
	"github.com/zenon-network/go-zenon/vm/vm_context"
)

const (
	validityChainID    = 69
	validityDomainID   = 1
	validityStartBatch = 4
	validityCallCount  = 3
)

//go:embed vault-validity-guest-key.txt
var proposedBatchGuestKey string

var validityGuestKey = decodeRecordedValue(proposedBatchGuestKey)

type SettlementValiditySubmitBatchMethod struct{ MethodName string }

type validityBatch struct {
	entry      *definition.BatchEntry
	commitment []byte
	proof      []byte
}

func validityProtected(entry *definition.BatchEntry) bool {
	return entry.DomainId == validityDomainID && entry.BatchId >= validityStartBatch
}

func (p *SettlementValiditySubmitBatchMethod) decode(block *nom.AccountBlock) (validityBatch, error) {
	var result validityBatch
	if block.ChainIdentifier != validityChainID || block.Amount == nil || block.Amount.Sign() != 0 {
		return result, constants.ErrForbiddenParam
	}
	var param SettlementSubmitBatchParam
	if err := definition.ABISettlement.UnpackMethod(&param, p.MethodName, block.Data); err != nil {
		return result, constants.ErrUnpackError
	}
	canonical, err := definition.ABISettlement.PackMethod(p.MethodName, param.Commitment)
	if err != nil || !bytes.Equal(canonical, block.Data) || len(param.Commitment) > constants.MaxOnChainCommitmentSize {
		return result, constants.ErrForbiddenParam
	}
	result.commitment = param.Commitment
	entry, executor, height, err := decodeBatchCommitment(result.commitment)
	if err != nil {
		// Use the existing Phase 1 decoder on the prefix with its empty
		// proof field restored. The trailer is exactly u32(256) || proof.
		end := len(param.Commitment) - recordedProofBytes - 4
		if end < commitmentMinLen || binary.BigEndian.Uint32(param.Commitment[end:]) != recordedProofBytes {
			return validityBatch{}, err
		}
		result.commitment = binary.BigEndian.AppendUint32(bytes.Clone(param.Commitment[:end]), 0)
		result.proof = param.Commitment[end+4:]
		entry, executor, height, err = decodeBatchCommitment(result.commitment)
		if err != nil {
			return validityBatch{}, err
		}
	}
	if executor != block.Address || height != 0 || (!validityProtected(entry) && len(result.proof) != 0) {
		return validityBatch{}, constants.ErrForbiddenParam
	}
	result.entry = entry
	return result, nil
}

// admittedCallSource is the subset of the native momentum store needed to
// reconstruct the source send and its confirmation context.
type admittedCallSource interface {
	GetAccountBlockByHash(types.Hash) (*nom.AccountBlock, error)
	GetBlockConfirmationHeight(types.Hash) (uint64, error)
	GetMomentumByHeight(uint64) (*nom.Momentum, error)
}

func loadAdmittedCall(source admittedCallSource, index *definition.DomainInputOrdinal) (statement.Call, error) {
	var call statement.Call
	send, err := source.GetAccountBlockByHash(index.SourceAccountBlockHash)
	if err != nil {
		return call, err
	}
	if send == nil || send.Hash != index.SourceAccountBlockHash || send.ChainIdentifier != validityChainID ||
		!send.IsSendBlock() || send.ToAddress != types.SettlementContract || send.Amount == nil || send.Amount.Sign() != 0 {
		return call, errors.New("invalid accepted zero-value input source")
	}
	var param SettlementCallL2Param
	if err := definition.ABISettlement.UnpackMethod(&param, definition.SettlementCallL2MethodName, send.Data); err != nil || param.DomainId != index.DomainId {
		return call, errors.New("accepted input is outside the validity call profile")
	}
	height, err := source.GetBlockConfirmationHeight(send.Hash)
	if err != nil || height == 0 {
		return call, errors.New("accepted input has no confirmed source momentum")
	}
	confirmed, err := source.GetMomentumByHeight(height)
	if err != nil || confirmed == nil || confirmed.Height != height || confirmed.ChainIdentifier != validityChainID ||
		confirmed.Hash.IsZero() || confirmed.TimestampUnix == 0 {
		return call, errors.New("accepted input source momentum differs")
	}
	return statement.Call{
		Index: index.GlobalInputIndex, SourceHash: send.Hash, Sender: send.Address,
		MomentumHeight: height, MomentumTime: confirmed.TimestampUnix, MomentumHash: confirmed.Hash,
		Contract: param.TargetContract, PlasmaLimit: param.PlasmaLimit, Payload: param.Payload,
	}, nil
}

func validityAnchorKey(domain uint32) []byte {
	return binary.BigEndian.AppendUint32([]byte{0xf1}, domain)
}

func readValidityStart(storage db.DB, cursor *definition.InputSequenceCursor, root types.Hash) (statement.Digest, error) {
	var start statement.Digest
	data, err := storage.Get(validityAnchorKey(cursor.DomainId))
	if err != nil {
		return start, err
	}
	if cursor.NextBatchId == validityStartBatch && len(data) == 0 {
		return statement.Seed(validityChainID, cursor.DomainId, cursor.NextBatchId, cursor.ConsumedInputCount, root), nil
	}
	if len(data) != 48 || binary.BigEndian.Uint64(data[:8]) != cursor.NextBatchId ||
		binary.BigEndian.Uint64(data[8:16]) != cursor.ConsumedInputCount {
		return start, errors.New("validity input chain anchor differs from the cursor")
	}
	copy(start[:], data[16:])
	return start, nil
}

func deriveValidityJournal(storage db.DB, source admittedCallSource, proposed validityBatch) (types.Hash, []byte, error) {
	var zero types.Hash
	entry := proposed.entry
	cursor, err := definition.GetInputSequenceCursor(storage, entry.DomainId)
	if err != nil {
		return zero, nil, err
	}
	if !validityProtected(entry) || cursor.NextBatchId != entry.BatchId || entry.BatchId == math.MaxUint64 ||
		cursor.ConsumedInputCount > math.MaxUint64-validityCallCount {
		return zero, nil, errors.New("proposal does not extend the validity cursor")
	}
	parent, err := definition.GetBatchEntry(storage, entry.DomainId, entry.BatchId-1)
	if err != nil || parent.PostStateRoot != entry.PreStateRoot {
		return zero, nil, errors.New("proposal pre-state differs from the accepted parent")
	}
	start, err := readValidityStart(storage, cursor, parent.PostStateRoot)
	if err != nil {
		return zero, nil, err
	}
	counter, err := definition.GetDomainInputCounter(storage, entry.DomainId)
	if err != nil || counter.Count < cursor.ConsumedInputCount+validityCallCount {
		return zero, nil, errors.New("proposal range contains inputs Settlement has yet to admit")
	}
	end := start
	var previous uint64
	for i := uint64(0); i < validityCallCount; i++ {
		ordinal := cursor.ConsumedInputCount + i
		index, err := definition.GetDomainInputOrdinal(storage, entry.DomainId, ordinal)
		if err != nil {
			return zero, nil, err
		}
		if (i == 0 && index.GlobalInputIndex != entry.FirstInputSeq) || (i > 0 && index.GlobalInputIndex <= previous) ||
			(i == validityCallCount-1 && index.GlobalInputIndex != entry.LastInputSeq) {
			return zero, nil, errors.New("proposal range differs from the next admitted input ordinals")
		}
		call, err := loadAdmittedCall(source, index)
		if err != nil {
			return zero, nil, fmt.Errorf("input ordinal %d: %w", ordinal, err)
		}
		end = statement.Link(end, ordinal, call)
		previous = index.GlobalInputIndex
	}
	journal := statement.Journal(validityChainID, cursor.ConsumedInputCount, validityCallCount, start, end, proposed.commitment)
	anchor := binary.BigEndian.AppendUint64(nil, cursor.NextBatchId+1)
	anchor = binary.BigEndian.AppendUint64(anchor, cursor.ConsumedInputCount+validityCallCount)
	return journal, append(anchor, end[:]...), nil
}

func (p *SettlementValiditySubmitBatchMethod) GetPlasma(table *constants.PlasmaTable) (uint64, error) {
	return table.EmbeddedSimple, nil
}

func (p *SettlementValiditySubmitBatchMethod) ValidateSendBlock(block *nom.AccountBlock) error {
	_, err := p.decode(block)
	return err
}

func (p *SettlementValiditySubmitBatchMethod) ReceiveBlock(context vm_context.AccountVmContext, block *nom.AccountBlock) ([]*nom.AccountBlock, error) {
	proposed, err := p.decode(block)
	if err != nil {
		return nil, err
	}
	if !validityProtected(proposed.entry) {
		return (&SettlementSubmitBatchMethod{MethodName: p.MethodName}).ReceiveBlock(context, block)
	}
	if len(proposed.proof) == 0 {
		return nil, errors.New("batch validity proof required")
	}
	storage, source := context.Storage(), context.MomentumStore()
	journal, anchor, err := deriveValidityJournal(storage, source, proposed)
	if err != nil {
		return nil, err
	}
	inputs, err := zirenPublicInputs(validityGuestKey, journal)
	if err != nil {
		return nil, err
	}
	if err := verifyZirenProof(proposed.proof, inputs); err != nil {
		return nil, err
	}
	frontier, err := context.GetFrontierMomentum()
	if err != nil {
		return nil, err
	}
	active, err := source.GetActivePillars()
	if err != nil {
		return nil, err
	}
	if err := acceptBatch(storage, frontier.Height, active, block.Address, proposed.entry); err != nil {
		return nil, err
	}
	return nil, storage.Put(validityAnchorKey(proposed.entry.DomainId), anchor)
}
