use std::{env, fs, path::PathBuf, process::Command};

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
    let readlink_source = PathBuf::from(goroot.trim()).join("src/syscall/zsyscall_linux_mipsle.go");
    let readlink_overlay = output.join("zsyscall_linux_mipsle.go");
    let syscall = fs::read_to_string(&readlink_source).expect("read Go syscall source");
    let entry = "func readlinkat(dirfd int, path string, buf []byte) (n int, err error) {";
    assert_eq!(syscall.matches(entry).count(), 1, "unexpected readlinkat source");
    // The guest has no procfs. Preserve the Linux error for this optional probe.
    let without_procfs = format!(
        "{entry}\n\tif path == \"/proc/self/exe\" {{\n\t\treturn 0, ENOENT\n\t}}"
    );
    fs::write(&readlink_overlay, syscall.replace(entry, &without_procfs))
        .expect("write guest readlinkat overlay");
    let mut replacements: serde_json::Value =
        serde_json::from_slice(&fs::read(&overlay).expect("read Ziren overlay"))
            .expect("parse Ziren overlay");
    replacements["Replace"][readlink_source.to_str().expect("UTF-8 Go path")] =
        serde_json::json!(readlink_overlay);
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
