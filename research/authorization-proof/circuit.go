package main

import (
	"bytes"
	"crypto/sha3"
	"encoding/hex"

	"github.com/consensys/gnark/frontend"
	"github.com/consensys/gnark/std/math/uints"
	"github.com/consensys/gnark/std/permutation/keccakf"
)

const (
	authorizationTag = "zenon-btc-vault/contract-authorization\x00\x01"
	statementTag     = "zenon-btc-vault/groth16-authorization\x00\x01"
	bitcoinTargetLen = 100
	maxBitcoinSats   = 21_000_000 * 100_000_000
)

var (
	contractID = fixtureHex("4dbe92ecf9f3a3a71d87f561a9f11cfe6b6ba0467d8103d004af0d258150a0e4")
	custodian  = fixtureHex("0073959db8921fbd1b5b8ad158f9d3c555deaa79")
	regtest    = fixtureHex("0f9188f13cb7b2c71f2a335e3a4fc328bf5beb436012afca590b1a11466e2206")
	source     = fixtureHex("000000000000004500000001") // chain 69, domain 1
	quotaAfter = fixtureHex("00000000000000600000000000000060")
	callHeader = append(append(fixtureHex("0000000000989680"), make([]byte, 64)...), fixtureHex("00000024c3ab03af")...)
	slotPath   = sha3.Sum256(bytes.Join([][]byte{contractID, {0x01}}, nil))
	quotaPath  = sha3.Sum256(bytes.Join([][]byte{contractID, {0x00, 0x05}}, nil))
)

func fixtureHex(s string) []byte {
	raw, err := hex.DecodeString(s)
	if err != nil {
		panic("invalid fixed fixture constant")
	}
	return raw
}

type authorizationCircuit struct {
	Statement [32]frontend.Variable `gnark:",public"`
	Context   [32]frontend.Variable
	Caller    [20]frontend.Variable
	SendHash  [32]frontend.Variable
	Target    [bitcoinTargetLen]frontend.Variable
}

// constrainBytes uses bit decomposition for width checks. All packed bytes
// feed the upstream Keccak-f circuit without byte lookup commitments.
func constrainBytes(api frontend.API, b *uints.Bytes, src []frontend.Variable) []uints.U8 {
	result := make([]uints.U8, len(src))
	for i, v := range src {
		result[i] = b.FromBits(api.ToBinary(v, 8)...)
	}
	return result
}

// fixedSHA3 uses fixed-length FIPS 202 padding and bitwise absorption with
// gnark's Keccak-f permutation. It keeps the relation in ordinary R1CS.
func fixedSHA3(api frontend.API, b *uints.Bytes, u *uints.BinaryField[uints.U64], parts ...[]uints.U8) []uints.U8 {
	const rate = 136
	var message []uints.U8
	for _, part := range parts {
		message = append(message, part...)
	}
	padding := make([]byte, rate-len(message)%rate)
	padding[0] = 0x06
	padding[len(padding)-1] |= 0x80
	message = append(message, uints.NewU8Array(padding)...)

	var state [25]uints.U64
	for lane := range state {
		state[lane] = uints.NewU64(0)
	}
	for offset := 0; offset < len(message); offset += rate {
		for lane := 0; lane < rate/8; lane++ {
			bits := u.ToBits(state[lane])
			for j := 0; j < 8; j++ {
				in := b.ToBits(message[offset+lane*8+j])
				for k := range in {
					bits[j*8+k] = api.Xor(bits[j*8+k], in[k])
				}
			}
			state[lane] = u.FromBits(bits...)
		}
		state = keccakf.Permute(u, state)
	}
	out := make([]uints.U8, 0, 32)
	for _, lane := range state[:4] {
		out = append(out, u.UnpackLSB(lane)...)
	}
	return out
}

func (c *authorizationCircuit) Define(api frontend.API) error {
	b, err := uints.NewBytes(api)
	if err != nil {
		return err
	}
	u, err := uints.New[uints.U64](api)
	if err != nil {
		return err
	}
	pub := constrainBytes(api, b, c.Statement[:])
	context := constrainBytes(api, b, c.Context[:])
	caller := constrainBytes(api, b, c.Caller[:])
	send := constrainBytes(api, b, c.SendHash[:])
	target := constrainBytes(api, b, c.Target[:])
	constant := uints.NewU8Array
	hash := func(parts ...[]uints.U8) []uints.U8 { return fixedSHA3(api, b, u, parts...) }

	for i, x := range custodian {
		b.AssertIsEqual(caller[i], uints.NewU8(x))
	}
	for i, x := range regtest {
		b.AssertIsEqual(target[i], uints.NewU8(x))
	}
	for i, x := range []byte{0x00, 0x16, 0x00, 0x14} {
		b.AssertIsEqual(target[76+i], uints.NewU8(x))
	}
	var amount frontend.Variable = 0
	for _, satByte := range target[68:76] {
		amount = api.Add(api.Mul(amount, 256), b.Value(satByte))
	}
	api.AssertIsDifferent(amount, 0)
	api.AssertIsLessOrEqual(amount, maxBitcoinSats)

	moduleContract := constant(contractID)
	commitment := hash(constant([]byte(authorizationTag)), constant(source), moduleContract, target)
	inputHash := hash(constant([]byte{0}), send, moduleContract, constant(callHeader), commitment)
	slotWrite := append(constant(slotPath[:]), constant([]byte{0, 0, 0, 32})...)
	slotWrite = append(slotWrite, commitment...)
	quotaWrite := constant(bytes.Join([][]byte{quotaPath[:], {0, 0, 0, 16}, quotaAfter}, nil))
	first, second := slotWrite, quotaWrite
	if bytes.Compare(slotPath[:], quotaPath[:]) > 0 {
		first, second = second, first
	}
	diffHash := hash(constant([]byte{0, 0, 0, 2}), first, second)
	out := hash(constant([]byte(statementTag)), context, moduleContract, caller, send, commitment, inputHash, diffHash)
	for i := range out {
		b.AssertIsEqual(pub[i], out[i])
	}
	return nil
}
