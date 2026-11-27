//go:build ziren

package main

import (
	"encoding/json"
	"fmt"

	"github.com/ProjectZKM/Ziren/crates/go-runtime/zkvm_runtime"
)

func main() {
	raw := zkvm_runtime.Read[[]byte]()
	var witness proposalWitness
	if len(raw) > maxWitnessSize || json.Unmarshal(raw, &witness) != nil {
		fmt.Println("invalid proposal witness bytes")
		zkvm_runtime.RuntimeExit(1)
		return
	}
	journal, _, err := verifyProposal(witness)
	if err != nil {
		fmt.Println(err)
		zkvm_runtime.RuntimeExit(1)
		return
	}
	// The Solidity wrapper hashes raw public bytes. Commit the digest bytes
	// directly so the node can derive the same public input independently.
	zkvm_runtime.Commit(journal[:])
}
