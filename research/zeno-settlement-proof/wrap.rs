use std::{env, fs, io::BufReader, path::Path};
use zkm_sdk::{
    provers::ProofOpts, CpuProver, HashableKey, ProverClient, ZKMProof, ZKMProofWithPublicValues,
    ZKMProver, ZKMPublicValues, ZKM_CIRCUIT_VERSION,
};

const ELF: &[u8] = include_bytes!(concat!(env!("OUT_DIR"), "/vault-guest"));
const VK_SHA256: &str = "b3e14fe98042c3ff6f219172965bddd8dda586c556adac9b4acbd285e13d3cdb";

fn open_new(path: &str) -> fs::File {
    fs::OpenOptions::new().write(true).create_new(true).open(path).expect("create proof output")
}

fn shrink_and_wrap(client: &ProverClient, journal: &[u8], source: &str, dest: &str) {
    let length = fs::metadata(source).expect("stat recursive proof").len();
    assert!((1..=16 * 1024 * 1024).contains(&length), "unexpected compressed proof size");
    let bundle = ZKMProofWithPublicValues::load(source).expect("load recursive proof");
    assert_eq!(bundle.zkm_version, ZKM_CIRCUIT_VERSION, "unexpected proof version");
    assert_eq!(bundle.public_values.as_slice(), journal, "unexpected proof journal");
    let (_, vk) = client.setup(ELF);
    client.verify(&bundle, &vk).expect("verify recursive proof");
    let ZKMProof::Compressed(proof) = bundle.proof else {
        panic!("expected a recursive proof");
    };
    let mut opts = ProofOpts::default().zkm_prover_opts;
    opts.recursion_opts.shard_batch_size = 1;
    let prover = client.prover.zkm_prover();
    println!("SETTLEMENT_SHRINK_STARTED");
    let shrink = prover.shrink(*proof, opts).expect("shrink recursive proof");
    prover.verify_shrink(&shrink, &vk).expect("verify shrink proof");
    println!("SETTLEMENT_WRAP_STARTED");
    let outer = prover.wrap_bn254(shrink, opts).expect("wrap shrink proof over BN254");
    prover.verify_wrap_bn254(&outer, &vk).expect("verify the outer STARK");
    bincode::serialize_into(open_new(dest), &outer).expect("save outer proof");
    println!("SETTLEMENT_OUTER_VERIFIED {}", vk.bytes32());
}

fn groth16(client: &ProverClient, journal: &[u8], source: &str, circuit: &Path, dest: &str) {
    let length = fs::metadata(source).expect("stat outer proof").len();
    assert!((1..=256 * 1024 * 1024).contains(&length), "unexpected outer proof size");
    let outer = bincode::deserialize_from(BufReader::new(fs::File::open(source).expect("open outer proof")))
        .expect("decode outer proof");
    let (_, vk) = client.setup(ELF);
    let prover = client.prover.zkm_prover();
    println!("SETTLEMENT_GROTH16_STARTED");
    let proof = prover.wrap_groth16_bn254(outer, circuit);
    let public_values = ZKMPublicValues::from(&journal.to_vec());
    prover.verify_groth16_bn254(&proof, &vk, &public_values, circuit)
        .expect("verify Groth16 proof against the guest key and original journal");
    assert_eq!(hex::encode(proof.groth16_vkey_hash), VK_SHA256, "unexpected circuit verifier key");
    let proof_bytes = hex::decode(&proof.encoded_proof).expect("decode Solidity points");
    assert_eq!(proof_bytes.len(), 256, "unexpected Groth16 proof encoding");

    serde_json::to_writer(open_new(dest), &serde_json::json!({
        "schema": "zeno-settlement-r4-groth16-v1",
        "version": ZKM_CIRCUIT_VERSION,
        "guestKey": vk.bytes32(),
        "journal": hex::encode(journal),
        "verificationKeySha256": VK_SHA256,
        "publicInputs": proof.public_inputs,
        "proof": proof.encoded_proof,
    })).expect("save proof fixture");
    println!("SETTLEMENT_GROTH16_VERIFIED {} {}", proof_bytes.len(), vk.bytes32());
}

fn main() {
    zkm_sdk::setup_logger();
    let args: Vec<_> = env::args().skip(1).collect();
    let client = ProverClient {
        prover: Box::new(CpuProver::from_prover(ZKMProver::uninitialized_with(Some(true)))),
    };
    match args.as_slice() {
        [mode] if mode == "guest-key" => {
            let (_, vk) = client.setup(ELF);
            println!("SETTLEMENT_GUEST_KEY {}", vk.bytes32());
        }
        [mode, digest, compressed, outer] if mode == "outer" => {
            let journal = hex::decode(digest).expect("witness SHA-256");
            assert_eq!(journal.len(), 32, "invalid witness digest length");
            shrink_and_wrap(&client, &journal, compressed, outer);
        }
        [mode, digest, outer, circuit, fixture] if mode == "groth16" => {
            let journal = hex::decode(digest).expect("witness SHA-256");
            assert_eq!(journal.len(), 32, "invalid witness digest length");
            groth16(&client, &journal, outer, Path::new(circuit), fixture);
        }
        _ => panic!("usage: guest-key | outer SHA256 COMPRESSED OUTER | groth16 SHA256 OUTER CIRCUIT FIXTURE"),
    }
}
