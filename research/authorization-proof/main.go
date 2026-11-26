package main

import (
	"bytes"
	"crypto/sha256"
	"crypto/sha3"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"

	"github.com/consensys/gnark-crypto/ecc"
	"github.com/consensys/gnark/backend/groth16"
	bn254 "github.com/consensys/gnark/backend/groth16/bn254"
	"github.com/consensys/gnark/backend/witness"
	"github.com/consensys/gnark/frontend"
	"github.com/consensys/gnark/frontend/cs/r1cs"
)

const bundleLimit = 16_384

type statementRecord struct {
	AuthorizationSHA256 string `json:"authorizationSha256"`
	ExecutionSHA256     string `json:"executionSha256"`
	Caller              string `json:"caller"`
	CanonicalInput      string `json:"canonicalInput"`
	BitcoinTarget       string `json:"bitcoinTarget"`
	Commitment          string `json:"commitment"`
	StateDiffHash       string `json:"stateDiffHash"`
}

type proofCase struct {
	context, caller, send, target []byte
	commitment, inputHash, diffHash [32]byte
}

type proofBundle struct {
	Schema       int    `json:"schema"`
	Statement    string `json:"statement"`
	VerifyingKey []byte `json:"verifyingKey"`
	Proof        []byte `json:"proof"`
}

func readBounded(name string) ([]byte, error) {
	file, err := os.Open(name)
	if err != nil {
		return nil, errors.New("could not open proof or statement file")
	}
	defer file.Close()
	raw, err := io.ReadAll(io.LimitReader(file, bundleLimit+1))
	if err != nil || len(raw) > bundleLimit {
		return nil, errors.New("could not read proof or statement within its byte limit")
	}
	return raw, nil
}

func decodeHex(field string, value string, want int) ([]byte, error) {
	out, err := hex.DecodeString(value)
	if err != nil || len(out) != want || hex.EncodeToString(out) != value {
		return nil, fmt.Errorf("invalid %s encoding", field)
	}
	return out, nil
}

func loadCase(name string) (proofCase, error) {
	raw, err := readBounded(name)
	if err != nil {
		return proofCase{}, err
	}
	var record statementRecord
	if err := json.Unmarshal(raw, &record); err != nil {
		return proofCase{}, errors.New("invalid replay statement")
	}
	auth, err := decodeHex("authorization capture SHA-256", record.AuthorizationSHA256, 32)
	if err != nil {
		return proofCase{}, err
	}
	exec, err := decodeHex("execution capture SHA-256", record.ExecutionSHA256, 32)
	if err != nil {
		return proofCase{}, err
	}
	context := sha256.Sum256(bytes.Join([][]byte{auth, exec}, nil))
	caller, err := decodeHex("caller", record.Caller, 20)
	if err != nil {
		return proofCase{}, err
	}
	target, err := decodeHex("Bitcoin target", record.BitcoinTarget, bitcoinTargetLen)
	if err != nil {
		return proofCase{}, err
	}
	input, err := decodeHex("CallL2 input", record.CanonicalInput, 177)
	if err != nil {
		return proofCase{}, err
	}
	commitment := sha3.Sum256(bytes.Join([][]byte{[]byte(authorizationTag), source, contractID, target}, nil))
	if hex.EncodeToString(commitment[:]) != record.Commitment {
		return proofCase{}, errors.New("Bitcoin target does not reproduce the recorded state commitment")
	}
	send := input[1:33]
	wantInput := bytes.Join([][]byte{{0}, send, contractID, callHeader, commitment[:]}, nil)
	if !bytes.Equal(input, wantInput) || !bytes.Equal(caller, custodian) {
		return proofCase{}, errors.New("replayed authorization call falls outside the circuit profile")
	}
	diffHash, err := decodeHex("replayed state-diff hash", record.StateDiffHash, 32)
	if err != nil {
		return proofCase{}, err
	}
	return proofCase{
		context: context[:], caller: caller, send: send, target: target,
		commitment: commitment, inputHash: sha3.Sum256(input), diffHash: [32]byte(diffHash),
	}, nil
}

func statementFor(c proofCase) [32]byte {
	return sha3.Sum256(bytes.Join([][]byte{
		[]byte(statementTag), c.context, contractID, c.caller, c.send,
		c.commitment[:], c.inputHash[:], c.diffHash[:],
	}, nil))
}

func assign(dst []frontend.Variable, raw []byte) {
	for i, v := range raw {
		dst[i] = v
	}
}

func witnessFor(c proofCase) (witness.Witness, error) {
	statement := statementFor(c)
	var a authorizationCircuit
	assign(a.Statement[:], statement[:])
	assign(a.Context[:], c.context)
	assign(a.Caller[:], c.caller)
	assign(a.SendHash[:], c.send)
	assign(a.Target[:], c.target)
	return frontend.NewWitness(&a, ecc.BN254.ScalarField())
}

func publicFor(c proofCase) (witness.Witness, error) {
	statement := statementFor(c)
	var a authorizationCircuit
	assign(a.Statement[:], statement[:])
	return frontend.NewWitness(&a, ecc.BN254.ScalarField(), frontend.PublicOnly())
}

func createProof(c proofCase) (proofBundle, error) {
	field := ecc.BN254.ScalarField()
	cs, err := frontend.Compile(field, r1cs.NewBuilder, &authorizationCircuit{})
	if err != nil {
		return proofBundle{}, err
	}
	fmt.Printf("AUTHORIZATION_R1CS_CONSTRAINTS %d\n", cs.GetNbConstraints())
	w, err := witnessFor(c)
	if err != nil {
		return proofBundle{}, err
	}
	if err := cs.IsSolved(w); err != nil {
		return proofBundle{}, fmt.Errorf("captured authorization did not solve the circuit: %w", err)
	}
	wrongCaller := c
	wrongCaller.caller = bytes.Clone(c.caller)
	wrongCaller.caller[19] ^= 1
	wrongTarget := c
	wrongTarget.target = bytes.Clone(c.target)
	wrongTarget.target[99] ^= 1
	for _, bad := range []proofCase{wrongCaller, wrongTarget} {
		w, err := witnessFor(bad)
		if err != nil {
			return proofBundle{}, err
		}
		if err := cs.IsSolved(w); err == nil {
			return proofBundle{}, errors.New("changed caller or recipient unexpectedly solved the circuit")
		}
	}
	fmt.Println("PASS: circuit refused the altered caller and recipient witnesses")

	// This one-run setup supplies an experimental key for the pinned demo.
	pk, vk, err := groth16.Setup(cs)
	if err != nil {
		return proofBundle{}, err
	}
	p, err := groth16.Prove(cs, pk, w)
	if err != nil {
		return proofBundle{}, err
	}
	if len(p.(*bn254.Proof).Commitments) != 0 {
		return proofBundle{}, errors.New("the proof used an additional commitment")
	}
	var keyData, proofData bytes.Buffer
	if _, err := vk.WriteTo(&keyData); err != nil {
		return proofBundle{}, err
	}
	if _, err := p.WriteTo(&proofData); err != nil {
		return proofBundle{}, err
	}
	statement := statementFor(c)
	return proofBundle{1, hex.EncodeToString(statement[:]), keyData.Bytes(), proofData.Bytes()}, nil
}

func verifyBundle(c proofCase, bundle proofBundle) error {
	statement := statementFor(c)
	if bundle.Schema != 1 || bundle.Statement != hex.EncodeToString(statement[:]) {
		return errors.New("proof bundle does not match the replay-selected public statement")
	}
	var key bn254.VerifyingKey
	keyReader := bytes.NewReader(bundle.VerifyingKey)
	if _, err := key.ReadFrom(keyReader); err != nil || keyReader.Len() != 0 || key.NbPublicWitness() != 32 {
		return errors.New("invalid verifying key encoding")
	}
	var proof bn254.Proof
	proofReader := bytes.NewReader(bundle.Proof)
	if _, err := proof.ReadFrom(proofReader); err != nil || proofReader.Len() != 0 || len(proof.Commitments) != 0 {
		return errors.New("invalid or extended Groth16 proof encoding")
	}
	public, err := publicFor(c)
	if err != nil {
		return err
	}
	if err := groth16.Verify(&proof, &key, public); err != nil {
		return fmt.Errorf("Groth16 proof refused for the selected statement: %w", err)
	}
	wrongContext := c
	wrongContext.context = bytes.Clone(c.context)
	wrongContext.context[0] ^= 1
	wrongDiff := c
	wrongDiff.diffHash[0] ^= 1
	for _, bad := range []proofCase{wrongContext, wrongDiff} {
		public, err := publicFor(bad)
		if err != nil {
			return err
		}
		if err := groth16.Verify(&proof, &key, public); err == nil {
			return errors.New("the proof verified under a changed capture or state diff")
		}
	}
	fmt.Printf("PASS: verified the authorization statement with a %d-byte BN254 Groth16 proof and a %d-byte verifying key\n",
		len(bundle.Proof), len(bundle.VerifyingKey))
	fmt.Println("PASS: the proof refused a changed capture context and a changed state diff")
	return nil
}

func run(args []string) error {
	if len(args) < 3 || (args[0] != "prove" && args[0] != "verify") {
		return errors.New("use prove <statement> <output> or verify <statement> <bundle> <bundle-sha256>")
	}
	c, err := loadCase(args[1])
	if err != nil {
		return err
	}
	switch {
	case args[0] == "prove" && len(args) == 3:
		bundle, err := createProof(c)
		if err != nil {
			return err
		}
		if err := verifyBundle(c, bundle); err != nil {
			return err
		}
		raw, err := json.Marshal(bundle)
		if err != nil || len(raw)+1 > bundleLimit {
			return errors.New("proof bundle exceeds its byte limit or could not be encoded")
		}
		file, err := os.OpenFile(args[2], os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0o600)
		if err != nil {
			return err
		}
		if _, err := file.Write(append(raw, '\n')); err != nil {
			file.Close()
			return err
		}
		return file.Close()
	case args[0] == "verify" && len(args) == 4:
		raw, err := readBounded(args[2])
		if err != nil {
			return err
		}
		if fmt.Sprintf("%x", sha256.Sum256(raw)) != args[3] {
			return errors.New("proof bundle differs from independent SHA-256 selection")
		}
		var bundle proofBundle
		if err := json.Unmarshal(raw, &bundle); err != nil {
			return errors.New("invalid proof bundle")
		}
		return verifyBundle(c, bundle)
	default:
		return errors.New("incorrect proof command arguments")
	}
}

func main() {
	if err := run(os.Args[1:]); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
