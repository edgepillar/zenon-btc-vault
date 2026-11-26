//go:build devnet

package implementation

import (
	"bytes"
	"crypto/sha256"
	_ "embed"
	"encoding/binary"
	"encoding/hex"
	"errors"
	"strings"

	curve "github.com/consensys/gnark-crypto/ecc/bn254"
	"github.com/consensys/gnark-crypto/ecc/bn254/fr"
	groth16 "github.com/consensys/gnark/backend/groth16/bn254"

	"github.com/zenon-network/go-zenon/chain/nom"
	"github.com/zenon-network/go-zenon/common/types"
	"github.com/zenon-network/go-zenon/vm/constants"
	"github.com/zenon-network/go-zenon/vm/embedded/definition"
	"github.com/zenon-network/go-zenon/vm/vm_context"
)

const (
	recordedJournalHex = "583827c2db6bb87baff06a8492742da5e9c2327f7009b73b4538de2b99a2cfcc"
	recordedCircuitRoot = "007ad39482c4460fe91d19534ecc20e27d374c3e17c0fe5f44d7cf7cb0fd2349"
	recordedProofBytes = 2*curve.SizeOfG1AffineUncompressed + curve.SizeOfG2AffineUncompressed
	recordedCallBytes = 4 + 5*32 + recordedProofBytes
)

//go:embed vault-r4-vk.bin
var recordedCircuitKey []byte

//go:embed vault-r4-guest-key.txt
var recordedGuestKey string

var (
	recordedJournal = types.Hash(decodeRecordedValue(recordedJournalHex))
	recordedVerifier = readRecordedVerifier()
	recordedInputs = readRecordedInputs()
)

func decodeRecordedValue(text string) [32]byte {
	var result [32]byte
	decoded, err := hex.DecodeString(strings.TrimPrefix(strings.TrimSpace(text), "0x"))
	if err != nil || len(decoded) != len(result) {
		panic("invalid embedded recorded-proof value")
	}
	copy(result[:], decoded)
	return result
}

func readRecordedVerifier() *groth16.VerifyingKey {
	want := decodeRecordedValue("b3e14fe98042c3ff6f219172965bddd8dda586c556adac9b4acbd285e13d3cdb")
	if sha256.Sum256(recordedCircuitKey) != want {
		panic("unexpected Ziren Groth16 verifier key")
	}
	key := new(groth16.VerifyingKey)
	reader := bytes.NewReader(recordedCircuitKey)
	if _, err := key.ReadFrom(reader); err != nil || reader.Len() != 0 ||
		len(key.G1.K) != 4 || len(key.PublicAndCommitmentCommitted) != 0 {
		panic("invalid Ziren Groth16 verifier key layout")
	}
	return key
}

func readRecordedInputs() fr.Vector {
	digest := sha256.Sum256(recordedJournal[:])
	// Ziren maps the committed-value digest to 253 bits in BN254.
	digest[0] &= 0x1f
	words := [3][32]byte{decodeRecordedValue(recordedGuestKey), digest, decodeRecordedValue(recordedCircuitRoot)}
	inputs := make(fr.Vector, len(words))
	for i := range words {
		if err := inputs[i].SetBytesCanonical(words[i][:]); err != nil {
			panic("invalid Ziren BN254 public input")
		}
	}
	return inputs
}

type SettlementVerifyRecordedBatchMethod struct{ MethodName string }

type recordedBatchParams struct {
	DomainId uint32
	BatchId  uint64
	Journal  types.Hash
	Proof    []byte
}

func (p *SettlementVerifyRecordedBatchMethod) decode(block *nom.AccountBlock) (*recordedBatchParams, error) {
	if block.ChainIdentifier != 69 || len(block.Data) != recordedCallBytes {
		return nil, constants.ErrForbiddenParam
	}
	if block.Amount == nil || block.Amount.Sign() != 0 {
		return nil, constants.ErrInvalidTokenOrAmount
	}
	params := new(recordedBatchParams)
	if err := definition.ABISettlement.UnpackMethod(params, p.MethodName, block.Data); err != nil {
		return nil, constants.ErrUnpackError
	}
	if params.DomainId != 1 || params.BatchId != 4 || len(params.Proof) != recordedProofBytes {
		return nil, constants.ErrForbiddenParam
	}
	canonical, err := definition.ABISettlement.PackMethod(p.MethodName, params.DomainId, params.BatchId, params.Journal, params.Proof)
	if err != nil || !bytes.Equal(canonical, block.Data) {
		return nil, constants.ErrForbiddenParam
	}
	return params, nil
}

func (p *SettlementVerifyRecordedBatchMethod) GetPlasma(table *constants.PlasmaTable) (uint64, error) {
	return table.EmbeddedWWithdraw, nil
}

func (p *SettlementVerifyRecordedBatchMethod) ValidateSendBlock(block *nom.AccountBlock) error {
	_, err := p.decode(block)
	return err
}

func (p *SettlementVerifyRecordedBatchMethod) ReceiveBlock(context vm_context.AccountVmContext, block *nom.AccountBlock) ([]*nom.AccountBlock, error) {
	params, err := p.decode(block)
	if err != nil {
		return nil, err
	}
	if params.Journal != recordedJournal {
		return nil, errors.New("recorded batch journal differs")
	}
	key := binary.BigEndian.AppendUint64(binary.BigEndian.AppendUint32([]byte{0xf0}, params.DomainId), params.BatchId)
	storage := context.Storage()
	hasReceipt, err := storage.Has(key)
	if err != nil {
		return nil, err
	}
	if hasReceipt {
		return nil, errors.New("recorded batch proof already verified")
	}
	points := curve.NewDecoder(bytes.NewReader(params.Proof))
	var proof groth16.Proof
	if err := points.Decode(&proof.Ar); err != nil {
		return nil, errors.New("invalid recorded Groth16 A point")
	}
	if err := points.Decode(&proof.Bs); err != nil {
		return nil, errors.New("invalid recorded Groth16 B point")
	}
	if err := points.Decode(&proof.Krs); err != nil {
		return nil, errors.New("invalid recorded Groth16 C point")
	}
	if points.BytesRead() != recordedProofBytes || proof.Ar.IsInfinity() || proof.Bs.IsInfinity() || proof.Krs.IsInfinity() {
		return nil, errors.New("invalid recorded Groth16 point encoding")
	}
	if err := groth16.Verify(&proof, recordedVerifier, recordedInputs); err != nil {
		return nil, errors.New("recorded Groth16 verification failed")
	}
	proofDigest := sha256.Sum256(params.Proof)
	value := make([]byte, 0, len(recordedJournal)+len(proofDigest))
	value = append(value, recordedJournal[:]...)
	value = append(value, proofDigest[:]...)
	return nil, storage.Put(key, value)
}
