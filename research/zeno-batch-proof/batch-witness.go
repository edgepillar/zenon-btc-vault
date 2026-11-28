package main

import (
	"github.com/Zeno-L2/zeno-executor/internal/ledger"
	"github.com/Zeno-L2/zeno-executor/internal/znnd"
	"github.com/zenon-network/go-zenon/common/types"
)

type batchLeaf struct {
	Path  types.Hash `json:"path"`
	Value []byte     `json:"value"`
}

type batchWitness struct {
	Schema           int            `json:"schema"`
	BatchID          uint64         `json:"batchId"`
	CheckpointHash   types.Hash     `json:"checkpointHash"`
	CheckpointHeight uint64         `json:"checkpointHeight"`
	SettlementProof  znnd.Proof     `json:"settlementProof"`
	ContractID       types.Hash     `json:"contractId"`
	ModuleHash       types.Hash     `json:"moduleHash"`
	Module           []byte         `json:"module"`
	Leaves           []batchLeaf    `json:"leaves"`
	Inputs           []ledger.Input `json:"inputs"`
}
