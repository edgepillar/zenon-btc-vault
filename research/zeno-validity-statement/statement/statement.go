// Package statement defines the devnet validity experiment's byte encoding.
package statement

import (
	"crypto/sha256"
	"encoding/binary"
)

type Digest = [32]byte

// Call is a zero-deposit CallL2 admitted by Settlement. The momentum fields
// identify the momentum that confirmed the source send, as in the executor.
type Call struct {
	Index          uint64
	SourceHash     Digest
	Sender         [20]byte
	MomentumHeight uint64
	MomentumTime   uint64
	MomentumHash   Digest
	Contract       Digest
	PlasmaLimit    uint64
	Payload        []byte
}

// Seed starts the input chain at the experiment's explicit activation boundary.
func Seed(chain uint64, domain uint32, batch, ordinal uint64, preState Digest) Digest {
	data := []byte("zeno-devnet-validity-start-v1\x00")
	data = binary.BigEndian.AppendUint64(data, chain)
	data = binary.BigEndian.AppendUint32(data, domain)
	data = binary.BigEndian.AppendUint64(data, batch)
	data = binary.BigEndian.AppendUint64(data, ordinal)
	data = append(data, preState[:]...)
	return sha256.Sum256(data)
}

// Link extends the input chain with the next admitted call.
func Link(previous Digest, ordinal uint64, call Call) Digest {
	data := []byte("zeno-devnet-validity-call-v1\x00")
	data = append(data, previous[:]...)
	data = binary.BigEndian.AppendUint64(data, ordinal)
	data = binary.BigEndian.AppendUint64(data, call.Index)
	data = append(data, call.SourceHash[:]...)
	data = append(data, call.Sender[:]...)
	data = binary.BigEndian.AppendUint64(data, call.MomentumHeight)
	data = binary.BigEndian.AppendUint64(data, call.MomentumTime)
	data = append(data, call.MomentumHash[:]...)
	data = append(data, call.Contract[:]...)
	data = binary.BigEndian.AppendUint64(data, call.PlasmaLimit)
	data = binary.BigEndian.AppendUint64(data, uint64(len(call.Payload)))
	data = append(data, call.Payload...)
	return sha256.Sum256(data)
}

// Journal binds the cursor and both chain links to the exact canonical batch
// commitment with empty ProofData and zero SubmittedAtHeight.
func Journal(chain, ordinal uint64, count uint32, start, end Digest, commitment []byte) Digest {
	data := []byte("zeno-devnet-validity-proposal-v1\x00")
	data = binary.BigEndian.AppendUint64(data, chain)
	data = binary.BigEndian.AppendUint64(data, ordinal)
	data = binary.BigEndian.AppendUint32(data, count)
	data = append(data, start[:]...)
	data = append(data, end[:]...)
	data = binary.BigEndian.AppendUint64(data, uint64(len(commitment)))
	data = append(data, commitment...)
	return sha256.Sum256(data)
}
