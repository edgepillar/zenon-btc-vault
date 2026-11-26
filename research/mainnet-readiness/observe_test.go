package main

import (
	"bytes"
	"crypto/ed25519"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"os"
	"path/filepath"
	"testing"

	"github.com/0x3639/zenon-spv/internal/chain"
	"github.com/0x3639/zenon-spv/internal/verify"
)

func signed(h chain.Header) chain.Header {
	key := ed25519.NewKeyFromSeed(make([]byte, ed25519.SeedSize))
	h.PublicKey = key.Public().(ed25519.PublicKey)
	h.HeaderHash = h.ComputeHash()
	h.Signature = ed25519.Sign(key, h.HeaderHash[:])
	return h
}

func fixture() (capture, verify.GenesisTrustRoot) {
	genesis := chain.Header{Version: 1, ChainIdentifier: 1, Height: 1}
	genesis.HeaderHash = genesis.ComputeHash()
	anchor := verify.GenesisTrustRoot{ChainID: 1, Height: 1, HeaderHash: genesis.HeaderHash}
	first := signed(chain.Header{Version: 1, ChainIdentifier: 1, Height: 10, TimestampUnix: 100})
	next := signed(chain.Header{Version: 1, ChainIdentifier: 1, Height: 11, TimestampUnix: 110, PreviousHash: first.HeaderHash})
	data := capture{Schema: 1, ObservedAt: 120}
	for _, endpoint := range endpoints {
		tip := next
		data.Peers = append(data.Peers, observation{
			Endpoint: endpoint, Genesis: &genesis, Frontier: &tip, Sample: []chain.Header{first, next},
		})
	}
	return data, anchor
}

func TestAssess(t *testing.T) {
	cases := []struct {
		name   string
		change func(*capture)
		want   string
	}{
		{"matching v1 headers", func(*capture) {}, "LINKED_HEADERS_AGREE"},
		{"matching v2 headers", func(data *capture) {
			for i := range data.Peers {
				p := &data.Peers[i]
				p.Sample[0].Version = 2
				p.Sample[0].NextFusionPrice, p.Sample[0].NextWorkPrice = 100, 100
				p.Sample[0] = signed(p.Sample[0])
				p.Sample[1].Version = 2
				p.Sample[1].NextFusionPrice, p.Sample[1].NextWorkPrice = 100, 100
				p.Sample[1].PreviousHash = p.Sample[0].HeaderHash
				p.Sample[1] = signed(p.Sample[1])
				p.Frontier = &p.Sample[1]
			}
		}, "LINKED_HEADERS_AGREE"},
		{"duplicate endpoint", func(data *capture) { data.Peers[1].Endpoint = data.Peers[0].Endpoint }, "INVALID_CAPTURE"},
		{"unavailable read", func(data *capture) { data.Peers[1].Failure = "SAMPLE_TIMEOUT" }, "INCOMPLETE"},
		{"different genesis", func(data *capture) { data.Peers[1].Genesis = &chain.Header{} }, "GENESIS_MISMATCH"},
		{"unknown frontier layout", func(data *capture) {
			data.Peers[0].Frontier.Version = 3
			*data.Peers[0].Frontier = signed(*data.Peers[0].Frontier)
		}, "INVALID_FRONTIER"},
		{"short sample", func(data *capture) { data.Peers[1].Sample = data.Peers[1].Sample[:1] }, "INVALID_SAMPLE_HEIGHT"},
		{"changed header hash", func(data *capture) { data.Peers[1].Sample[0].TimestampUnix -= 1 }, "INVALID_SAMPLE"},
		{"invalid signature", func(data *capture) {
			data.Peers[1].Sample[0].Signature = make([]byte, ed25519.SignatureSize)
		}, "INVALID_SAMPLE"},
		{"valid signatures with broken linkage", func(data *capture) {
			data.Peers[1].Sample[1].PreviousHash = chain.Hash{}
			data.Peers[1].Sample[1] = signed(data.Peers[1].Sample[1])
		}, "INVALID_SAMPLE"},
		{"different sample at observed frontier", func(data *capture) {
			data.Peers[1].Sample[1].TimestampUnix += 1
			data.Peers[1].Sample[1] = signed(data.Peers[1].Sample[1])
		}, "SAMPLE_FRONTIER_MISMATCH"},
		{"two internally valid histories", func(data *capture) {
			data.Peers[1].Sample[1].TimestampUnix += 1
			data.Peers[1].Sample[1] = signed(data.Peers[1].Sample[1])
			data.Peers[1].Frontier = &data.Peers[1].Sample[1]
		}, "PEERS_DISAGREE"},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			data, anchor := fixture()
			c.change(&data)
			got := assess(data, anchor)
			if got.Observation != c.want {
				t.Fatalf("observation = %s, want %s", got.Observation, c.want)
			}
			if got.BitcoinRelease != "NO_GO" || got.ConsensusFinality != "NOT_ESTABLISHED" ||
				got.V3Activation != "NOT_ESTABLISHED" || got.L2Execution != "NOT_ESTABLISHED" {
				t.Fatal("observations must preserve the release refusal")
			}
			if got.Observation == "LINKED_HEADERS_AGREE" && (got.SampleHeight != 10 || got.StateRoot != "ABSENT_FROM_SAMPLED_LAYOUTS") {
				t.Fatalf("unexpected matching sample: %+v", got)
			}
		})
	}
}

func TestReplayRequiresTheSelectedBytes(t *testing.T) {
	data, _ := fixture()
	raw, err := json.Marshal(data)
	if err != nil {
		t.Fatal(err)
	}
	file := filepath.Join(t.TempDir(), "capture.json")
	if err := os.WriteFile(file, raw, 0o600); err != nil {
		t.Fatal(err)
	}
	sum := sha256.Sum256(raw)
	digest := hex.EncodeToString(sum[:])
	if got, err := readCapture(file, digest); err != nil || !sameHeader(*got.Peers[0].Frontier, *data.Peers[0].Frontier) {
		t.Fatalf("selected bytes failed to replay: %v", err)
	}
	if err := os.WriteFile(file, append(raw, ' '), 0o600); err != nil {
		t.Fatal(err)
	}
	if _, err := readCapture(file, digest); err == nil {
		t.Fatal("a different file matched the selected digest")
	}
	if err := os.WriteFile(file, bytes.Repeat([]byte{' '}, maxCaptureBytes+1), 0o600); err != nil {
		t.Fatal(err)
	}
	if _, err := readCapture(file, digest); err == nil {
		t.Fatal("oversized capture was read")
	}
}
