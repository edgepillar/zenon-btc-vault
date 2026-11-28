use std::{env, fs, path::Path};
use zkm_prover::{ZKMCoreProof, ZKMCoreProofData};
use zkm_sdk::{
    provers::ProofOpts, CpuProver, HashableKey, ProverClient, ZKMProof, ZKMProofWithPublicValues,
    ZKMProver, ZKMPublicValues, ZKMStdin, ZKM_CIRCUIT_VERSION,
};

const ELF: &[u8] = include_bytes!(concat!(env!("OUT_DIR"), "/vault-guest"));
const CORE_SIZE: u64 = 150_029_239;
const CORE_SHARDS: usize = 171;
const LIVE_CORE_LIMIT: u64 = 512 * 1024 * 1024;
const COMPRESSED_LIMIT: u64 = 16 * 1024 * 1024;

fn load(
    file: &str,
    digest: &[u8],
    size_bound: std::ops::RangeInclusive<u64>,
) -> ZKMProofWithPublicValues {
    let size = fs::metadata(file).expect("stat proof").len();
    assert!(size_bound.contains(&size), "proof size differs");
    let proof = ZKMProofWithPublicValues::load(file).expect("load proof file");
    assert_eq!(proof.zkm_version, ZKM_CIRCUIT_VERSION, "proof version differs");
    assert_eq!(proof.public_values.as_slice(), digest, "proof journal differs");
    proof
}

fn compress(client: &ProverClient, source: &str, output: &str, digest: &[u8], expected: Option<(u64, usize)>) {
    assert!(!Path::new(output).exists(), "proof output already exists");
    let sizes = expected.map_or(1..=LIVE_CORE_LIMIT, |(size, _)| size..=size);
    let bundle = load(source, digest, sizes);
    let (_, vk) = client.setup(ELF);
    client.verify(&bundle, &vk).expect("verify the retained core proof");
    let ZKMProof::Core(shards) = bundle.proof else {
        panic!("expected a core proof");
    };
    if let Some((_, count)) = expected {
        assert_eq!(shards.len(), count, "core shard count differs");
    }
    assert!(!shards.is_empty(), "expected a full Core proof");
    println!(
        "SETTLEMENT_CORE_VERIFIED {} {} {}",
        shards.len(),
        hex::encode(digest),
        vk.bytes32()
    );

    let public_values = bundle.public_values;
    let core = ZKMCoreProof {
        proof: ZKMCoreProofData(shards),
        stdin: ZKMStdin::new(),
        public_values: public_values.clone(),
        // The saved SDK envelope omits the prover's cycle counter.
        cycles: 0,
    };
    let mut opts = ProofOpts::default().zkm_prover_opts;
    opts.recursion_opts.trace_gen_workers = 1;
    opts.recursion_opts.records_and_traces_channel_capacity = 0;
    opts.recursion_opts.checkpoints_channel_capacity = 1;
    opts.recursion_opts.shard_batch_size = 1;
    println!("SETTLEMENT_RECURSION_STARTED");
    let reduced = client
        .prover
        .zkm_prover()
        .compress(&vk, core, Vec::new(), opts)
        .expect("aggregate all retained core shards");
    let result = ZKMProofWithPublicValues {
        proof: ZKMProof::Compressed(Box::new(reduced)),
        public_values,
        zkm_version: bundle.zkm_version,
    };
    result.save(output).expect("save compressed proof");
}

fn verify_compressed(client: &ProverClient, file: &str, digest: &[u8]) {
    let (_, vk) = client.setup(ELF);
    let mut bundle = load(file, digest, 1..=COMPRESSED_LIMIT);
    assert!(matches!(&bundle.proof, ZKMProof::Compressed(_)), "expected a compressed proof");
    client.verify(&bundle, &vk).expect("verify the serialized compressed proof");
    println!(
        "SETTLEMENT_COMPRESSED_VERIFIED {} {} {} {}",
        fs::metadata(file).expect("stat compressed proof").len(),
        hex::encode(digest),
        bundle.zkm_version,
        vk.bytes32()
    );
    let mut changed = digest.to_vec();
    changed[0] ^= 1;
    bundle.public_values = ZKMPublicValues::from(&changed);
    assert!(client.verify(&bundle, &vk).is_err(), "altered journal was accepted");
    println!("SETTLEMENT_COMPRESSED_REJECTED altered-journal");
}

fn main() {
    zkm_sdk::setup_logger();
    let args: Vec<_> = env::args().skip(1).collect();
    let client = ProverClient {
        prover: Box::new(CpuProver::from_prover(ZKMProver::uninitialized_with(Some(true)))),
    };
    match args.as_slice() {
        [mode, digest, source, output] if mode == "compress" || mode == "compress-live" => {
            let digest = hex::decode(digest).expect("witness SHA-256");
            assert_eq!(digest.len(), 32, "invalid witness digest length");
            let expected = (mode == "compress").then_some((CORE_SIZE, CORE_SHARDS));
            compress(&client, source, output, &digest, expected);
        }
        [mode, digest, file] if mode == "verify-compressed" => {
            let digest = hex::decode(digest).expect("witness SHA-256");
            assert_eq!(digest.len(), 32, "invalid witness digest length");
            verify_compressed(&client, file, &digest);
        }
        _ => panic!("usage: compress|compress-live JOURNAL CORE COMPRESSED | verify-compressed JOURNAL COMPRESSED"),
    }
}
