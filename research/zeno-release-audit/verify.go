package main

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math/big"
	"os"

	curve "github.com/consensys/gnark-crypto/ecc/bn254"
	"github.com/consensys/gnark-crypto/ecc/bn254/fr"
	groth16 "github.com/consensys/gnark/backend/groth16/bn254"
)

const (
	keySHA       = "b3e14fe98042c3ff6f219172965bddd8dda586c556adac9b4acbd285e13d3cdb"
	guestKey     = "0x00e4e8f52c5c085d833abcfacbbf0270e9832cd2af090ae12f7659c08270ffa4"
	circuitRoot  = "007ad39482c4460fe91d19534ecc20e27d374c3e17c0fe5f44d7cf7cb0fd2349"
	keySize      = 428
	proofSize    = 2*curve.SizeOfG1AffineUncompressed + curve.SizeOfG2AffineUncompressed
	fixtureLimit = 8192
)

type fixture struct {
	Schema                string   `json:"schema"`
	Version               string   `json:"version"`
	VerificationKeySHA256 string   `json:"verificationKeySha256"`
	GuestKey              string   `json:"guestKey"`
	Journal               string   `json:"journal"`
	Proof                 string   `json:"proof"`
	PublicInputs          []string `json:"publicInputs"`
}

func readKey(path string) (*groth16.VerifyingKey, error) {
	file, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer file.Close()
	raw, err := io.ReadAll(io.LimitReader(file, keySize+1))
	if err != nil || len(raw) != keySize || fmt.Sprintf("%x", sha256.Sum256(raw)) != keySHA {
		return nil, errors.New("unexpected Ziren verification key")
	}
	key := new(groth16.VerifyingKey)
	reader := bytes.NewReader(raw)
	if _, err := key.ReadFrom(reader); err != nil || reader.Len() != 0 ||
		len(key.G1.K) != 4 || len(key.PublicAndCommitmentCommitted) != 0 {
		return nil, errors.New("unexpected verification key layout")
	}
	return key, nil
}

func verify(path, journal string, capture io.Reader) error {
	raw, err := io.ReadAll(io.LimitReader(capture, fixtureLimit+1))
	if err != nil || len(raw) > fixtureLimit {
		return errors.New("unexpected Groth16 fixture size")
	}
	var statement fixture
	if err := json.Unmarshal(raw, &statement); err != nil {
		return err
	}
	if statement.Schema != "zeno-settlement-r4-groth16-v1" || statement.Version != "v2.0.0" ||
		statement.VerificationKeySHA256 != keySHA || statement.GuestKey != guestKey || statement.Journal != journal {
		return errors.New("fixture differs from the independently derived statement")
	}
	journalBytes, err := hex.DecodeString(journal)
	if err != nil || len(journalBytes) != 32 || hex.EncodeToString(journalBytes) != journal {
		return errors.New("invalid journal encoding")
	}
	digest := sha256.Sum256(journalBytes)
	digest[0] &= 0x1f
	guest, err := hex.DecodeString(guestKey[2:])
	if err != nil {
		return err
	}
	root, err := hex.DecodeString(circuitRoot)
	if err != nil {
		return err
	}
	words := [][]byte{guest, digest[:], root}
	inputs := make(fr.Vector, len(words))
	if len(statement.PublicInputs) != len(inputs) {
		return errors.New("unexpected number of public inputs")
	}
	for i, word := range words {
		if err := inputs[i].SetBytesCanonical(word); err != nil {
			return err
		}
		if statement.PublicInputs[i] != new(big.Int).SetBytes(word).String() {
			return errors.New("fixture public input differs from the reconstructed value")
		}
	}

	proofBytes, err := hex.DecodeString(statement.Proof)
	if err != nil || len(proofBytes) != proofSize || hex.EncodeToString(proofBytes) != statement.Proof {
		return errors.New("unexpected Groth16 proof encoding")
	}
	decoder := curve.NewDecoder(bytes.NewReader(proofBytes))
	var proof groth16.Proof
	for _, point := range []any{&proof.Ar, &proof.Bs, &proof.Krs} {
		if err := decoder.Decode(point); err != nil {
			return err
		}
	}
	if decoder.BytesRead() != proofSize || proof.Ar.IsInfinity() || proof.Bs.IsInfinity() || proof.Krs.IsInfinity() {
		return errors.New("unexpected Groth16 proof point")
	}
	key, err := readKey(path)
	if err != nil {
		return err
	}
	return groth16.Verify(&proof, key, inputs)
}

func main() {
	if len(os.Args) != 3 {
		fmt.Fprintln(os.Stderr, "usage: verify-groth16 KEY JOURNAL < FIXTURE")
		os.Exit(2)
	}
	if err := verify(os.Args[1], os.Args[2], os.Stdin); err != nil {
		fmt.Fprintf(os.Stderr, "Groth16 verification failed: %v\n", err)
		os.Exit(1)
	}
}
