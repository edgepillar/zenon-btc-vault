//go:build devnet

package api

import (
	"errors"

	"github.com/zenon-network/go-zenon/chain/nom"
	"github.com/zenon-network/go-zenon/common/types"
	"github.com/zenon-network/go-zenon/verifier"
)

const (
	vaultDevnetChainID      = 69
	vaultRequiredSuccessors = 30
	vaultMaximumReplayLag   = 60
)

type vaultHeader struct {
	Height       uint64        `json:"height"`
	Hash         types.Hash    `json:"hash"`
	PreviousHash types.Hash    `json:"previousHash"`
	StateRoot    types.Hash    `json:"stateRoot"`
	Producer     types.Address `json:"producer"`
}

type vaultCheckpointView struct {
	ChainID        uint64        `json:"chainId"`
	Genesis        types.Hash    `json:"genesis"`
	FrontierHeight uint64        `json:"frontierHeight"`
	FrontierHash   types.Hash    `json:"frontierHash"`
	Headers        []vaultHeader `json:"headers"`
}

// GetVaultCheckpoint reports a bounded segment of the local canonical devnet.
// The side-branch insertion limit is 30. Replacing the selected momentum would
// require rewinding to its parent, 31 or more steps behind this frontier.
func (l *LedgerApi) GetVaultCheckpoint(height uint64) (*vaultCheckpointView, error) {
	if l.chain.ChainIdentifier() != vaultDevnetChainID || height < 2 {
		return nil, errors.New("vault checkpoint requires the pinned devnet profile")
	}
	insert := l.chain.AcquireInsert("check devnet vault checkpoint")
	defer insert.Unlock()

	store := l.chain.GetFrontierMomentumStore()
	frontier, err := store.GetFrontierMomentum()
	if err != nil || frontier == nil {
		return nil, errors.New("vault checkpoint frontier is unavailable")
	}
	if frontier.Height < height || frontier.Height-height < vaultRequiredSuccessors {
		return nil, errors.New("vault checkpoint needs 30 canonical successors")
	}
	if frontier.Height-height > vaultMaximumReplayLag {
		return nil, errors.New("vault checkpoint is outside the devnet replay window")
	}
	parent, err := store.GetMomentumByHeight(height - 1)
	if err != nil || parent == nil {
		return nil, errors.New("vault checkpoint parent is unavailable")
	}

	view := &vaultCheckpointView{
		ChainID:        frontier.ChainIdentifier,
		Genesis:        l.chain.GetGenesisMomentum().Hash,
		FrontierHeight: frontier.Height,
		FrontierHash:   frontier.Hash,
		Headers:        make([]vaultHeader, 0, vaultRequiredSuccessors+1),
	}
	for offset := uint64(0); offset <= vaultRequiredSuccessors; offset++ {
		m, err := store.GetMomentumByHeight(height + offset)
		if err != nil || m == nil || m.Previous() != parent.Identifier() {
			return nil, errors.New("vault checkpoint segment is unavailable")
		}
		if m.Version != nom.StateRootMomentumVersion || m.ChainIdentifier != vaultDevnetChainID || m.StateRoot.IsZero() {
			return nil, errors.New("vault checkpoint requires v3 state roots")
		}
		header := *m
		header.EnsureCache()
		if err := verifier.MomentumStatic(&header); err != nil {
			return nil, err
		}
		elected, err := l.z.Consensus().VerifyMomentumProducerAt(parent.Identifier(), &header)
		if err != nil || !elected {
			return nil, errors.New("vault checkpoint producer is not authorized by the parent state")
		}
		view.Headers = append(view.Headers, vaultHeader{
			Height: header.Height, Hash: header.Hash, PreviousHash: header.PreviousHash,
			StateRoot: header.StateRoot, Producer: header.Producer(),
		})
		parent = m
	}
	return view, nil
}
