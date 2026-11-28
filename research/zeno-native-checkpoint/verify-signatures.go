package main

import (
	"crypto/ed25519"
	"crypto/sha3"
	"encoding/binary"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
)

const (
	momentumV3Bytes = 208
	segmentHeaders  = 31
	maxSegmentBytes = 128 * 1024
)

type signedMomentum struct {
	Height       uint64 `json:"height"`
	Hash         string `json:"hash"`
	PreviousHash string `json:"previousHash"`
	StateRoot    string `json:"stateRoot"`
	HashPreimage []byte `json:"hashPreimage"`
	PublicKey    []byte `json:"publicKey"`
	Signature    []byte `json:"signature"`
}

func verifyMomentum(header signedMomentum) error {
	preimage := header.HashPreimage
	if len(preimage) != momentumV3Bytes || len(header.PublicKey) != ed25519.PublicKeySize {
		return errors.New("Momentum preimage or public key length differs")
	}
	// The v3 preimage starts with version and chain ID. The state root
	// follows both Plasma prices at the end of the signed preimage.
	if binary.BigEndian.Uint64(preimage[:8]) != 3 || binary.BigEndian.Uint64(preimage[8:16]) != 69 ||
		binary.BigEndian.Uint64(preimage[48:56]) != header.Height || header.Height < 2 ||
		hex.EncodeToString(preimage[16:48]) != header.PreviousHash ||
		hex.EncodeToString(preimage[176:]) != header.StateRoot {
		return errors.New("Momentum fields differ from the signed preimage")
	}
	digest := sha3.Sum256(preimage)
	if hex.EncodeToString(digest[:]) != header.Hash {
		return errors.New("Momentum hash differs")
	}
	if !ed25519.Verify(header.PublicKey, digest[:], header.Signature) {
		return errors.New("Momentum signature differs")
	}
	return nil
}

func verifySegment(input io.Reader) error {
	data, err := io.ReadAll(io.LimitReader(input, maxSegmentBytes+1))
	if err != nil {
		return err
	}
	if len(data) > maxSegmentBytes {
		return errors.New("Momentum segment exceeds its size limit")
	}
	var segment struct {
		Headers []signedMomentum `json:"headers"`
	}
	if err := json.Unmarshal(data, &segment); err != nil {
		return err
	}
	if len(segment.Headers) != segmentHeaders {
		return errors.New("Momentum segment length differs")
	}
	for index, header := range segment.Headers {
		if err := verifyMomentum(header); err != nil {
			return err
		}
		if index > 0 {
			parent := segment.Headers[index-1]
			if header.Height != parent.Height+1 || header.PreviousHash != parent.Hash {
				return errors.New("Momentum segment is not linked")
			}
		}
	}
	return nil
}

func main() {
	if err := verifySegment(os.Stdin); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
