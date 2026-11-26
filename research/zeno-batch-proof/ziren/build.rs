use std::{env, fs, path::PathBuf, process::Command};


// The batch guest uses in-memory state, modules and bundles. Its Linux
// environment has no filesystem entries.
fn refuse_file_path(source: &str, signature: &str, result: &str) -> String {
    assert_eq!(source.matches(signature).count(), 1, "unexpected syscall source");
    let (before, body) = source.split_once(signature).unwrap();
    let (_, after) = body.split_once("\n}\n").expect("end of generated syscall");
    format!("{before}{signature}\n\treturn {result}\n}}\n{after}")
}

fn main() {
    let guest = PathBuf::from(env::var_os("ZENO_GUEST_DIR").expect("missing guest directory"));
    let output = PathBuf::from(env::var_os("OUT_DIR").expect("missing output directory"));
    let go_env = [
        ("GOOS", "linux"),
        ("GOARCH", "mipsle"),
        ("GOMIPS", "softfloat"),
        ("CGO_ENABLED", "0"),
        ("GOTOOLCHAIN", "local"),
        ("GOWORK", "off"),
    ];
    let overlay = zkm_build::generate_go_overlay_for(&output, &guest, &go_env)
        .expect("missing Ziren Go runtime overlay");
    let goroot = Command::new("go")
        .args(["env", "GOROOT"])
        .current_dir(&guest)
        .envs(go_env)
        .output()
        .expect("locate pinned Go toolchain");
    assert!(goroot.status.success(), "go env GOROOT failed");
    let goroot = String::from_utf8(goroot.stdout).expect("GOROOT must be UTF-8");
    let syscall_source = PathBuf::from(goroot.trim()).join("src/syscall/zsyscall_linux_mipsle.go");
    let syscall_overlay = output.join("zsyscall_linux_mipsle.go");
    let syscall = fs::read_to_string(&syscall_source).expect("read Go syscall source");
    let syscall = refuse_file_path(
        &syscall,
        "func openat(dirfd int, path string, flags int, mode uint32) (fd int, err error) {",
        "-1, ENOENT",
    );
    let syscall = refuse_file_path(
        &syscall,
        "func readlinkat(dirfd int, path string, buf []byte) (n int, err error) {",
        "0, ENOENT",
    );
    fs::write(&syscall_overlay, syscall).expect("write guest filesystem overlay");
    let mut replacements: serde_json::Value =
        serde_json::from_slice(&fs::read(&overlay).expect("read Ziren overlay"))
            .expect("parse Ziren overlay");
    replacements["Replace"][syscall_source.to_str().expect("UTF-8 Go path")] =
        serde_json::json!(syscall_overlay);
    fs::write(&overlay, serde_json::to_vec(&replacements).expect("encode Go overlay"))
        .expect("write complete guest runtime overlay");
    let status = Command::new("go")
        // Keep the symbol table for Ziren's Go runtime patches.
        .args([
            "build",
            "-mod=mod",
            "-tags=ziren",
            "-trimpath",
            "-buildvcs=false",
            "-ldflags=-w -buildid=",
        ])
        .arg("-overlay")
        .arg(overlay)
        .arg("-o")
        .arg(output.join("vault-guest"))
        .arg(".")
        .current_dir(&guest)
        .envs(go_env)
        .status()
        .expect("launch MIPS Go build");
    assert!(status.success(), "MIPS Go build failed");
    println!("cargo:rerun-if-env-changed=ZENO_GUEST_DIR");
    println!("cargo:rerun-if-changed={}", guest.display());
}
