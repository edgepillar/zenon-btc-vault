use serde_json::Value;
use std::{env, fs, io::Read, path::Path};
use zkm_sdk::{
    CpuProver, ProverClient, ZKMProof, ZKMProofWithPublicValues, ZKMProver, ZKMPublicValues,
    ZKMStdin,
};

const ELF: &[u8] = include_bytes!(concat!(env!("OUT_DIR"), "/vault-guest"));
const WITNESS_LIMIT: u64 = 49_152;
const PROOF_LIMIT: u64 = 268_435_456;
const MAX_CYCLES: u64 = 300_000_000;

fn read_witness(path: &str) -> Vec<u8> {
    let mut bytes = Vec::new();
    fs::File::open(path)
        .expect("open recorded batch witness")
        .take(WITNESS_LIMIT + 1)
        .read_to_end(&mut bytes)
        .expect("read recorded batch witness");
    assert!(
        !bytes.is_empty() && bytes.len() as u64 <= WITNESS_LIMIT,
        "recorded witness size is out of bounds"
    );
    bytes
}

fn execute(client: &ProverClient, raw: &[u8], digest: &[u8]) {
    let (journal, report) = client
        .execute(ELF, &stdin(raw))
        .max_cycles(MAX_CYCLES)
        .run()
        .expect("positive guest execution failed");
    assert_eq!(journal.as_slice(), digest, "guest journal differs");
    println!(
        "BATCH_GUEST_EXECUTED {} {}",
        report.total_instruction_count(),
        hex::encode(journal.as_slice())
    );
}

fn stdin(raw: &[u8]) -> ZKMStdin {
    let mut input = ZKMStdin::new();
    input.write(&raw);
    input
}

fn reject(client: &ProverClient, name: &str, witness: &Value) {
    let raw = serde_json::to_vec(witness).expect("encode control witness");
    assert!(
        client
            .execute(ELF, &stdin(&raw))
            .max_cycles(MAX_CYCLES)
            .run()
            .is_err(),
        "{name} unexpectedly executed successfully"
    );
    println!("BATCH_GUEST_REJECTED {name}");
}

fn controls(client: &ProverClient, raw: &[u8]) {
    let witness: Value = serde_json::from_slice(raw).expect("decode control witness");

    let mut changed = witness.clone();
    changed["inputs"].as_array_mut().unwrap().pop().unwrap();
    reject(client, "omitted-input", &changed);

    let mut changed = witness.clone();
    changed["inputs"].as_array_mut().unwrap().swap(0, 1);
    reject(client, "reordered-inputs", &changed);

    let mut changed = witness.clone();
    changed["inputs"][1]["Ctx"]["L1Sender"] = witness["inputs"][0]["Ctx"]["L1Sender"].clone();
    assert_ne!(changed, witness, "sender control must change the witness");
    reject(client, "substituted-sender", &changed);

    let mut changed = witness;
    changed["leaves"].as_array_mut().unwrap().pop().unwrap();
    reject(client, "omitted-pre-state-leaf", &changed);
}

fn verify(client: &ProverClient, file: &str, digest: &[u8]) {
    let size = fs::metadata(file).expect("stat proof").len();
    assert!((1..=PROOF_LIMIT).contains(&size), "proof size is out of bounds");
    let mut proof = ZKMProofWithPublicValues::load(file).expect("read back proof");
    assert!(matches!(&proof.proof, ZKMProof::Core(_)), "unexpected proof kind");
    assert_eq!(proof.public_values.as_slice(), digest, "proof journal differs");
    let (_, vk) = client.setup(ELF);
    client.verify(&proof, &vk).expect("verify batch proof");
    println!(
        "BATCH_PROOF_VERIFIED {size} {} {}",
        hex::encode(digest),
        proof.zkm_version
    );

    let mut changed = digest.to_vec();
    changed[0] ^= 1;
    proof.public_values = ZKMPublicValues::from(&changed);
    assert!(client.verify(&proof, &vk).is_err(), "altered journal verified");
    println!("BATCH_PROOF_REJECTED altered-journal");
}

fn main() {
    zkm_sdk::utils::setup_logger();
    let args: Vec<_> = env::args().skip(1).collect();
    // Core proving does not use recursion. Build its programs only if requested.
    let client = ProverClient {
        prover: Box::new(CpuProver::from_prover(ZKMProver::uninitialized())),
    };
    match args.as_slice() {
        [mode] if mode == "smoke" => {
            let digest = hex::decode(
                "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
            )
            .unwrap();
            execute(&client, &[], &digest);
        }
        [mode, source, digest] if mode == "execute" => {
            let raw = read_witness(source);
            let digest = hex::decode(digest).expect("witness SHA-256");
            assert_eq!(digest.len(), 32, "expected a SHA-256 digest");
            execute(&client, &raw, &digest);
            controls(&client, &raw);
        }
        [mode, source, digest, file] if mode == "prove" => {
            let raw = read_witness(source);
            let digest = hex::decode(digest).expect("witness SHA-256");
            assert_eq!(digest.len(), 32, "expected a SHA-256 digest");
            assert!(!Path::new(file).exists(), "proof output already exists");
            println!("BATCH_PROVER_SETUP_STARTED");
            let (pk, _) = client.setup(ELF);
            println!("BATCH_PROVER_CORE_STARTED");
            let proof = client
                .prove(&pk, stdin(&raw))
                .core()
                .cycle_limit(MAX_CYCLES)
                .shard_batch_size(1)
                .run()
                .expect("CPU proof generation failed");
            assert_eq!(proof.public_values.as_slice(), digest, "proof journal differs");
            proof.save(file).expect("write proof");
        }
        [mode, digest, file] if mode == "verify" => {
            let digest = hex::decode(digest).expect("witness SHA-256");
            assert_eq!(digest.len(), 32, "expected a SHA-256 digest");
            verify(&client, file, &digest);
        }
        _ => panic!("usage: smoke | execute WITNESS SHA256 | prove WITNESS SHA256 PROOF | verify SHA256 PROOF"),
    }
}
