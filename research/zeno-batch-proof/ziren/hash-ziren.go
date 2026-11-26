//go:build ziren

package crypto

import (
	"encoding/binary"
	"unsafe"

	"github.com/ProjectZKM/Ziren/crates/go-runtime/zkvm_runtime"
)

// Zeno uses SHA3-256. The Ziren sponge syscall consumes pre-padded blocks.
func zirenSHA3(data ...[]byte) []byte {
	const rate = 136
	length := 0
	for _, part := range data {
		length += len(part)
	}
	payload := make([]byte, length-length%rate+rate)
	offset := 0
	for _, part := range data {
		offset += copy(payload[offset:], part)
	}
	payload[length] = 0x06
	payload[len(payload)-1] |= 0x80

	words := make([]uint32, len(payload)/rate*36)
	for block := 0; block < len(payload)/rate; block++ {
		for word := 0; word < rate/4; word++ {
			start := block*rate + word*4
			words[block*36+word] = binary.LittleEndian.Uint32(payload[start : start+4])
		}
	}
	var result [17]uint32
	result[16] = uint32(len(words))
	zkvm_runtime.SyscallKeccakSponge(unsafe.Pointer(&words[0]), unsafe.Pointer(&result[0]))

	hash := make([]byte, 32)
	for i, word := range result[:8] {
		binary.LittleEndian.PutUint32(hash[i*4:], word)
	}
	return hash
}
