// The hosted workflow builds this probe inside a disposable checkout of the pinned SPV source.
package main

import (
	"bytes"
	"context"
	"crypto/ed25519"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"time"

	"github.com/0x3639/zenon-spv/internal/chain"
	"github.com/0x3639/zenon-spv/internal/fetch"
	"github.com/0x3639/zenon-spv/internal/verify"
)

const maxCaptureBytes = 32_768

var endpoints = [...]string{
	"https://my.hc1node.com:35997",
	"https://node.zenonhub.io:35997",
}

type observation struct {
	Endpoint string         `json:"endpoint"`
	Genesis  *chain.Header  `json:"genesis,omitempty"`
	Frontier *chain.Header  `json:"frontier,omitempty"`
	Sample   []chain.Header `json:"sample,omitempty"`
	Failure  string         `json:"failure,omitempty"`
}

type capture struct {
	Schema     int           `json:"schema"`
	ObservedAt int64         `json:"observedAt"`
	Peers      []observation `json:"peers"`
}

type assessment struct {
	Observation      string   `json:"observation"`
	SampleHeight     uint64   `json:"sampleHeight,omitempty"`
	Versions         []uint64 `json:"sampleVersions,omitempty"`
	StateRoot        string   `json:"stateRoot"`
	V3Activation     string   `json:"v3Activation"`
	ConsensusFinality string  `json:"consensusFinality"`
	L2Execution      string   `json:"l2Execution"`
	BitcoinRelease   string   `json:"bitcoinRelease"`
}

func validGenesis(h *chain.Header, anchor verify.GenesisTrustRoot) bool {
	return h != nil && h.Height == anchor.Height && h.Version == 1 &&
		h.ChainIdentifier == anchor.ChainID && h.PreviousHash.IsZero() &&
		h.HeaderHash == anchor.HeaderHash && h.ComputeHash() == h.HeaderHash
}

func validSignedHeader(h *chain.Header, chainID uint64) bool {
	return h != nil && h.ChainIdentifier == chainID && h.Height > 1 &&
		chain.ValidateHeaderVersion(h.Version) == nil && h.ComputeHash() == h.HeaderHash &&
		len(h.PublicKey) == ed25519.PublicKeySize && ed25519.Verify(h.PublicKey, h.HeaderHash[:], h.Signature)
}

func sameHeader(a, b chain.Header) bool {
	return a.HeaderHash == b.HeaderHash && bytes.Equal(a.PublicKey, b.PublicKey) && bytes.Equal(a.Signature, b.Signature)
}

func sampleStart(data capture, anchor verify.GenesisTrustRoot) (uint64, string) {
	if data.Schema != 1 || data.ObservedAt <= 0 || len(data.Peers) != len(endpoints) {
		return 0, "INVALID_CAPTURE"
	}
	for i, p := range data.Peers {
		if p.Endpoint != endpoints[i] {
			return 0, "INVALID_CAPTURE"
		}
		if p.Failure != "" {
			return 0, "INCOMPLETE"
		}
		if !validGenesis(p.Genesis, anchor) {
			return 0, "GENESIS_MISMATCH"
		}
		if !validSignedHeader(p.Frontier, anchor.ChainID) || p.Frontier.Height <= 2 {
			return 0, "INVALID_FRONTIER"
		}
	}
	return min(data.Peers[0].Frontier.Height, data.Peers[1].Frontier.Height) - 1, ""
}

func assess(data capture, anchor verify.GenesisTrustRoot) assessment {
	result := assessment{
		StateRoot: "NOT_ESTABLISHED", V3Activation: "NOT_ESTABLISHED",
		ConsensusFinality: "NOT_ESTABLISHED", L2Execution: "NOT_ESTABLISHED", BitcoinRelease: "NO_GO",
	}
	start, failure := sampleStart(data, anchor)
	if failure != "" {
		result.Observation = failure
		return result
	}
	a, b := data.Peers[0], data.Peers[1]
	for _, p := range data.Peers {
		if len(p.Sample) != 2 || p.Sample[0].Height != start || p.Sample[1].Height != start+1 {
			result.Observation = "INVALID_SAMPLE_HEIGHT"
			return result
		}
		first, next := p.Sample[0], p.Sample[1]
		if !validSignedHeader(&first, anchor.ChainID) || !validSignedHeader(&next, anchor.ChainID) ||
			next.PreviousHash != first.HeaderHash || next.TimestampUnix <= first.TimestampUnix {
			result.Observation = "INVALID_SAMPLE"
			return result
		}
		if next.Height == p.Frontier.Height && !sameHeader(next, *p.Frontier) {
			result.Observation = "SAMPLE_FRONTIER_MISMATCH"
			return result
		}
	}
	if !sameHeader(a.Sample[0], b.Sample[0]) || !sameHeader(a.Sample[1], b.Sample[1]) {
		result.Observation = "PEERS_DISAGREE"
		return result
	}
	result.Observation = "LINKED_HEADERS_AGREE"
	result.SampleHeight = start
	result.Versions = []uint64{a.Sample[0].Version, a.Sample[1].Version}
	result.StateRoot = "ABSENT_FROM_SAMPLED_LAYOUTS"
	return result
}

func fetchFailure(stage string, err error) string {
	switch {
	case errors.Is(err, chain.ErrUnsupportedHeaderVersion):
		return stage + "_UNSUPPORTED_LAYOUT"
	case errors.Is(err, fetch.ErrHashMismatch):
		return stage + "_HASH_MISMATCH"
	case errors.Is(err, context.DeadlineExceeded):
		return stage + "_TIMEOUT"
	default:
		return stage + "_RPC_FAILURE"
	}
}

func collect(ctx context.Context, anchor verify.GenesisTrustRoot) capture {
	data := capture{Schema: 1, Peers: make([]observation, len(endpoints))}
	for i, endpoint := range endpoints {
		p := &data.Peers[i]
		p.Endpoint = endpoint
		client := fetch.NewClient(endpoint)
		genesis, err := client.FetchByHeight(ctx, anchor.Height, 1)
		if err != nil {
			p.Failure = fetchFailure("GENESIS", err)
			continue
		}
		p.Genesis = &genesis[0]
		frontier, err := client.FetchFrontier(ctx)
		if err != nil {
			p.Failure = fetchFailure("FRONTIER", err)
			continue
		}
		p.Frontier = &frontier
	}
	// Apply the identity and frontier checks before using any peer's height for a new query.
	data.ObservedAt = time.Now().Unix()
	start, failure := sampleStart(data, anchor)
	if failure != "" {
		return data
	}
	for i := range data.Peers {
		p := &data.Peers[i]
		sample, err := fetch.NewClient(p.Endpoint).FetchByHeight(ctx, start, 2)
		if err != nil {
			p.Failure = fetchFailure("SAMPLE", err)
			continue
		}
		p.Sample = sample
	}
	data.ObservedAt = time.Now().Unix()
	return data
}

func readCapture(name, expected string) (capture, error) {
	file, err := os.Open(name)
	if err != nil {
		return capture{}, errors.New("capture file could not be opened")
	}
	defer file.Close()
	raw, err := io.ReadAll(io.LimitReader(file, maxCaptureBytes+1))
	if err != nil || len(raw) > maxCaptureBytes {
		return capture{}, errors.New("capture exceeds its read bound or could not be read")
	}
	digest := sha256.Sum256(raw)
	if hex.EncodeToString(digest[:]) != expected {
		return capture{}, errors.New("capture differs from the selected SHA-256")
	}
	var data capture
	if err := json.Unmarshal(raw, &data); err != nil {
		return capture{}, errors.New("invalid capture format")
	}
	return data, nil
}

func run(args []string, out io.Writer) error {
	anchor, err := verify.MainnetGenesis()
	if err != nil {
		return errors.New("mainnet genesis pin is unavailable")
	}
	var data capture
	switch {
	case len(args) == 1 && args[0] == "capture":
		ctx, cancel := context.WithTimeout(context.Background(), 60*time.Second)
		defer cancel()
		data = collect(ctx, anchor)
		raw, err := json.Marshal(data)
		if err != nil || len(raw)+1 > maxCaptureBytes {
			return errors.New("capture exceeds its output bound or could not be encoded")
		}
		raw = append(raw, '\n')
		if _, err := fmt.Fprintf(out, "MAINNET_CAPTURE_SHA256 %x\nMAINNET_CAPTURE_JSON %s", sha256.Sum256(raw), raw); err != nil {
			return err
		}
	case len(args) == 3 && args[0] == "replay":
		data, err = readCapture(args[1], args[2])
		if err != nil {
			return err
		}
	default:
		return errors.New("use capture or replay <file> <sha256>")
	}
	result := assess(data, anchor)
	raw, err := json.Marshal(result)
	if err != nil {
		return err
	}
	if _, err := fmt.Fprintf(out, "MAINNET_ASSESSMENT %s\n", raw); err != nil {
		return err
	}
	if result.Observation != "LINKED_HEADERS_AGREE" {
		return errors.New("mainnet observation incomplete or refused")
	}
	return nil
}

func main() {
	if err := run(os.Args[1:], os.Stdout); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
